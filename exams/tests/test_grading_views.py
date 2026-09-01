"""
As telas de correcao, notas e resultado do aluno.

O que separa este arquivo dos de servico: aqui tudo passa pelo HTTP, com
sessao e papel de verdade. Um teste de servico prova que a regra existe; este
prova que a rota realmente a atravessa.

Politica de resposta verificada aqui:

    403  papel errado ou CSRF ausente
    404  a coisa nao e sua, ou nao existe
    405  metodo errado numa rota de escrita
    409  e valido, mas o estado nao permite

A assimetria entre 403 e 404 e deliberada. As telas administrativas devolvem
403 a um aluno: a area existe e nao e segredo, o que falta e permissao. O
resultado de OUTRO aluno devolve 404: ali a existencia do recurso e que nao
pode ser confirmada.
"""

from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from exams.models import (
    AttemptQuestion,
    AttemptResult,
    GradingStatus,
    QuestionGradingStatus,
    QuestionType,
)
from exams.services import (
    autosave_answer,
    finalize_grading,
    save_manual_grade,
    submit_attempt,
)

pytestmark = pytest.mark.django_db

MANUAIS = [QuestionType.SHORT_TEXT, QuestionType.ESSAY]


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------


def corretas(linha):
    return [
        a for a in linha.options.select_related("option").all() if a.option.is_correct
    ]


def responder_tudo(tentativa):
    for linha in tentativa.questions.select_related("question").all():
        if linha.question.type in MANUAIS:
            autosave_answer(
                tentativa,
                question_token=str(linha.public_token),
                text="Resposta com conteudo.",
            )
        else:
            autosave_answer(
                tentativa,
                question_token=str(linha.public_token),
                option_tokens=[str(a.public_token) for a in corretas(linha)],
            )


def manuais(tentativa):
    return list(
        AttemptQuestion.objects.filter(
            attempt=tentativa, question__type__in=MANUAIS
        ).order_by("display_order")
    )


@pytest.fixture
def aguardando(tentativa):
    responder_tudo(tentativa)
    return submit_attempt(tentativa)


@pytest.fixture
def corrigida(aguardando, admin_user):
    for linha in manuais(aguardando):
        save_manual_grade(
            aguardando,
            question_id=linha.pk,
            points=linha.points_snapshot,
            actor=admin_user,
        )
    return finalize_grading(aguardando, actor=admin_user)


@pytest.fixture
def aluno_logado(client, aluno_matriculado):
    client.force_login(aluno_matriculado)
    return client


def url_correcao(tentativa):
    return reverse(
        "admin_panel:correction_detail", kwargs={"public_id": tentativa.public_id}
    )


def url_salvar(tentativa):
    return reverse(
        "admin_panel:correction_save", kwargs={"public_id": tentativa.public_id}
    )


def url_finalizar(tentativa):
    return reverse(
        "admin_panel:correction_finalize", kwargs={"public_id": tentativa.public_id}
    )


def url_nota(tentativa):
    return reverse(
        "admin_panel:grade_detail", kwargs={"public_id": tentativa.public_id}
    )


def url_resultado(tentativa):
    return reverse(
        "student:attempt_result", kwargs={"public_id": tentativa.public_id}
    )


# ---------------------------------------------------------------------------
# Fila de correcao
# ---------------------------------------------------------------------------


def test_a_fila_lista_o_que_espera_avaliador(admin_client_logado, aguardando):
    resposta = admin_client_logado.get(reverse("admin_panel:correction_list"))

    assert resposta.status_code == 200
    corpo = resposta.content.decode("utf-8")
    assert aguardando.student.full_name in corpo
    assert aguardando.exam.title in corpo


def test_a_fila_nao_lista_o_que_ja_foi_corrigido(admin_client_logado, corrigida):
    resposta = admin_client_logado.get(reverse("admin_panel:correction_list"))

    corpo = resposta.content.decode("utf-8")
    assert "Nenhuma tentativa aguardando correcao" in corpo


