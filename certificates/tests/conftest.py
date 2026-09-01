"""
Apoio dos testes de certificado.

Todo certificado nasce de uma tentativa aprovada de verdade: o aluno responde,
envia, as objetivas corrigem sozinhas e o avaliador fecha as manuais. Montar
ExamAttempt com os campos ja preenchidos economizaria segundos e testaria
outra coisa — o objeto que eu construi, e nao o que o sistema produz.
"""

from decimal import Decimal

import pytest

from exams.models import AttemptResult, GradingStatus, QuestionType
from exams.services import (
    autosave_answer,
    finalize_grading,
    save_manual_grade,
    submit_attempt,
)

TEXTUAIS = {QuestionType.SHORT_TEXT, QuestionType.ESSAY}


def corretas(linha):
    return [
        alternativa
        for alternativa in linha.options.select_related("option").all()
        if alternativa.option.is_correct
    ]


def responder_tudo(tentativa, *, certo=True):
    """Responde todas as questoes; as objetivas certas ou erradas conforme pedido."""
    for linha in tentativa.questions.select_related("question").all():
        if linha.question.type in TEXTUAIS:
            autosave_answer(
                tentativa, question_token=str(linha.public_token), text="resposta"
            )
            continue

        escolhidas = corretas(linha)
        if not certo:
            erradas = [
                alternativa
                for alternativa in linha.options.select_related("option").all()
                if not alternativa.option.is_correct
            ]
            escolhidas = erradas[:1]
        autosave_answer(
            tentativa,
            question_token=str(linha.public_token),
            option_tokens=[str(o.public_token) for o in escolhidas],
        )


def corrigir_manuais(tentativa, admin_user, *, cheio=True):
    """Avalia as questoes de texto e fecha a correcao."""
    for linha in tentativa.questions.select_related("question").all():
        if linha.question.type not in TEXTUAIS:
            continue
        valor = linha.points_snapshot if cheio else Decimal("0.00")
        save_manual_grade(
            tentativa,
            # PK da AttemptQuestion — a linha da tentativa —, e nao da Question.
            # Os dois numeros costumam coincidir em base pequena, e foi assim
            # que este engano passou despercebido uma vez.
            question_id=linha.pk,
            points=valor,
            actor=admin_user,
        )
    return finalize_grading(tentativa, actor=admin_user)


@pytest.fixture
def tentativa_aprovada(tentativa, admin_user):
    """Tentativa corrigida com nota cheia: GRADED + APPROVED."""
    responder_tudo(tentativa, certo=True)
    enviada = submit_attempt(tentativa)
    fechada = corrigir_manuais(enviada, admin_user, cheio=True)

    assert fechada.grading_status == GradingStatus.GRADED
    assert fechada.result == AttemptResult.APPROVED
    return fechada


@pytest.fixture
def tentativa_reprovada(tentativa, admin_user):
    """Tentativa corrigida e reprovada: nada certo, nenhum ponto manual."""
    responder_tudo(tentativa, certo=False)
    enviada = submit_attempt(tentativa)
    fechada = corrigir_manuais(enviada, admin_user, cheio=False)

    assert fechada.grading_status == GradingStatus.GRADED
    assert fechada.result == AttemptResult.FAILED
    return fechada


@pytest.fixture
def certificado(tentativa_aprovada, admin_user):
    from certificates.services import issue_certificate

    documento, criado = issue_certificate(tentativa_aprovada, actor=admin_user)
    assert criado
    return documento
