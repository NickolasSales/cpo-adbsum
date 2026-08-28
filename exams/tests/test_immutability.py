"""
Imutabilidade da prova publicada e fechada.

Todos os testes atacam pela camada de servico, e nao pela tela. O requisito
foi explicito: nao basta o botao sumir. Se um servico aceitasse a operacao,
qualquer caminho novo — um comando de gestao, um script de correcao em massa,
uma view futura — quebraria o historico sem ninguem perceber.
"""

from decimal import Decimal

import pytest

from common.exceptions import DomainError
from exams.models import ExamStatus, QuestionType
from exams.services import (
    close_exam,
    create_question,
    delete_question,
    remove_exam_password,
    reorder_questions,
    set_exam_password,
    update_exam,
    update_question,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def prova_fechada(prova_publicada, admin_user):
    return close_exam(prova_publicada, actor=admin_user)


def dados_da_prova(prova, **trocas):
    dados = {
        "module": prova.module,
        "title": prova.title,
        "description": prova.description,
        "instructions": prova.instructions,
        "open_at": prova.open_at,
        "close_at": prova.close_at,
        "duration_minutes": prova.duration_minutes,
        "passing_score": prova.passing_score,
        "max_attempts": prova.max_attempts,
        "failure_message": prova.failure_message,
        "randomize_questions": prova.randomize_questions,
        "randomize_options": prova.randomize_options,
        "show_score_after_submission": prova.show_score_after_submission,
    }
    dados.update(trocas)
    return dados


# ---------------------------------------------------------------------------
# Prova publicada
# ---------------------------------------------------------------------------


def test_publicada_nao_aceita_edicao_da_configuracao(prova_publicada, admin_user):
    with pytest.raises(DomainError) as erro:
        update_exam(
            prova_publicada,
            actor=admin_user,
            **dados_da_prova(prova_publicada, duration_minutes=999),
        )
    assert "rascunho" in str(erro.value).lower()

    prova_publicada.refresh_from_db()
    assert prova_publicada.duration_minutes == 60


def test_publicada_nao_aceita_troca_de_modulo(prova_publicada, outro_modulo, admin_user):
    modulo_original = prova_publicada.module_id

    with pytest.raises(DomainError):
        update_exam(
            prova_publicada,
            actor=admin_user,
            **dados_da_prova(prova_publicada, module=outro_modulo),
        )

    prova_publicada.refresh_from_db()
    assert prova_publicada.module_id == modulo_original


def test_publicada_nao_aceita_questao_nova(prova_publicada, admin_user):
    quantas = prova_publicada.questions.count()

    with pytest.raises(DomainError) as erro:
        create_question(
            prova_publicada,
            type=QuestionType.ESSAY,
            text="Questao infiltrada",
            points="1.00",
            actor=admin_user,
        )
    assert "publicada" in str(erro.value).lower()
    assert prova_publicada.questions.count() == quantas


def test_publicada_nao_aceita_edicao_de_questao(prova_publicada, admin_user):
    questao = prova_publicada.questions.first()
    texto_original = questao.text

    with pytest.raises(DomainError):
        update_question(
            questao,
            type=questao.type,
            text="Enunciado adulterado",
            points=questao.points,
            actor=admin_user,
        )

    questao.refresh_from_db()
    assert questao.text == texto_original


def test_publicada_nao_aceita_mudanca_de_pontos(prova_publicada, admin_user):
    questao = prova_publicada.questions.first()
    pontos_originais = questao.points

    with pytest.raises(DomainError):
        update_question(
            questao,
            type=questao.type,
            text=questao.text,
            points=Decimal("99.00"),
            actor=admin_user,
        )

    questao.refresh_from_db()
    assert questao.points == pontos_originais


def test_publicada_nao_aceita_mudanca_de_gabarito(prova_publicada, admin_user):
    questao = prova_publicada.questions.get(type=QuestionType.SINGLE_CHOICE)
    gabarito_original = set(
        questao.options.filter(is_correct=True).values_list("pk", flat=True)
    )

    with pytest.raises(DomainError):
        update_question(
            questao,
            type=questao.type,
            text=questao.text,
            points=questao.points,
            opcoes=[
                {"text": "Brasilia", "is_correct": False},
                {"text": "Rio de Janeiro", "is_correct": True},
                {"text": "Salvador", "is_correct": False},
            ],
            actor=admin_user,
        )

    questao.refresh_from_db()
    assert (
        set(questao.options.filter(is_correct=True).values_list("pk", flat=True))
        == gabarito_original
    )


def test_publicada_nao_aceita_exclusao_de_questao(prova_publicada, admin_user):
    questao = prova_publicada.questions.first()

    with pytest.raises(DomainError) as erro:
        delete_question(questao, actor=admin_user)
    assert "publicada" in str(erro.value).lower()

    questao.refresh_from_db()
    assert questao.pk is not None


def test_publicada_nao_aceita_reordenacao(prova_publicada, admin_user):
    questao = prova_publicada.questions.first()
    ordem_original = questao.order

    with pytest.raises(DomainError):
        reorder_questions(prova_publicada, {questao.pk: 99}, actor=admin_user)

    questao.refresh_from_db()
    assert questao.order == ordem_original


def test_publicada_aceita_troca_de_senha(prova_publicada, admin_user):
    """
    Excecao deliberada.

    A senha e operacional, nao estrutural: se vazar as vesperas da aplicacao,
    trocar precisa ser possivel sem invalidar a prova. Nao toca em questao,
    gabarito nem pontuacao, entao nao ameaca o historico.
    """
    set_exam_password(prova_publicada, "NovaSenha#2026", actor=admin_user)
    prova_publicada.refresh_from_db()

    assert prova_publicada.tem_senha is True
    assert prova_publicada.status == ExamStatus.PUBLISHED
    assert prova_publicada.total_points == Decimal("10.00")


# ---------------------------------------------------------------------------
# Prova fechada
# ---------------------------------------------------------------------------


def test_fechada_nao_aceita_edicao(prova_fechada, admin_user):
    with pytest.raises(DomainError) as erro:
        update_exam(
            prova_fechada, actor=admin_user, **dados_da_prova(prova_fechada, title="Outro")
        )
    assert "rascunho" in str(erro.value).lower()


def test_fechada_nao_aceita_questao_nova(prova_fechada, admin_user):
    with pytest.raises(DomainError) as erro:
        create_question(
            prova_fechada,
            type=QuestionType.ESSAY,
            text="Nova",
            points="1.00",
            actor=admin_user,
        )
    assert "fechada" in str(erro.value).lower()


def test_fechada_nao_aceita_exclusao_de_questao(prova_fechada, admin_user):
    questao = prova_fechada.questions.first()
    with pytest.raises(DomainError):
        delete_question(questao, actor=admin_user)


def test_fechada_nao_aceita_troca_de_senha(prova_fechada, admin_user):
    with pytest.raises(DomainError) as erro:
        set_exam_password(prova_fechada, "Qualquer#2026", actor=admin_user)
    assert "fechada" in str(erro.value).lower()


def test_fechada_nao_aceita_remocao_de_senha(prova_publicada, admin_user):
    set_exam_password(prova_publicada, "Senha#Antes2026", actor=admin_user)
    close_exam(prova_publicada, actor=admin_user)

    with pytest.raises(DomainError):
        remove_exam_password(prova_publicada, actor=admin_user)


# ---------------------------------------------------------------------------
# Rascunho continua editavel
# ---------------------------------------------------------------------------


def test_rascunho_aceita_tudo(prova_pronta, admin_user, outro_modulo):
    """Contraprova: sem ela, os testes acima passariam com tudo bloqueado."""
    update_exam(
        prova_pronta,
        actor=admin_user,
        **dados_da_prova(prova_pronta, duration_minutes=90, module=outro_modulo),
    )
    prova_pronta.refresh_from_db()
    assert prova_pronta.duration_minutes == 90
    assert prova_pronta.module_id == outro_modulo.pk

    questao = prova_pronta.questions.get(type=QuestionType.ESSAY)
    update_question(
        questao,
        type=QuestionType.ESSAY,
        text="Enunciado novo",
        points="2.00",
        actor=admin_user,
    )
    questao.refresh_from_db()
    assert questao.text == "Enunciado novo"

    delete_question(questao, actor=admin_user)
    assert prova_pronta.questions.filter(type=QuestionType.ESSAY).count() == 0


def test_estrutura_editavel_reflete_o_estado(prova_pronta, prova_publicada):
    assert prova_publicada.estrutura_editavel is False
    assert prova_publicada.e_publicada is True