def test_a_fila_filtra_por_aluno(admin_client_logado, aguardando):
    resposta = admin_client_logado.get(
        reverse("admin_panel:correction_list"), {"q": "nao-existe-esse-nome"}
    )

    corpo = resposta.content.decode("utf-8")
    assert aguardando.student.full_name not in corpo


# ---------------------------------------------------------------------------
# Tela de correcao
# ---------------------------------------------------------------------------


def test_a_tela_de_correcao_mostra_a_resposta_do_aluno(
    admin_client_logado, aguardando
):
    corpo = admin_client_logado.get(url_correcao(aguardando)).content.decode("utf-8")

    assert "Resposta com conteudo." in corpo


def test_a_tela_de_correcao_mostra_o_gabarito(admin_client_logado, aguardando):
    """
    Aqui o gabarito PODE aparecer, e precisa: sem ele nao ha como conferir a
    correcao automatica. Esta e area administrativa.
    """
    corpo = admin_client_logado.get(url_correcao(aguardando)).content.decode("utf-8")

    assert "Gabarito" in corpo
    assert "Brasilia" in corpo


def test_a_tela_de_correcao_nao_edita_objetiva(admin_client_logado, aguardando):
    """
    So as manuais tem campo. As objetivas aparecem somente leitura, e o
    servico recusa nota manual nelas mesmo com POST forjado.
    """
    corpo = admin_client_logado.get(url_correcao(aguardando)).content.decode("utf-8")

    objetivas = AttemptQuestion.objects.filter(
        attempt=aguardando, question__type=QuestionType.SINGLE_CHOICE
    )
    for linha in objetivas:
        assert 'id="pontos-{}"'.format(linha.pk) not in corpo

    for linha in manuais(aguardando):
        assert 'id="pontos-{}"'.format(linha.pk) in corpo


def test_salvar_nota_pelo_http(admin_client_logado, aguardando):
    linha = manuais(aguardando)[0]

    resposta = admin_client_logado.post(
        url_salvar(aguardando),
        {"questao": linha.pk, "pontos": "1.00", "comentario": "ok"},
    )

    assert resposta.status_code == 302
    linha.refresh_from_db()
    assert linha.awarded_points == Decimal("1.00")


def test_finalizar_pelo_http(admin_client_logado, aguardando):
    for linha in manuais(aguardando):
        admin_client_logado.post(
            url_salvar(aguardando),
            {"questao": linha.pk, "pontos": str(linha.points_snapshot)},
        )

    resposta = admin_client_logado.post(url_finalizar(aguardando))

    assert resposta.status_code == 302
    aguardando.refresh_from_db()
    assert aguardando.grading_status == GradingStatus.GRADED


def test_finalizar_com_pendencia_responde_409(admin_client_logado, aguardando):
    """
    409, e nao 302 com mensagem.

    O pedido era valido; o estado e que nao permite atende-lo. Um 200 ou um
    302 fariam a tela parecer uma finalizacao bem-sucedida.
    """
    resposta = admin_client_logado.post(url_finalizar(aguardando))

    assert resposta.status_code == 409
    corpo = resposta.content.decode("utf-8")
    assert "Ainda faltam notas" in corpo

    aguardando.refresh_from_db()
    assert aguardando.grading_status == GradingStatus.AWAITING_REVIEW


def test_get_nas_rotas_de_escrita_responde_405(admin_client_logado, aguardando):
    assert admin_client_logado.get(url_salvar(aguardando)).status_code == 405
    assert admin_client_logado.get(url_finalizar(aguardando)).status_code == 405


def test_correcao_de_tentativa_inexistente_responde_404(admin_client_logado):
    url = reverse(
        "admin_panel:correction_detail",
        kwargs={"public_id": "00000000-0000-4000-8000-000000000000"},
    )
    assert admin_client_logado.get(url).status_code == 404


# ---------------------------------------------------------------------------
# Notas
# ---------------------------------------------------------------------------


def test_a_lista_de_notas_mostra_so_o_que_esta_fechado(
    admin_client_logado, corrigida
):
    resposta = admin_client_logado.get(reverse("admin_panel:grade_list"))

    assert resposta.status_code == 200
    corpo = resposta.content.decode("utf-8")
    assert corrigida.student.full_name in corpo
    assert "Aprovado" in corpo


