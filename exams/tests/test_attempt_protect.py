"""
O que nao pode mais ser apagado depois que alguem faz a prova.

Uma tentativa e o registro do que um aluno respondeu numa avaliacao. Apagar a
prova, a questao, a alternativa ou o proprio aluno deixaria esse registro
apontando para o vazio — e o que sobra de uma prova sem as questoes dela nao
descreve nada.

Todas as FKs historicas sao PROTECT, e este arquivo cobre as quatro. O
contraponto tambem esta aqui: apagar a tentativa continua funcionando e leva
junto o que so existe dentro dela.
"""

import pytest
from django.db import transaction
from django.db.models import ProtectedError

from exams.models import (
    Answer,
    AnswerOption,
    AttemptOption,
    AttemptQuestion,
    ExamAttempt,
    QuestionType,
)
from exams.services import autosave_answer

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# O que a tentativa protege
# ---------------------------------------------------------------------------


def test_prova_com_tentativa_nao_pode_ser_apagada(tentativa, prova_aberta):
    with pytest.raises(ProtectedError):
        with transaction.atomic():
            prova_aberta.delete()

    assert ExamAttempt.objects.filter(pk=tentativa.pk).exists()


def test_questao_com_tentativa_nao_pode_ser_apagada(tentativa, prova_aberta):
    questao = prova_aberta.questions.first()

    with pytest.raises(ProtectedError):
        with transaction.atomic():
            questao.delete()

    assert prova_aberta.questions.filter(pk=questao.pk).exists()


def test_alternativa_com_tentativa_nao_pode_ser_apagada(tentativa, prova_aberta):
    alternativa = (
        prova_aberta.questions.get(type=QuestionType.SINGLE_CHOICE).options.first()
    )

    with pytest.raises(ProtectedError):
        with transaction.atomic():
            alternativa.delete()


def test_aluno_com_tentativa_nao_pode_ser_apagado(tentativa, aluno_matriculado):
    """
    O historico academico sobrevive ao cadastro.

    Se apagar o usuario apagasse as tentativas, um aluno removido por engano
    levaria junto a prova que ele fez — e nao ha como reconstruir isso.
    Bloquear o aluno continua funcionando normalmente: bloqueio e outra coisa,
    e nao mexe em linha nenhuma de tentativa.
    """
    with pytest.raises(ProtectedError):
        with transaction.atomic():
            aluno_matriculado.delete()

    assert ExamAttempt.objects.filter(pk=tentativa.pk).exists()


def test_o_protectederror_da_prova_aponta_a_tentativa(tentativa, prova_aberta):
    with pytest.raises(ProtectedError) as erro:
        with transaction.atomic():
            prova_aberta.delete()

    bloqueadores = {type(objeto).__name__ for objeto in erro.value.protected_objects}
    assert "ExamAttempt" in bloqueadores


# ---------------------------------------------------------------------------
# O que a tentativa leva junto quando ela mesma e apagada
# ---------------------------------------------------------------------------


def test_apagar_a_tentativa_leva_questoes_alternativas_e_respostas(tentativa, tokens):
    """
    O outro lado: AttemptQuestion, AttemptOption, Answer e AnswerOption so
    existem dentro de uma tentativa, entao sao CASCADE.

    Isso mantem a excecao administrativa possivel — remover uma tentativa
    criada por engano nao exige limpar quatro tabelas na mao — sem abrir
    nenhuma porta para apagar prova, questao ou aluno.
    """
    questao, alternativas = tokens[QuestionType.SINGLE_CHOICE]
    autosave_answer(tentativa, question_token=questao, option_tokens=[alternativas[0]])

    assert Answer.objects.filter(attempt_question__attempt=tentativa).count() == 1
    assert AnswerOption.objects.count() == 1

    tentativa.delete()

    assert AttemptQuestion.objects.filter(attempt_id=tentativa.pk).count() == 0
    assert AttemptOption.objects.count() == 0
    assert Answer.objects.count() == 0
    assert AnswerOption.objects.count() == 0


def test_apagar_a_tentativa_nao_toca_na_prova(tentativa, prova_aberta):
    questoes_antes = prova_aberta.questions.count()

    tentativa.delete()

    prova_aberta.refresh_from_db()
    assert prova_aberta.questions.count() == questoes_antes
