"""
As rotas do aluno: metodo, CSRF, propriedade, estado e desempenho.

O que separa este arquivo dos de servico: aqui tudo passa pelo HTTP, com
sessao e token de verdade. Um teste de servico prova que a regra existe; este
prova que a rota realmente a atravessa, e que nao ha caminho paralelo.

Politica de resposta verificada aqui:

    404  a coisa nao e sua, ou nao existe para voce
    403  papel errado ou CSRF ausente
    405  metodo errado numa rota de escrita
    409  e sua, mas o estado nao permite
"""

import json
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from exams.models import (
    Answer,
    AnswerOption,
    AttemptQuestion,
    AttemptStatus,
    ExamAttempt,
    QuestionType,
)
from exams.services import autosave_answer, start_attempt, submit_attempt

pytestmark = pytest.mark.django_db


def url_instrucoes(prova):
    return reverse("student:exam_instructions", kwargs={"exam_id": prova.pk})


def url_iniciar(prova):
    return reverse("student:attempt_start", kwargs={"exam_id": prova.pk})


def url_tentativa(tentativa):
    return reverse("student:attempt", kwargs={"public_id": tentativa.public_id})


def url_autosave(tentativa):
    return reverse(
        "student:attempt_autosave", kwargs={"public_id": tentativa.public_id}
    )


def url_finalizar(tentativa):
    return reverse(
        "student:attempt_submit", kwargs={"public_id": tentativa.public_id}
    )


@pytest.fixture
def aluno_logado(client, aluno_matriculado):
    client.force_login(aluno_matriculado)
    return client


@pytest.fixture
def segundo_aluno(db, outro_student, modulo):
    from courses.services import create_enrollment

    create_enrollment(student=outro_student, module=modulo)
    return outro_student


def responder_todas(tentativa):
    from exams.models import TIPOS_COM_ALTERNATIVAS

    for linha in (
        AttemptQuestion.objects.filter(attempt=tentativa)
        .select_related("question")
        .prefetch_related("options")
    ):
        if linha.question.type in TIPOS_COM_ALTERNATIVAS:
            autosave_answer(
                tentativa,
                question_token=str(linha.public_token),
                option_tokens=[str(linha.options.first().public_token)],
            )
        else:
            autosave_answer(
                tentativa, question_token=str(linha.public_token), text="Resposta."
            )


# ---------------------------------------------------------------------------
# Instrucoes: GET nunca cria tentativa
# ---------------------------------------------------------------------------


def test_instrucoes_abre_para_o_aluno_matriculado(aluno_logado, prova_aberta):
    resposta = aluno_logado.get(url_instrucoes(prova_aberta))

    assert resposta.status_code == 200
    assert prova_aberta.title in resposta.content.decode("utf-8")


def test_cem_gets_nas_instrucoes_nao_criam_tentativa(aluno_logado, prova_aberta):
    """
    A tela que o aluno mais recarrega antes de comecar.

    Se um GET criasse tentativa, cada F5 gastaria uma das chances dele — e
    esta e exatamente a tela em que ele recarrega por ansiedade, com dois
    aparelhos abertos.
    """
    for _ in range(100):
        assert aluno_logado.get(url_instrucoes(prova_aberta)).status_code == 200

    assert ExamAttempt.objects.count() == 0


def test_instrucoes_mostram_o_que_o_aluno_precisa_saber(aluno_logado, prova_aberta):
    corpo = aluno_logado.get(url_instrucoes(prova_aberta)).content.decode("utf-8")

    assert "60" in corpo
    assert "Duracao" in corpo
    assert "Nota minima" in corpo
    assert "Tentativas" in corpo
    assert "Senha da prova" in corpo


def test_instrucoes_de_prova_de_outro_modulo_dao_404(
    client, outro_student, prova_aberta
):
    """
    404 e nao 403. Um 403 confirmaria que a prova existe e em qual modulo
    esta, que e justamente o que quem sonda quer descobrir.
    """
    client.force_login(outro_student)

    assert client.get(url_instrucoes(prova_aberta)).status_code == 404