def test_a_lista_de_notas_ignora_o_que_espera_avaliador(
    admin_client_logado, aguardando
):
    corpo = admin_client_logado.get(
        reverse("admin_panel:grade_list")
    ).content.decode("utf-8")

    assert "Nenhuma nota fechada ainda" in corpo


def test_o_resultado_aparece_escrito_e_nao_so_colorido(
    admin_client_logado, corrigida
):
    """
    Cerca de 8% dos homens tem alguma deficiencia de visao de cores, e
    verde-vermelho e exatamente o par que eles confundem. A palavra precisa
    estar la.
    """
    corpo = admin_client_logado.get(
        reverse("admin_panel:grade_list")
    ).content.decode("utf-8")

    assert "Aprovado" in corpo


def test_detalhe_da_nota_mostra_a_composicao(admin_client_logado, corrigida):
    corpo = admin_client_logado.get(url_nota(corrigida)).content.decode("utf-8")

    assert "Objetivas" in corpo
    assert "Manuais" in corpo
    assert "Nota final" in corpo


def test_detalhe_da_nota_recusa_tentativa_nao_corrigida(
    admin_client_logado, aguardando
):
    """A tela de nota so existe quando ha nota."""
    assert admin_client_logado.get(url_nota(aguardando)).status_code == 404


def test_exportar_csv(admin_client_logado, corrigida):
    resposta = admin_client_logado.get(reverse("admin_panel:grade_export"))

    assert resposta.status_code == 200
    assert "text/csv" in resposta["Content-Type"]
    assert "attachment" in resposta["Content-Disposition"]

    corpo = resposta.content.decode("utf-8")
    assert corpo.startswith("﻿"), "falta o BOM que o Excel em portugues precisa"
    assert corrigida.student.email in corpo
    assert "Aprovado" in corpo


def test_o_csv_nao_leva_resposta_nem_gabarito(admin_client_logado, corrigida):
    """
    O arquivo circula por e-mail e pendrive. Ele carrega o resultado, nao o
    conteudo da prova.
    """
    corpo = admin_client_logado.get(
        reverse("admin_panel:grade_export")
    ).content.decode("utf-8").lower()

    for proibido in (
        "resposta com conteudo",
        "brasilia",
        "is_correct",
        "gabarito",
        str(corrigida.public_id),
    ):
        assert proibido.lower() not in corpo


# ---------------------------------------------------------------------------
# Seguranca das rotas administrativas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "nome",
    ["admin_panel:correction_list", "admin_panel:grade_list", "admin_panel:grade_export"],
)
def test_aluno_nao_entra_nas_telas_administrativas(student_client_logado, nome):
    assert student_client_logado.get(reverse(nome)).status_code == 403


def test_aluno_nao_abre_a_correcao_de_ninguem(student_client_logado, aguardando):
    assert student_client_logado.get(url_correcao(aguardando)).status_code == 403


def test_anonimo_e_mandado_para_o_login(client, aguardando):
    resposta = client.get(reverse("admin_panel:correction_list"))
    assert resposta.status_code == 302
    assert reverse("accounts:login") in resposta["Location"]


def test_salvar_sem_csrf_responde_403(admin_user, aguardando):
    """CSRF de verdade: o cliente de teste normal desliga a verificacao."""
    cliente = Client(enforce_csrf_checks=True)
    cliente.force_login(admin_user)
    linha = manuais(aguardando)[0]

    resposta = cliente.post(
        url_salvar(aguardando), {"questao": linha.pk, "pontos": "1.00"}
    )

    assert resposta.status_code == 403
    linha.refresh_from_db()
    assert linha.awarded_points is None


def test_finalizar_sem_csrf_responde_403(admin_user, aguardando):
    cliente = Client(enforce_csrf_checks=True)
    cliente.force_login(admin_user)

    assert cliente.post(url_finalizar(aguardando)).status_code == 403


