"""
A trilha de auditoria das tentativas.

Dois eixos, como em test_audit.py: o evento certo e gravado, e a metadata nao
carrega nada que nao possa ser guardado.

O que nunca pode entrar aqui
----------------------------
    senha da prova     ja proibida desde a Etapa 3
    gabarito           is_correct e texto de alternativa correta
    respostas          texto de redacao e alternativas marcadas
    tokens publicos    sao a credencial de escrita do aluno na tentativa

Os tokens merecem explicacao. Eles nao sao segredo criptografico, mas sao o
que identifica uma questao para quem esta respondendo. Guarda-los numa tabela
que existe para ser lida por relatorio e consulta administrativa espalha
material de escrita por onde ele nao precisa estar. As respostas ja moram na
tabela certa; a trilha guarda o fato, nao o conteudo.
"""

import json

import pytest

from audit.models import AuditEvent, AuditLog
from exams.models import AttemptQuestion, AttemptStatus, QuestionType
from exams.services import (
    autosave_answer,
    expire_attempt,
    set_exam_password,
    start_attempt,
    submit_attempt,
)

pytestmark = pytest.mark.django_db

EVENTOS_DE_TENTATIVA = (
    AuditEvent.ATTEMPT_STARTED,
    AuditEvent.ATTEMPT_SUBMITTED,
    AuditEvent.ATTEMPT_EXPIRED,
)


def trilha_como_texto():
    """Toda a metadata da trilha, concatenada, para varredura."""
    return " ".join(
        json.dumps(registro.metadata, ensure_ascii=False)
        for registro in AuditLog.objects.all()
    )


def responder_todas(tentativa, *, texto="Resposta."):
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
                tentativa, question_token=str(linha.public_token), text=texto
            )


# ---------------------------------------------------------------------------
# Eventos gravados
# ---------------------------------------------------------------------------


def test_inicio_registra_evento(tentativa, aluno_matriculado, prova_aberta):
    registros = AuditLog.objects.filter(event=AuditEvent.ATTEMPT_STARTED)

    assert registros.count() == 1
    registro = registros.first()
    assert registro.student_id == aluno_matriculado.pk
    assert registro.entity_type == "ExamAttempt"
    assert registro.entity_id == str(tentativa.pk)
    assert registro.metadata["exam_id"] == prova_aberta.pk
    assert registro.metadata["attempt_number"] == 1
    assert registro.metadata["questions"] == 5


def test_envio_registra_evento(tentativa):
    responder_todas(tentativa)
    submit_attempt(tentativa)

    registro = AuditLog.objects.filter(event=AuditEvent.ATTEMPT_SUBMITTED).first()
    assert registro is not None
    assert registro.metadata["answered"] == 5


def test_expiracao_registra_evento(tentativa):
    expire_attempt(tentativa, agora=tentativa.expires_at)

    registro = AuditLog.objects.filter(event=AuditEvent.ATTEMPT_EXPIRED).first()
    assert registro is not None
    assert registro.metadata["attempt_number"] == 1


def test_expiracao_nao_tem_autor(tentativa):
    """
    Expirar e acao do relogio, e nao de alguem.

    Registrar o aluno como autor sugeriria que ele decidiu encerrar; registrar
    um administrador seria pior ainda, porque nenhum estava envolvido. O
    comando de gestao expira sem ninguem logado, e o campo precisa refletir
    isso nos dois caminhos.
    """
    expire_attempt(tentativa, agora=tentativa.expires_at)

    registro = AuditLog.objects.filter(event=AuditEvent.ATTEMPT_EXPIRED).first()
    assert registro.actor_id is None
    assert registro.student_id is not None


def test_autosave_nao_gera_evento(tentativa, tokens):
    """
    Um evento por resposta salva produziria milhares de linhas numa prova de
    turma inteira, sem contar nada que a tabela de respostas ja nao conte
    melhor — e com a resposta duplicada num lugar a mais.
    """
    antes = AuditLog.objects.count()

    questao, _ = tokens[QuestionType.ESSAY]
    for numero in range(20):
        autosave_answer(
            tentativa, question_token=questao, text="Versao {}".format(numero)
        )

    assert AuditLog.objects.count() == antes


def test_operacao_recusada_nao_registra_nada(tentativa):
    from exams.services.attempt import ObrigatoriasPendentes

    antes = AuditLog.objects.count()

    with pytest.raises(ObrigatoriasPendentes):
        submit_attempt(tentativa)

    assert AuditLog.objects.count() == antes


def test_retomar_nao_registra_segundo_inicio(prova_aberta, aluno_matriculado):
    start_attempt(aluno_matriculado, prova_aberta)
    start_attempt(aluno_matriculado, prova_aberta)
    start_attempt(aluno_matriculado, prova_aberta)

    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_STARTED).count() == 1


# ---------------------------------------------------------------------------
# O que a metadata nao pode conter
# ---------------------------------------------------------------------------