def test_instrucoes_de_prova_inexistente_dao_404(aluno_logado):
    assert aluno_logado.get(
        reverse("student:exam_instructions", kwargs={"exam_id": 999999})
    ).status_code == 404


def test_prova_em_rascunho_nao_aparece_para_o_aluno(aluno_logado, prova_pronta):
    assert aluno_logado.get(url_instrucoes(prova_pronta)).status_code == 404


def test_matricula_bloqueada_perde_acesso_as_instrucoes(
    aluno_logado, prova_aberta, matricula
):
    from courses.models import Enrollment

    assert aluno_logado.get(url_instrucoes(prova_aberta)).status_code == 200

    Enrollment.objects.filter(pk=matricula.pk).update(access_enabled=False)

    assert aluno_logado.get(url_instrucoes(prova_aberta)).status_code == 404


# ---------------------------------------------------------------------------
# Inicio: somente POST
# ---------------------------------------------------------------------------


def test_iniciar_recusa_get(aluno_logado, prova_aberta):
    resposta = aluno_logado.get(url_iniciar(prova_aberta))

    assert resposta.status_code == 405
    assert ExamAttempt.objects.count() == 0


def test_post_cria_a_tentativa_e_redireciona(aluno_logado, prova_aberta):
    resposta = aluno_logado.post(url_iniciar(prova_aberta))

    assert resposta.status_code == 302
    tentativa = ExamAttempt.objects.get()
    assert str(tentativa.public_id) in resposta["Location"]
    assert str(tentativa.pk) != resposta["Location"].strip("/").split("/")[-1]


def test_duplo_post_nao_cria_duas_tentativas(aluno_logado, prova_aberta):
    primeira = aluno_logado.post(url_iniciar(prova_aberta))
    segunda = aluno_logado.post(url_iniciar(prova_aberta))

    assert ExamAttempt.objects.count() == 1
    assert primeira["Location"] == segunda["Location"]


def test_post_fora_da_janela_volta_com_mensagem(aluno_logado, prova_aberta):
    from exams.models import Exam

    agora = timezone.now()
    Exam.objects.filter(pk=prova_aberta.pk).update(
        open_at=agora + timedelta(hours=1), close_at=agora + timedelta(hours=2)
    )

    resposta = aluno_logado.post(url_iniciar(prova_aberta), follow=True)

    assert resposta.status_code == 200
    assert "ainda nao abriu" in resposta.content.decode("utf-8")
    assert ExamAttempt.objects.count() == 0


def test_senha_errada_volta_com_mensagem_e_sem_tentativa(
    aluno_logado, prova_aberta, admin_user
):
    from exams.services import set_exam_password

    set_exam_password(prova_aberta, "Turma#Alpha2026", actor=admin_user)

    resposta = aluno_logado.post(
        url_iniciar(prova_aberta), {"access_password": "errada"}, follow=True
    )

    assert resposta.status_code == 200
    assert "Senha da prova invalida" in resposta.content.decode("utf-8")
    assert ExamAttempt.objects.count() == 0


def test_a_senha_enviada_nao_aparece_na_resposta(
    aluno_logado, prova_aberta, admin_user
):
    from exams.services import set_exam_password

    set_exam_password(prova_aberta, "Turma#Alpha2026", actor=admin_user)

    resposta = aluno_logado.post(
        url_iniciar(prova_aberta), {"access_password": "MinhaTentativa#123"}, follow=True
    )

    assert "MinhaTentativa#123" not in resposta.content.decode("utf-8")


def test_post_de_aluno_sem_matricula_da_404(client, outro_student, prova_aberta):
    client.force_login(outro_student)

    assert client.post(url_iniciar(prova_aberta)).status_code == 404
    assert ExamAttempt.objects.count() == 0


# ---------------------------------------------------------------------------
# A tela da prova
# ---------------------------------------------------------------------------


def test_a_tela_da_prova_lista_as_questoes(aluno_logado, tentativa):
    resposta = aluno_logado.get(url_tentativa(tentativa))

    assert resposta.status_code == 200
    assert len(resposta.context["questoes"]) == 5
    assert resposta.context["segundos_restantes"] > 0