def test_campos_calculados_vindos_do_post_sao_ignorados(
    admin_client_logado, aguardando
):
    """
    Mass assignment.

    A view le tres coisas: questao, pontos, comentario. Qualquer outro nome no
    corpo nao e lido por ninguem.
    """
    linha = manuais(aguardando)[0]

    admin_client_logado.post(
        url_salvar(aguardando),
        {
            "questao": linha.pk,
            "pontos": "1.00",
            "final_score": "10",
            "result": "APPROVED",
            "grading_status": "GRADED",
            "obtained_points": "999",
            "points_snapshot": "999",
            "passing_score_snapshot": "0",
        },
    )

    aguardando.refresh_from_db()
    assert aguardando.grading_status == GradingStatus.AWAITING_REVIEW
    assert aguardando.result is None
    assert aguardando.final_score is None
    assert aguardando.obtained_points == Decimal("0.00")

    linha.refresh_from_db()
    assert linha.points_snapshot != Decimal("999")


# ---------------------------------------------------------------------------
# Resultado do aluno
# ---------------------------------------------------------------------------


def test_aguardando_correcao_nao_mostra_nota(aluno_logado, aguardando):
    """
    Sem nota provisoria.

    As objetivas ja estao corrigidas e o sistema SABE quantos pontos o aluno
    tem. Dizer isso seria entregar metade de uma nota, e o aluno calcularia a
    propria aprovacao com dados incompletos.
    """
    corpo = aluno_logado.get(url_resultado(aguardando)).content.decode("utf-8")

    assert "aguardando correcao" in corpo.lower()
    for proibido in ("Aprovado", "Reprovado", "Sua nota"):
        assert proibido not in corpo


def test_corrigida_mostra_o_resultado(aluno_logado, corrigida):
    corpo = aluno_logado.get(url_resultado(corrigida)).content.decode("utf-8")

    assert "Avaliacao concluida" in corpo
    assert "Aprovado" in corpo


def test_a_nota_aparece_quando_a_prova_permite(aluno_logado, corrigida):
    assert corrigida.exam.show_score_after_submission is True

    corpo = aluno_logado.get(url_resultado(corrigida)).content.decode("utf-8")

    assert "Sua nota" in corpo
    assert "10,00" in corpo


def test_a_nota_some_quando_a_prova_nao_permite(aluno_logado, corrigida):
    """
    show_score_after_submission=False esconde o NUMERO, nao o resultado.

    Esconder tambem "aprovado ou reprovado" tornaria a tela inutil, e o aluno
    descobriria de qualquer forma pelo certificado.
    """
    from exams.models import Exam

    Exam.objects.filter(pk=corrigida.exam_id).update(
        show_score_after_submission=False
    )

    corpo = aluno_logado.get(url_resultado(corrigida)).content.decode("utf-8")

    assert "Sua nota" not in corpo
    assert "10,00" not in corpo
    assert "Aprovado" in corpo


def test_reprovado_ve_a_mensagem_da_prova(aluno_logado, aguardando, admin_user):
    from exams.models import Exam

    Exam.objects.filter(pk=aguardando.exam_id).update(
        failure_message="Procure a coordenacao para reagendar."
    )
    for linha in manuais(aguardando):
        save_manual_grade(
            aguardando, question_id=linha.pk, points="0", actor=admin_user
        )
    # Zera tambem as objetivas para garantir a reprovacao.
    AttemptQuestion.objects.filter(attempt=aguardando).update(
        awarded_points=Decimal("0.00"),
        grading_status=QuestionGradingStatus.MANUALLY_GRADED,
    )
    finalize_grading(aguardando, actor=admin_user)

    corpo = aluno_logado.get(url_resultado(aguardando)).content.decode("utf-8")

    assert "Reprovado" in corpo
    assert "Procure a coordenacao para reagendar." in corpo


def test_aprovado_nao_ve_mensagem_de_reprovacao(aluno_logado, corrigida):
    from exams.models import Exam

    Exam.objects.filter(pk=corrigida.exam_id).update(
        failure_message="Procure a coordenacao para reagendar."
    )

    corpo = aluno_logado.get(url_resultado(corrigida)).content.decode("utf-8")

    assert "Procure a coordenacao" not in corpo


