"""
Envio, expiracao e a corrida entre os dois.

Regra que separa os dois desfechos:

    envio voluntario  o aluno decide. Exige as obrigatorias respondidas, e
                      grava submitted_at.

    expiracao         o relogio decide. Nao exige nada, e grava expired_at.

Os dois campos nunca sao preenchidos juntos. Marcar submitted_at numa
tentativa que expirou registraria um envio que nao houve.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from audit.models import AuditEvent, AuditLog
from exams.models import (
    Answer,
    AttemptQuestion,
    AttemptStatus,
    ExamAttempt,
    QuestionType,
    TIPOS_COM_ALTERNATIVAS,
)
from exams.services import autosave_answer, expire_attempt, submit_attempt
from exams.services.attempt import ObrigatoriasPendentes

pytestmark = pytest.mark.django_db


def responder_todas(tentativa, *, texto="Resposta."):
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


def vencer_prazo(tentativa):
    """Empurra a tentativa inteira para o passado, mantendo o prazo coerente."""
    agora = timezone.now()
    ExamAttempt.objects.filter(pk=tentativa.pk).update(
        started_at=agora - timedelta(hours=2), expires_at=agora - timedelta(hours=1)
    )
    tentativa.refresh_from_db()
    return tentativa


# ---------------------------------------------------------------------------
# Envio valido
# ---------------------------------------------------------------------------


def test_envio_com_tudo_respondido(tentativa):
    responder_todas(tentativa)

    resultado = submit_attempt(tentativa)

    assert resultado.status == AttemptStatus.SUBMITTED
    assert resultado.submitted_at is not None
    assert resultado.expired_at is None


def test_envio_registra_auditoria(tentativa):
    responder_todas(tentativa)
    submit_attempt(tentativa)

    registros = AuditLog.objects.filter(event=AuditEvent.ATTEMPT_SUBMITTED)
    assert registros.count() == 1
    assert registros.first().metadata["attempt_number"] == 1


def test_envio_nao_apaga_respostas(tentativa):
    responder_todas(tentativa)
    antes = Answer.objects.filter(attempt_question__attempt=tentativa).count()

    submit_attempt(tentativa)

    assert (
        Answer.objects.filter(attempt_question__attempt=tentativa).count() == antes
    )


# ---------------------------------------------------------------------------
# Questoes obrigatorias
# ---------------------------------------------------------------------------


def test_envio_com_obrigatoria_em_branco_e_recusado(tentativa):
    with pytest.raises(ObrigatoriasPendentes) as erro:
        submit_attempt(tentativa)

    assert erro.value.numeros == [1, 2, 3, 4, 5]

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.IN_PROGRESS
    assert tentativa.submitted_at is None


def test_recusa_por_obrigatoria_nao_registra_evento(tentativa):
    with pytest.raises(ObrigatoriasPendentes):
        submit_attempt(tentativa)

    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_SUBMITTED).count() == 0


def test_a_recusa_lista_apenas_as_que_faltam(tentativa, tokens):
    """
    A lista precisa ser util: dizer "faltam questoes" sem dizer quais deixaria
    o aluno relendo a prova inteira com o cronometro correndo.
    """
    questao, alternativas = tokens[QuestionType.SINGLE_CHOICE]
    autosave_answer(tentativa, question_token=questao, option_tokens=[alternativas[0]])

    with pytest.raises(ObrigatoriasPendentes) as erro:
        submit_attempt(tentativa)

    assert 1 not in erro.value.numeros
    assert erro.value.numeros == [2, 3, 4, 5]


def test_apos_responder_o_envio_passa(tentativa):
    with pytest.raises(ObrigatoriasPendentes):
        submit_attempt(tentativa)

    responder_todas(tentativa)
    resultado = submit_attempt(tentativa)

    assert resultado.status == AttemptStatus.SUBMITTED


def test_texto_so_com_espaco_nao_conta_como_respondida(tentativa, tokens):
    """
    Espaco em branco nao e resposta.

    O strip serve apenas para decidir se ha conteudo; o texto gravado
    continua exatamente como o aluno digitou.
    """
    questao, _ = tokens[QuestionType.ESSAY]
    autosave_answer(tentativa, question_token=questao, text="   \n\n   ")

    with pytest.raises(ObrigatoriasPendentes) as erro:
        submit_attempt(tentativa)

    assert 5 in erro.value.numeros

    resposta = Answer.objects.get(attempt_question__public_token=questao)
    assert resposta.text_answer == "   \n\n   "


def test_alternativa_desmarcada_volta_a_contar_como_nao_respondida(
    tentativa, tokens
):
    questao, alternativas = tokens[QuestionType.SINGLE_CHOICE]
    autosave_answer(tentativa, question_token=questao, option_tokens=[alternativas[0]])
    autosave_answer(tentativa, question_token=questao, option_tokens=[])

    with pytest.raises(ObrigatoriasPendentes) as erro:
        submit_attempt(tentativa)

    assert 1 in erro.value.numeros


def test_questao_nao_obrigatoria_nao_bloqueia_o_envio(
    prova_aberta, aluno_matriculado
):
    from exams.models import Question
    from exams.services import start_attempt

    Question.objects.filter(exam=prova_aberta, type=QuestionType.ESSAY).update(
        required=False
    )
    tentativa = start_attempt(aluno_matriculado, prova_aberta)

    for linha in (
        AttemptQuestion.objects.filter(attempt=tentativa)
        .select_related("question")
        .prefetch_related("options")
    ):
        if linha.question.type == QuestionType.ESSAY:
            continue
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

    resultado = submit_attempt(tentativa)
    assert resultado.status == AttemptStatus.SUBMITTED


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------


def test_segundo_envio_nao_altera_nada(tentativa):
    """
    O duplo clique e o F5 na pagina de envio nao podem ser punidos.

    Nem submitted_at muda, nem um segundo evento entra na trilha.
    """
    responder_todas(tentativa)
    primeiro = submit_attempt(tentativa)
    momento = primeiro.submitted_at

    segundo = submit_attempt(tentativa)
    terceiro = submit_attempt(tentativa)

    assert segundo.submitted_at == momento
    assert terceiro.submitted_at == momento
    assert segundo.status == AttemptStatus.SUBMITTED
    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_SUBMITTED).count() == 1


def test_envio_em_tentativa_expirada_nao_a_transforma_em_enviada(tentativa):
    responder_todas(tentativa)
    expire_attempt(tentativa, agora=tentativa.expires_at)

    resultado = submit_attempt(tentativa)

    assert resultado.status == AttemptStatus.EXPIRED
    assert resultado.submitted_at is None
    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_SUBMITTED).count() == 0


# ---------------------------------------------------------------------------
# Envio depois do prazo
# ---------------------------------------------------------------------------


def test_envio_depois_do_prazo_expira_em_vez_de_enviar(tentativa):
    """
    O envio voluntario nao vence o relogio.

    Se o aluno clicou em finalizar depois do prazo, o que aconteceu foi que o
    tempo acabou. Gravar submitted_at diria que ele entregou dentro do prazo,
    o que e falso e apagaria a diferenca entre quem entregou e quem nao
    entregou.
    """
    responder_todas(tentativa)
    vencer_prazo(tentativa)

    resultado = submit_attempt(tentativa)

    assert resultado.status == AttemptStatus.EXPIRED
    assert resultado.expired_at is not None
    assert resultado.submitted_at is None


def test_expiracao_ignora_questao_obrigatoria_em_branco(tentativa):
    """
    O tempo acabou. Barrar a expiracao por falta de resposta deixaria a
    tentativa presa em IN_PROGRESS para sempre, e o aluno nao pode mais
    responder de qualquer forma.
    """
    vencer_prazo(tentativa)

    resultado = submit_attempt(tentativa)

    assert resultado.status == AttemptStatus.EXPIRED
    assert Answer.objects.filter(attempt_question__attempt=tentativa).count() == 0


def test_expiracao_preserva_o_que_ja_estava_salvo(tentativa, tokens):
    questao, _ = tokens[QuestionType.ESSAY]
    autosave_answer(tentativa, question_token=questao, text="Salvo antes do fim")
    vencer_prazo(tentativa)

    expire_attempt(tentativa)

    resposta = Answer.objects.get(attempt_question__public_token=questao)
    assert resposta.text_answer == "Salvo antes do fim"


def test_expiracao_registra_auditoria_uma_unica_vez(tentativa):
    vencer_prazo(tentativa)

    expire_attempt(tentativa)
    expire_attempt(tentativa)
    expire_attempt(tentativa)

    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_EXPIRED).count() == 1


def test_expirar_uma_tentativa_ja_enviada_nao_muda_nada(tentativa):
    responder_todas(tentativa)
    submit_attempt(tentativa)
    tentativa.refresh_from_db()
    momento = tentativa.submitted_at

    expire_attempt(tentativa)

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.SUBMITTED
    assert tentativa.submitted_at == momento
    assert tentativa.expired_at is None
    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_EXPIRED).count() == 0


# ---------------------------------------------------------------------------
# Corrida entre autosave e envio
# ---------------------------------------------------------------------------


def test_o_envio_trava_a_tentativa(tentativa):
    from django.test.utils import CaptureQueriesContext

    responder_todas(tentativa)

    with CaptureQueriesContext(connection) as consultas:
        submit_attempt(tentativa)

    sql = " ".join(consulta["sql"].upper() for consulta in consultas)
    assert "FOR UPDATE" in sql


def test_o_autosave_trava_a_tentativa(tentativa, tokens):
    from django.test.utils import CaptureQueriesContext

    questao, _ = tokens[QuestionType.ESSAY]

    with CaptureQueriesContext(connection) as consultas:
        autosave_answer(tentativa, question_token=questao, text="x")

    sql = " ".join(consulta["sql"].upper() for consulta in consultas)
    assert "FOR UPDATE" in sql


@pytest.mark.django_db(transaction=True)
def test_autosave_e_envio_simultaneos_nunca_gravam_depois_do_envio(
    prova_aberta, aluno_matriculado
):
    """
    A corrida real: um autosave em voo enquanto o aluno clica em finalizar.

    Os dois desfechos sao aceitaveis:

        autosave primeiro  a resposta entra e a prova e enviada com ela
        envio primeiro     o autosave acorda com a tentativa encerrada e recusa

    O que nao pode acontecer, em desfecho nenhum, e a resposta ser gravada
    depois de submitted_at. Como os dois travam a mesma linha de ExamAttempt,
    um deles necessariamente enxerga o estado que o outro deixou.
    """
    from exams.services import start_attempt
    from exams.services.attempt import TentativaNaoEditavel

    tentativa = start_attempt(aluno_matriculado, prova_aberta)
    responder_todas(tentativa)

    questao = AttemptQuestion.objects.filter(
        attempt=tentativa, question__type=QuestionType.ESSAY
    ).first()
    token = str(questao.public_token)

    def salvar():
        try:
            autosave_answer(tentativa, question_token=token, text="Ultimo instante")
            return "salvou"
        except TentativaNaoEditavel:
            return "recusado"
        finally:
            connection.close()

    def enviar():
        try:
            submit_attempt(tentativa)
            return "enviou"
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futuros = [executor.submit(salvar), executor.submit(enviar)]
        resultados = [futuro.result(timeout=30) for futuro in futuros]

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.SUBMITTED
    assert resultados[0] in {"salvou", "recusado"}

    resposta = Answer.objects.get(attempt_question=questao)
    if resultados[0] == "salvou":
        # Gravou antes do envio: o texto novo esta la, e saved_at e anterior
        # ao submitted_at.
        assert resposta.text_answer == "Ultimo instante"
        assert resposta.saved_at <= tentativa.submitted_at
    else:
        # O envio chegou primeiro. A resposta antiga permanece intacta.
        assert resposta.text_answer == "Resposta."

    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_SUBMITTED).count() == 1


@pytest.mark.django_db(transaction=True)
def test_dois_envios_simultaneos_produzem_um_unico_evento(
    prova_aberta, aluno_matriculado
):
    """O duplo clique real, com duas conexoes de verdade."""
    from exams.services import start_attempt

    tentativa = start_attempt(aluno_matriculado, prova_aberta)
    responder_todas(tentativa)

    def enviar():
        try:
            return submit_attempt(tentativa).submitted_at
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futuros = [executor.submit(enviar) for _ in range(2)]
        momentos = [futuro.result(timeout=30) for futuro in futuros]

    assert momentos[0] == momentos[1]
    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_SUBMITTED).count() == 1