def test_o_tempo_restante_vem_do_servidor(aluno_logado, tentativa):
    resposta = aluno_logado.get(url_tentativa(tentativa))

    restantes = resposta.context["segundos_restantes"]
    assert 3500 < restantes <= 3600


def test_refresh_devolve_a_mesma_ordem_e_os_mesmos_tokens(aluno_logado, tentativa):
    primeira = aluno_logado.get(url_tentativa(tentativa))
    segunda = aluno_logado.get(url_tentativa(tentativa))

    tokens_um = [q.token for q in primeira.context["questoes"]]
    tokens_dois = [q.token for q in segunda.context["questoes"]]
    assert tokens_um == tokens_dois

    opcoes_um = [o.token for q in primeira.context["questoes"] for o in q.options]
    opcoes_dois = [o.token for q in segunda.context["questoes"] for o in q.options]
    assert opcoes_um == opcoes_dois


def test_tentativa_de_outro_aluno_da_404(client, segundo_aluno, tentativa):
    client.force_login(segundo_aluno)

    assert client.get(url_tentativa(tentativa)).status_code == 404


def test_uuid_inventado_da_404(aluno_logado):
    import uuid as _uuid

    assert aluno_logado.get(
        reverse("student:attempt", kwargs={"public_id": _uuid.uuid4()})
    ).status_code == 404


def test_matricula_bloqueada_no_meio_da_prova_encerra_o_acesso(
    aluno_logado, tentativa, matricula
):
    """
    O bloqueio administrativo tem efeito no proximo request, sem precisar
    derrubar a sessao do aluno.
    """
    from courses.models import Enrollment

    assert aluno_logado.get(url_tentativa(tentativa)).status_code == 200

    Enrollment.objects.filter(pk=matricula.pk).update(access_enabled=False)

    assert aluno_logado.get(url_tentativa(tentativa)).status_code == 404


def test_abrir_a_tentativa_depois_do_prazo_a_expira(aluno_logado, tentativa):
    agora = timezone.now()
    ExamAttempt.objects.filter(pk=tentativa.pk).update(
        started_at=agora - timedelta(hours=2), expires_at=agora - timedelta(hours=1)
    )

    resposta = aluno_logado.get(url_tentativa(tentativa))

    assert resposta.status_code == 200
    assert "Tempo encerrado" in resposta.content.decode("utf-8")

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.EXPIRED


# ---------------------------------------------------------------------------
# Depois de encerrada nao ha formulario
# ---------------------------------------------------------------------------


def test_apos_o_envio_a_tela_nao_tem_campos(aluno_logado, tentativa):
    responder_todas(tentativa)
    submit_attempt(tentativa)

    corpo = aluno_logado.get(url_tentativa(tentativa)).content.decode("utf-8")

    assert "Prova enviada com sucesso" in corpo
    assert "cpo-opcao" not in corpo
    assert "cpo-texto" not in corpo
    assert "attempt_autosave" not in corpo


def test_apos_expirar_a_tela_nao_tem_campos(aluno_logado, tentativa):
    from exams.services import expire_attempt

    expire_attempt(tentativa, agora=tentativa.expires_at)

    corpo = aluno_logado.get(url_tentativa(tentativa)).content.decode("utf-8")

    assert "Tempo encerrado" in corpo
    assert "cpo-opcao" not in corpo
    assert "cpo-texto" not in corpo


def test_voltar_pelo_historico_nao_devolve_formulario(aluno_logado, tentativa):
    """
    O botao voltar do navegador pode mostrar a pagina do cache, mas qualquer
    request novo passa por esta verificacao.

    E por isso que a pagina final usa a mesma URL da prova: se fosse outra
    rota, voltar traria a tela antiga e um POST dali seguiria valido.
    """
    aluno_logado.get(url_tentativa(tentativa))
    responder_todas(tentativa)
    submit_attempt(tentativa)

    corpo = aluno_logado.get(url_tentativa(tentativa)).content.decode("utf-8")

    assert "Finalizar prova" not in corpo