def test_aprovado_ve_o_botao_de_emitir_certificado(aluno_logado, corrigida):
    """
    A Etapa 6 substituiu o aviso "disponibilizado em breve" pela emissao real.

    O botao vive dentro de um <form method="post">, e nao num link: emitir
    conclui a matricula e encerra o acesso ao modulo, e mudanca de estado
    academico nao pode acontecer porque alguem — ou algum robo — seguiu uma
    URL.
    """
    corpo = aluno_logado.get(url_resultado(corrigida)).content.decode("utf-8")

    assert "Certificado" in corpo
    assert "Emitir certificado" in corpo
    assert 'method="post"' in corpo
    assert "csrfmiddlewaretoken" in corpo
    # O aviso antigo saiu de cena.
    assert "disponibilizado em breve" not in corpo


def test_o_aviso_de_certificado_explica_o_efeito_antes_do_clique(
    aluno_logado, corrigida
):
    """
    Emitir encerra o acesso ao modulo. O aluno precisa saber disso ANTES.

    Descobrir depois que o modulo sumiu, sem aviso, seria o tipo de surpresa
    que gera chamado na secretaria.
    """
    corpo = aluno_logado.get(url_resultado(corrigida)).content.decode("utf-8")

    assert "acesso a ele" in corpo
    assert "concluido" in corpo


def test_reprovado_nao_ve_botao_de_certificado(aluno_logado, aguardando, admin_user):
    """Sem aprovacao nao ha documento, e o botao nem chega a aparecer."""
    from exams.models import AttemptQuestion, QuestionGradingStatus
    from exams.services import finalize_grading, save_manual_grade

    for linha in aguardando.questions.select_related("question").all():
        if linha.question.type in {"SHORT_TEXT", "ESSAY"}:
            # question_id aqui e a PK da AttemptQuestion, nao da Question.
            save_manual_grade(
                aguardando, question_id=linha.pk, points="0", actor=admin_user
            )
    AttemptQuestion.objects.filter(attempt=aguardando).update(
        awarded_points=Decimal("0.00"),
        grading_status=QuestionGradingStatus.MANUALLY_GRADED,
    )
    finalize_grading(aguardando, actor=admin_user)

    corpo = aluno_logado.get(url_resultado(aguardando)).content.decode("utf-8")

    assert "Reprovado" in corpo
    assert "Emitir certificado" not in corpo


def test_o_resultado_nunca_mostra_gabarito(aluno_logado, corrigida):
    """
    O aluno nao ve alternativa correta, is_correct, explicacao interna nem
    comentario do avaliador.
    """
    corpo = aluno_logado.get(url_resultado(corrigida)).content.decode("utf-8")
    minusculo = corpo.lower()

    for proibido in (
        "is_correct",
        "gabarito",
        "internal_explanation",
        "brasilia",
        "capital do brasil",
        "comentario",
    ):
        assert proibido not in minusculo


def test_resultado_de_outro_aluno_responde_404(
    client, corrigida, outro_student, modulo
):
    """
    404, e nao 403.

    Aqui a existencia do recurso e que nao pode ser confirmada: um 403 diria
    "existe uma tentativa com esse identificador, e nao e sua".
    """
    from courses.services import create_enrollment

    create_enrollment(student=outro_student, module=modulo)
    client.force_login(outro_student)

    assert client.get(url_resultado(corrigida)).status_code == 404


def test_resultado_inexistente_responde_404(aluno_logado):
    url = reverse(
        "student:attempt_result",
        kwargs={"public_id": "00000000-0000-4000-8000-000000000000"},
    )
    assert aluno_logado.get(url).status_code == 404


def test_a_prova_continua_sem_volta_ao_formulario(aluno_logado, corrigida):
    """
    Regra da Etapa 4 que precisa continuar valendo.

    Depois de corrigida, abrir a URL da tentativa nao devolve o formulario.
    """
    url = reverse("student:attempt", kwargs={"public_id": corrigida.public_id})
    corpo = aluno_logado.get(url).content.decode("utf-8")

    assert "cpo-opcao" not in corpo
    assert "cpo-texto" not in corpo