def test_a_trilha_nao_guarda_respostas(tentativa):
    redacao = "Minha dissertacao com argumento unico e reconhecivel: pantanal."
    responder_todas(tentativa, texto=redacao)
    submit_attempt(tentativa)

    corpo = trilha_como_texto()

    assert redacao not in corpo
    assert "pantanal" not in corpo


def test_a_trilha_nao_guarda_alternativas_marcadas(tentativa):
    responder_todas(tentativa)
    submit_attempt(tentativa)

    corpo = trilha_como_texto().lower()

    for texto in ("brasilia", "rio de janeiro", "verdadeiro", "falso"):
        assert texto not in corpo


def test_a_trilha_nao_guarda_o_gabarito(tentativa):
    responder_todas(tentativa)
    submit_attempt(tentativa)
    expire_attempt(tentativa)

    corpo = trilha_como_texto().lower()

    for marcador in ("is_correct", "correct", "gabarito", "apostila"):
        assert marcador not in corpo


def test_a_trilha_nao_guarda_tokens_publicos(tentativa):
    """
    Os tokens sao o que o aluno usa para escrever na tentativa dele.

    A sanitizacao de audit.services ja remove qualquer chave que contenha
    "token", entao a protecao vale mesmo que alguem acrescente um campo novo
    sem lembrar desta regra. Este teste confirma que ela alcanca os eventos
    de tentativa.
    """
    responder_todas(tentativa)
    submit_attempt(tentativa)

    corpo = trilha_como_texto()

    for linha in AttemptQuestion.objects.filter(attempt=tentativa):
        assert str(linha.public_token) not in corpo


def test_a_trilha_nao_guarda_a_senha_da_prova(
    prova_aberta, aluno_matriculado, admin_user
):
    senha = "Turma#Alpha2026"
    set_exam_password(prova_aberta, senha, actor=admin_user)
    prova_aberta.refresh_from_db()

    start_attempt(aluno_matriculado, prova_aberta, supplied_password=senha)

    corpo = trilha_como_texto()
    assert senha not in corpo
    assert prova_aberta.access_password_hash not in corpo


def test_a_trilha_nao_guarda_o_enunciado(tentativa):
    responder_todas(tentativa)
    submit_attempt(tentativa)

    corpo = trilha_como_texto().lower()

    assert "capital do brasil" not in corpo
    assert "numeros primos" not in corpo


def test_o_identificador_publico_nao_e_gravado_como_identidade(tentativa):
    """
    entity_id da trilha e a PK interna, de proposito.

    A trilha e ferramenta administrativa e cruza com o banco pela chave
    interna. O public_id existe para o navegador; guarda-lo aqui seria
    espalhar o identificador de acesso do aluno sem necessidade nenhuma.
    """
    registro = AuditLog.objects.filter(event=AuditEvent.ATTEMPT_STARTED).first()

    assert registro.entity_id == str(tentativa.pk)
    assert str(tentativa.public_id) not in json.dumps(registro.metadata)


# ---------------------------------------------------------------------------
# Evidencia operacional
# ---------------------------------------------------------------------------


def test_ip_e_user_agent_ficam_na_tentativa(prova_aberta, aluno_matriculado, rf):
    """
    IP e user-agent sao evidencia, nunca autenticacao.

    Ficam gravados para investigacao posterior, e nada no sistema os usa para
    decidir se o aluno pode continuar — trocar de rede ou de aparelho no meio
    da prova e legitimo e acontece o tempo todo no celular.
    """
    pedido = rf.post("/", REMOTE_ADDR="203.0.113.9", HTTP_USER_AGENT="Mozilla/5.0 Teste")

    tentativa = start_attempt(aluno_matriculado, prova_aberta, request=pedido)

    assert tentativa.ip_address == "203.0.113.9"
    assert "Teste" in tentativa.user_agent


def test_a_tentativa_nao_e_amarrada_ao_dispositivo(
    prova_aberta, aluno_matriculado, rf, client
):
    """
    Continuar em outro aparelho precisa funcionar: a mesma tentativa, os
    mesmos tokens, o mesmo prazo.
    """
    from django.urls import reverse

    pedido = rf.post("/", REMOTE_ADDR="203.0.113.9", HTTP_USER_AGENT="Celular")
    tentativa = start_attempt(aluno_matriculado, prova_aberta, request=pedido)

    client.force_login(aluno_matriculado)
    resposta = client.get(
        reverse("student:attempt", kwargs={"public_id": tentativa.public_id}),
        REMOTE_ADDR="198.51.100.4",
        HTTP_USER_AGENT="Notebook",
    )

    assert resposta.status_code == 200
    assert resposta.context["tentativa"].pk == tentativa.pk


def test_todos_os_eventos_novos_existem_no_enum():
    """
    Guarda contra remocao silenciosa: apagar um valor do enum invalidaria a
    trilha ja gravada, que referencia o codigo em texto.
    """
    valores = set(AuditEvent.values)

    for evento in EVENTOS_DE_TENTATIVA:
        assert evento in valores