def test_a_pagina_final_nao_mostra_nota(aluno_logado, tentativa):
    """
    Nao existe correcao nesta etapa, e a tela nao pode sugerir resultado.

    Qualquer numero aqui seria lido como desempenho pelo aluno.
    """
    responder_todas(tentativa)
    submit_attempt(tentativa)

    corpo = aluno_logado.get(url_tentativa(tentativa)).content.decode("utf-8").lower()

    for palavra in ("nota", "acertou", "aprovad", "reprovad", "pontuacao"):
        assert palavra not in corpo


# ---------------------------------------------------------------------------
# Autosave pelo HTTP
# ---------------------------------------------------------------------------


def test_autosave_recusa_get(aluno_logado, tentativa):
    assert aluno_logado.get(url_autosave(tentativa)).status_code == 405


def test_autosave_por_json_salva_e_devolve_o_tempo(aluno_logado, tentativa, tokens):
    questao, alternativas = tokens[QuestionType.SINGLE_CHOICE]

    resposta = aluno_logado.post(
        url_autosave(tentativa),
        data=json.dumps(
            {"question_token": questao, "option_tokens": [alternativas[0]]}
        ),
        content_type="application/json",
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["saved"] is True
    assert corpo["remaining_seconds"] > 0
    assert AnswerOption.objects.count() == 1


def test_autosave_por_formulario_tambem_funciona(aluno_logado, tentativa, tokens):
    questao, _ = tokens[QuestionType.ESSAY]

    resposta = aluno_logado.post(
        url_autosave(tentativa), {"question_token": questao, "text": "Texto"}
    )

    assert resposta.status_code == 200
    assert resposta.json()["saved"] is True


def test_autosave_com_token_forjado_devolve_409(aluno_logado, tentativa):
    import uuid as _uuid

    resposta = aluno_logado.post(
        url_autosave(tentativa),
        data=json.dumps({"question_token": str(_uuid.uuid4()), "text": "x"}),
        content_type="application/json",
    )

    assert resposta.status_code == 409
    assert resposta.json()["saved"] is False
    assert Answer.objects.count() == 0


def test_autosave_sem_question_token_devolve_400(aluno_logado, tentativa):
    resposta = aluno_logado.post(
        url_autosave(tentativa),
        data=json.dumps({"text": "x"}),
        content_type="application/json",
    )

    assert resposta.status_code == 400
    assert resposta.json()["saved"] is False


def test_autosave_com_json_quebrado_devolve_400(aluno_logado, tentativa):
    resposta = aluno_logado.post(
        url_autosave(tentativa), data="{isso nao e json", content_type="application/json"
    )

    assert resposta.status_code == 400


def test_autosave_em_tentativa_enviada_devolve_409_com_o_estado(
    aluno_logado, tentativa, tokens
):
    questao, _ = tokens[QuestionType.ESSAY]
    responder_todas(tentativa)
    submit_attempt(tentativa)

    resposta = aluno_logado.post(
        url_autosave(tentativa),
        data=json.dumps({"question_token": questao, "text": "depois"}),
        content_type="application/json",
    )

    assert resposta.status_code == 409
    assert resposta.json()["status"] == AttemptStatus.SUBMITTED
    assert resposta.json()["saved"] is False


def test_autosave_apos_o_prazo_devolve_409_e_expira(aluno_logado, tentativa, tokens):
    questao, _ = tokens[QuestionType.ESSAY]
    agora = timezone.now()
    ExamAttempt.objects.filter(pk=tentativa.pk).update(
        started_at=agora - timedelta(hours=2), expires_at=agora - timedelta(hours=1)
    )

    resposta = aluno_logado.post(
        url_autosave(tentativa),
        data=json.dumps({"question_token": questao, "text": "tarde"}),
        content_type="application/json",
    )

    assert resposta.status_code == 409
    assert resposta.json()["status"] == AttemptStatus.EXPIRED

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.EXPIRED


def test_autosave_em_tentativa_alheia_da_404(
    client, segundo_aluno, tentativa, tokens
):
    questao, _ = tokens[QuestionType.ESSAY]
    client.force_login(segundo_aluno)

    resposta = client.post(
        url_autosave(tentativa),
        data=json.dumps({"question_token": questao, "text": "invadindo"}),
        content_type="application/json",
    )

    assert resposta.status_code == 404
    assert Answer.objects.count() == 0


# ---------------------------------------------------------------------------
# Finalizar pelo HTTP
# ---------------------------------------------------------------------------


def test_finalizar_recusa_get(aluno_logado, tentativa):
    assert aluno_logado.get(url_finalizar(tentativa)).status_code == 405


def test_finalizar_com_tudo_respondido_redireciona(aluno_logado, tentativa):
    responder_todas(tentativa)

    resposta = aluno_logado.post(url_finalizar(tentativa))

    assert resposta.status_code == 302
    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.SUBMITTED


def test_finalizar_com_obrigatoria_em_branco_devolve_409(aluno_logado, tentativa):
    """
    409 e nao 200: o pedido era valido, mas o estado nao permite atende-lo.

    A prova volta inteira, com as respostas ja salvas no lugar e a lista do
    que falta. Um 200 aqui pareceria envio bem-sucedido.
    """
    resposta = aluno_logado.post(url_finalizar(tentativa))

    assert resposta.status_code == 409
    corpo = resposta.content.decode("utf-8")
    assert "Questao 1 ainda nao foi respondida" in corpo
    assert "cpo-opcao" in corpo

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.IN_PROGRESS


def test_finalizar_duas_vezes_e_idempotente(aluno_logado, tentativa):
    from audit.models import AuditEvent, AuditLog

    responder_todas(tentativa)
    aluno_logado.post(url_finalizar(tentativa))
    tentativa.refresh_from_db()
    momento = tentativa.submitted_at

    aluno_logado.post(url_finalizar(tentativa))

    tentativa.refresh_from_db()
    assert tentativa.submitted_at == momento
    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_SUBMITTED).count() == 1


def test_finalizar_tentativa_alheia_da_404(client, segundo_aluno, tentativa):
    client.force_login(segundo_aluno)

    assert client.post(url_finalizar(tentativa)).status_code == 404

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


def test_iniciar_sem_csrf_e_recusado(aluno_matriculado, prova_aberta):
    cliente = Client(enforce_csrf_checks=True)
    cliente.force_login(aluno_matriculado)

    resposta = cliente.post(url_iniciar(prova_aberta))

    assert resposta.status_code == 403
    assert ExamAttempt.objects.count() == 0


def test_autosave_sem_csrf_e_recusado(aluno_matriculado, tentativa, tokens):
    """
    O endpoint de autosave nao e csrf_exempt.

    Marcar como isento seria a saida facil para o fetch do JavaScript, e
    abriria a porta para um site externo gravar respostas na prova de quem
    estivesse logado.
    """
    questao, _ = tokens[QuestionType.ESSAY]
    cliente = Client(enforce_csrf_checks=True)
    cliente.force_login(aluno_matriculado)

    resposta = cliente.post(
        url_autosave(tentativa),
        data=json.dumps({"question_token": questao, "text": "sem token"}),
        content_type="application/json",
    )

    assert resposta.status_code == 403
    assert Answer.objects.count() == 0


def test_finalizar_sem_csrf_e_recusado(aluno_matriculado, tentativa):
    cliente = Client(enforce_csrf_checks=True)
    cliente.force_login(aluno_matriculado)

    resposta = cliente.post(url_finalizar(tentativa))

    assert resposta.status_code == 403
    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.IN_PROGRESS


def test_com_csrf_o_autosave_passa(aluno_matriculado, tentativa, tokens):
    """
    O outro lado: sem isto, os tres testes acima passariam mesmo que a rota
    estivesse quebrada por outro motivo.
    """
    questao, _ = tokens[QuestionType.ESSAY]
    cliente = Client(enforce_csrf_checks=True)
    cliente.force_login(aluno_matriculado)

    cliente.get(url_tentativa(tentativa))
    token = cliente.cookies["csrftoken"].value

    resposta = cliente.post(
        url_autosave(tentativa),
        data=json.dumps({"question_token": questao, "text": "com token"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )

    assert resposta.status_code == 200
    assert resposta.json()["saved"] is True


# ---------------------------------------------------------------------------
# Papel
# ---------------------------------------------------------------------------


def test_admin_nao_realiza_prova_pela_area_do_aluno(
    admin_client_logado, prova_aberta, tentativa
):
    for url in (
        url_instrucoes(prova_aberta),
        url_tentativa(tentativa),
    ):
        assert admin_client_logado.get(url).status_code == 403

    assert admin_client_logado.post(url_iniciar(prova_aberta)).status_code == 403
    assert admin_client_logado.post(url_finalizar(tentativa)).status_code == 403


def test_anonimo_e_mandado_para_o_login(client, prova_aberta, tentativa):
    for url in (url_instrucoes(prova_aberta), url_tentativa(tentativa)):
        resposta = client.get(url)
        assert resposta.status_code == 302
        assert "/login/" in resposta["Location"]


def test_aluno_continua_sem_acesso_a_administracao(aluno_logado, prova_aberta):
    assert aluno_logado.get(
        reverse("admin_panel:exam_gabarito", kwargs={"pk": prova_aberta.pk})
    ).status_code == 403


# ---------------------------------------------------------------------------
# Campos que o navegador nao pode ditar
# ---------------------------------------------------------------------------


def test_campos_extras_no_autosave_sao_ignorados(aluno_logado, tentativa, tokens):
    """
    O corpo do autosave le exatamente tres coisas. Tudo o mais e ignorado
    porque nao ha nada no codigo que o leia.
    """
    questao, _ = tokens[QuestionType.ESSAY]
    prazo_original = tentativa.expires_at

    resposta = aluno_logado.post(
        url_autosave(tentativa),
        data=json.dumps(
            {
                "question_token": questao,
                "text": "Resposta legitima",
                "status": "SUBMITTED",
                "expires_at": "2099-01-01T00:00:00Z",
                "student_id": 99999,
                "attempt_number": 42,
                "score": 10,
                "is_correct": True,
                "total_points_snapshot": "0.00",
            }
        ),
        content_type="application/json",
    )

    assert resposta.status_code == 200

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.IN_PROGRESS
    assert tentativa.expires_at == prazo_original
    assert tentativa.attempt_number == 1
    assert tentativa.student_id != 99999
    assert str(tentativa.total_points_snapshot) == "10.00"


def test_campos_extras_no_inicio_sao_ignorados(aluno_logado, prova_aberta):
    aluno_logado.post(
        url_iniciar(prova_aberta),
        {
            "status": "SUBMITTED",
            "attempt_number": "99",
            "expires_at": "2099-01-01T00:00:00Z",
            "total_points_snapshot": "0.00",
            "student": "99999",
        },
    )

    tentativa = ExamAttempt.objects.get()
    assert tentativa.status == AttemptStatus.IN_PROGRESS
    assert tentativa.attempt_number == 1
    assert str(tentativa.total_points_snapshot) == "10.00"
    assert tentativa.expires_at < timezone.now() + timedelta(hours=2)


def test_campos_extras_no_envio_sao_ignorados(aluno_logado, tentativa):
    responder_todas(tentativa)

    aluno_logado.post(
        url_finalizar(tentativa),
        {"status": "EXPIRED", "score": "10", "submitted_at": "2099-01-01T00:00:00Z"},
    )

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.SUBMITTED
    assert tentativa.submitted_at.year == timezone.now().year


# ---------------------------------------------------------------------------
# Desempenho
# ---------------------------------------------------------------------------


@pytest.fixture
def prova_grande(db, modulo, admin_user, janela_aberta):
    """Vinte questoes com quatro alternativas, para medir crescimento."""
    from exams.services import create_exam, create_question, publish_exam

    abertura, encerramento = janela_aberta
    prova = create_exam(
        module=modulo,
        title="Prova extensa",
        instructions="Leia com atencao.",
        open_at=abertura,
        close_at=encerramento,
        duration_minutes=120,
        max_attempts=1,
        actor=admin_user,
    )
    for numero in range(20):
        create_question(
            prova,
            type=QuestionType.SINGLE_CHOICE,
            text="Questao numero {}".format(numero + 1),
            points="0.50",
            order=numero + 1,
            opcoes=[
                {"text": "Alternativa A", "is_correct": numero % 4 == 0},
                {"text": "Alternativa B", "is_correct": numero % 4 == 1},
                {"text": "Alternativa C", "is_correct": numero % 4 == 2},
                {"text": "Alternativa D", "is_correct": numero % 4 == 3},
            ],
            actor=admin_user,
        )
    return publish_exam(prova, actor=admin_user)


def test_a_tela_da_prova_nao_faz_uma_consulta_por_questao(
    aluno_logado, prova_grande, aluno_matriculado, django_assert_max_num_queries
):
    """
    Vinte questoes com quatro alternativas: 20 + 80 linhas.

    Sem selector proprio isso viraria uma consulta por questao mais uma por
    alternativa — cem consultas para desenhar uma tela, com a turma inteira
    carregando ao mesmo tempo no inicio da prova.
    """
    tentativa = start_attempt(aluno_matriculado, prova_grande)

    with django_assert_max_num_queries(15):
        resposta = aluno_logado.get(url_tentativa(tentativa))

    assert resposta.status_code == 200
    assert len(resposta.context["questoes"]) == 20


def test_o_numero_de_consultas_nao_cresce_com_as_respostas(
    aluno_logado, prova_grande, aluno_matriculado, django_assert_max_num_queries
):
    """
    A mesma tela com todas as questoes respondidas.

    Se a leitura das respostas fosse por questao, o custo dobraria conforme o
    aluno responde — e a tela ficaria mais lenta justamente no fim da prova.
    """
    tentativa = start_attempt(aluno_matriculado, prova_grande)
    for linha in AttemptQuestion.objects.filter(attempt=tentativa).prefetch_related(
        "options"
    ):
        autosave_answer(
            tentativa,
            question_token=str(linha.public_token),
            option_tokens=[str(linha.options.first().public_token)],
        )

    with django_assert_max_num_queries(15):
        aluno_logado.get(url_tentativa(tentativa))


def test_montar_a_tentativa_nao_faz_uma_consulta_por_alternativa(
    prova_grande, aluno_matriculado, django_assert_max_num_queries
):
    with django_assert_max_num_queries(20):
        start_attempt(aluno_matriculado, prova_grande)

    assert AttemptQuestion.objects.count() == 20


def test_o_custo_do_autosave_nao_depende_do_tamanho_da_prova(
    aluno_logado, prova_aberta, prova_grande, aluno_matriculado
):
    """
    Compara o mesmo autosave numa prova de 5 questoes e numa de 20.

    Fixar um numero absoluto seria arbitrario — ele muda com o backend de
    sessao, com middleware novo, com uma versao do Django. O que importa e
    que salvar uma resposta custe o mesmo independentemente do tamanho da
    prova: qualquer laco escondido sobre as questoes apareceria como
    diferenca entre os dois numeros.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def medir(prova):
        tentativa = start_attempt(aluno_matriculado, prova)
        linha = (
            AttemptQuestion.objects.filter(attempt=tentativa)
            .prefetch_related("options")
            .first()
        )
        corpo = json.dumps(
            {
                "question_token": str(linha.public_token),
                "option_tokens": [str(linha.options.first().public_token)],
            }
        )
        with CaptureQueriesContext(connection) as consultas:
            resposta = aluno_logado.post(
                url_autosave(tentativa), data=corpo, content_type="application/json"
            )
        assert resposta.status_code == 200
        return len(consultas)

    pequena = medir(prova_aberta)
    grande = medir(prova_grande)

    assert pequena == grande, "o autosave cresce com o tamanho da prova"


def test_a_tela_do_modulo_nao_faz_uma_consulta_por_prova(
    aluno_logado, prova_aberta, prova_grande, modulo, django_assert_max_num_queries
):
    with django_assert_max_num_queries(15):
        resposta = aluno_logado.get(
            reverse("student:module_detail", kwargs={"pk": modulo.pk})
        )

    assert resposta.status_code == 200
    assert len(resposta.context["provas"]) == 2
