"""
Publicacao e fechamento.

Cada motivo de recusa tem um teste proprio, e nao apenas um caso geral: o que
importa e que a mensagem certa chegue ao administrador para cada problema, e
que nenhum deles passe despercebido.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from audit.models import AuditEvent, AuditLog
from common.exceptions import DomainError
from exams.models import Exam, ExamStatus, QuestionOption, QuestionType
from exams.services import (
    close_exam,
    create_exam,
    create_question,
    erros_para_publicacao,
    publish_exam,
)

pytestmark = pytest.mark.django_db


def mensagens(erro):
    return getattr(erro.value, "mensagens", [str(erro.value)])


# ---------------------------------------------------------------------------
# Caminho feliz
# ---------------------------------------------------------------------------


def test_prova_valida_publica(prova_pronta, admin_user):
    antes = timezone.now()
    prova = publish_exam(prova_pronta, actor=admin_user)
    prova.refresh_from_db()

    assert prova.status == ExamStatus.PUBLISHED
    assert prova.published_at is not None
    assert prova.published_at >= antes
    assert prova.closed_at is None


def test_publicacao_congela_o_total_de_pontos(prova_pronta, admin_user):
    assert prova_pronta.total_points == Decimal("0.00")

    publish_exam(prova_pronta, actor=admin_user)
    prova_pronta.refresh_from_db()

    assert prova_pronta.total_points == Decimal("10.00")


def test_total_de_pontos_e_decimal_exato(prova_pronta, admin_user):
    """
    2.50 + 3.25 + 1.25 + 1.50 + 1.50.

    Em float o resultado seria 9.999999999999998. O teste existe para travar
    o uso de Decimal em toda a cadeia: campo, soma e snapshot.
    """
    publish_exam(prova_pronta, actor=admin_user)
    prova_pronta.refresh_from_db()

    assert prova_pronta.total_points == Decimal("10.00")
    assert str(prova_pronta.total_points) == "10.00"
    assert isinstance(prova_pronta.total_points, Decimal)


def test_snapshot_nao_muda_se_a_soma_das_questoes_mudar_depois(
    prova_pronta, admin_user
):
    """
    A escala historica da prova.

    Uma correcao feita daqui a um ano precisa dividir pelo total que valia no
    dia da aplicacao. Alterar pontos por fora nao pode reescrever isso.
    """
    publish_exam(prova_pronta, actor=admin_user)
    prova_pronta.refresh_from_db()
    congelado = prova_pronta.total_points

    prova_pronta.questions.filter(order=1).update(points=Decimal("99.00"))
    prova_pronta.refresh_from_db()

    assert prova_pronta.total_points == congelado
    assert prova_pronta.pontos_vigentes == congelado


def test_publicacao_registra_auditoria(prova_pronta, admin_user):
    publish_exam(prova_pronta, actor=admin_user)

    registro = AuditLog.objects.filter(
        event=AuditEvent.EXAM_PUBLISHED, entity_id=str(prova_pronta.pk)
    ).first()

    assert registro is not None
    assert registro.actor_id == admin_user.pk
    assert registro.metadata["total_points"] == "10.00"
    assert registro.metadata["questions"] == 5


def test_prova_sem_pendencias_reporta_lista_vazia(prova_pronta):
    assert erros_para_publicacao(prova_pronta) == []


# ---------------------------------------------------------------------------
# Cada motivo de recusa
# ---------------------------------------------------------------------------


def test_nao_publica_com_modulo_inativo(prova_pronta, admin_user):
    prova_pronta.module.is_active = False
    prova_pronta.module.save(update_fields=["is_active"])

    with pytest.raises(DomainError) as erro:
        publish_exam(prova_pronta, actor=admin_user)

    assert any("inativo" in m.lower() for m in mensagens(erro))
    prova_pronta.refresh_from_db()
    assert prova_pronta.status == ExamStatus.DRAFT


def test_nao_publica_sem_abertura(prova_pronta, admin_user):
    Exam.objects.filter(pk=prova_pronta.pk).update(open_at=None)
    prova_pronta.refresh_from_db()

    with pytest.raises(DomainError) as erro:
        publish_exam(prova_pronta, actor=admin_user)
    assert any("abertura" in m.lower() for m in mensagens(erro))


def test_nao_publica_sem_encerramento(prova_pronta, admin_user):
    Exam.objects.filter(pk=prova_pronta.pk).update(close_at=None)
    prova_pronta.refresh_from_db()

    with pytest.raises(DomainError) as erro:
        publish_exam(prova_pronta, actor=admin_user)
    assert any("encerramento" in m.lower() for m in mensagens(erro))


def test_nao_publica_sem_duracao(prova_pronta, admin_user):
    Exam.objects.filter(pk=prova_pronta.pk).update(duration_minutes=None)
    prova_pronta.refresh_from_db()

    with pytest.raises(DomainError) as erro:
        publish_exam(prova_pronta, actor=admin_user)
    assert any("duracao" in m.lower() for m in mensagens(erro))


def test_nao_publica_sem_questao_ativa(prova, admin_user):
    with pytest.raises(DomainError) as erro:
        publish_exam(prova, actor=admin_user)
    assert any("nenhuma questao" in m.lower() for m in mensagens(erro))


def test_nao_publica_com_todas_as_questoes_inativas(prova_pronta, admin_user):
    prova_pronta.questions.update(active=False)

    with pytest.raises(DomainError) as erro:
        publish_exam(prova_pronta, actor=admin_user)
    assert any("nenhuma questao" in m.lower() for m in mensagens(erro))


def test_nao_publica_com_escolha_unica_sem_correta(prova_pronta, admin_user):
    questao = prova_pronta.questions.get(type=QuestionType.SINGLE_CHOICE)
    questao.options.update(is_correct=False)

    with pytest.raises(DomainError) as erro:
        publish_exam(prova_pronta, actor=admin_user)
    assert any("correta" in m.lower() for m in mensagens(erro))


def test_nao_publica_com_escolha_unica_com_duas_corretas(prova_pronta, admin_user):
    questao = prova_pronta.questions.get(type=QuestionType.SINGLE_CHOICE)
    questao.options.update(is_correct=True)

    with pytest.raises(DomainError) as erro:
        publish_exam(prova_pronta, actor=admin_user)
    assert any("escolha unica" in m.lower() for m in mensagens(erro))


def test_nao_publica_com_multipla_toda_correta(prova_pronta, admin_user):
    questao = prova_pronta.questions.get(type=QuestionType.MULTIPLE_CHOICE)
    questao.options.update(is_correct=True)

    with pytest.raises(DomainError) as erro:
        publish_exam(prova_pronta, actor=admin_user)
    assert any("todas as alternativas" in m.lower() for m in mensagens(erro))


def test_nao_publica_com_verdadeiro_falso_sem_correta(prova_pronta, admin_user):
    questao = prova_pronta.questions.get(type=QuestionType.TRUE_FALSE)
    questao.options.update(is_correct=False)

    with pytest.raises(DomainError) as erro:
        publish_exam(prova_pronta, actor=admin_user)
    assert any("exatamente uma" in m.lower() for m in mensagens(erro))


@pytest.mark.parametrize(
    "tipo", [QuestionType.SHORT_TEXT, QuestionType.ESSAY]
)
def test_nao_publica_com_correcao_manual_que_tem_alternativa(
    prova_pronta, admin_user, tipo
):
    questao = prova_pronta.questions.get(type=tipo)
    QuestionOption.objects.create(question=questao, text="Intrusa", order=1)

    with pytest.raises(DomainError) as erro:
        publish_exam(prova_pronta, actor=admin_user)
    assert any("correcao manual" in m.lower() for m in mensagens(erro))


def test_recusa_lista_todos_os_problemas_de_uma_vez(prova_pronta, admin_user):
    """
    O administrador precisa ver a lista inteira, e nao descobrir os erros um
    a um a cada tentativa de publicar.
    """
    Exam.objects.filter(pk=prova_pronta.pk).update(
        open_at=None, close_at=None, duration_minutes=None
    )
    prova_pronta.refresh_from_db()
    prova_pronta.questions.get(type=QuestionType.SINGLE_CHOICE).options.update(
        is_correct=False
    )

    with pytest.raises(DomainError) as erro:
        publish_exam(prova_pronta, actor=admin_user)

    lista = mensagens(erro)
    assert len(lista) >= 4


def test_publicacao_recusada_nao_grava_nada(prova_pronta, admin_user):
    """A transacao inteira e desfeita: nao existe prova meio publicada."""
    Exam.objects.filter(pk=prova_pronta.pk).update(duration_minutes=None)
    prova_pronta.refresh_from_db()

    with pytest.raises(DomainError):
        publish_exam(prova_pronta, actor=admin_user)

    prova_pronta.refresh_from_db()
    assert prova_pronta.status == ExamStatus.DRAFT
    assert prova_pronta.published_at is None
    assert prova_pronta.total_points == Decimal("0.00")
    assert not AuditLog.objects.filter(
        event=AuditEvent.EXAM_PUBLISHED, entity_id=str(prova_pronta.pk)
    ).exists()


def test_nao_publica_prova_ja_publicada(prova_publicada, admin_user):
    with pytest.raises(DomainError) as erro:
        publish_exam(prova_publicada, actor=admin_user)
    assert "ja esta publicada" in str(erro.value).lower()


def test_nao_republica_prova_fechada(prova_publicada, admin_user):
    close_exam(prova_publicada, actor=admin_user)

    with pytest.raises(DomainError) as erro:
        publish_exam(prova_publicada, actor=admin_user)
    assert "fechada" in str(erro.value).lower()


# ---------------------------------------------------------------------------
# Fechamento
# ---------------------------------------------------------------------------


def test_fechar_prova_publicada(prova_publicada, admin_user):
    antes = timezone.now()
    close_exam(prova_publicada, actor=admin_user)
    prova_publicada.refresh_from_db()

    assert prova_publicada.status == ExamStatus.CLOSED
    assert prova_publicada.closed_at is not None
    assert prova_publicada.closed_at >= antes


def test_fechamento_nao_exclui_nada(prova_publicada, admin_user):
    questoes = prova_publicada.questions.count()
    opcoes = QuestionOption.objects.filter(question__exam=prova_publicada).count()

    close_exam(prova_publicada, actor=admin_user)

    assert prova_publicada.questions.count() == questoes
    assert (
        QuestionOption.objects.filter(question__exam=prova_publicada).count() == opcoes
    )


def test_fechamento_preserva_o_total_de_pontos(prova_publicada, admin_user):
    close_exam(prova_publicada, actor=admin_user)
    prova_publicada.refresh_from_db()
    assert prova_publicada.total_points == Decimal("10.00")


def test_fechamento_registra_auditoria(prova_publicada, admin_user):
    close_exam(prova_publicada, actor=admin_user)

    assert AuditLog.objects.filter(
        event=AuditEvent.EXAM_CLOSED, entity_id=str(prova_publicada.pk)
    ).exists()


def test_nao_fecha_rascunho(prova_pronta, admin_user):
    with pytest.raises(DomainError) as erro:
        close_exam(prova_pronta, actor=admin_user)
    assert "rascunho" in str(erro.value).lower()


def test_nao_fecha_duas_vezes(prova_publicada, admin_user):
    close_exam(prova_publicada, actor=admin_user)
    with pytest.raises(DomainError) as erro:
        close_exam(prova_publicada, actor=admin_user)
    assert "ja esta fechada" in str(erro.value).lower()


# ---------------------------------------------------------------------------
# Desempenho da validacao
# ---------------------------------------------------------------------------


def test_validacao_nao_faz_uma_consulta_por_questao(
    prova, admin_user, django_assert_max_num_queries
):
    """
    O prefetch e o que impede a validacao de uma prova de trinta questoes
    virar mais de trinta consultas.
    """
    for indice in range(20):
        create_question(
            prova,
            type=QuestionType.SINGLE_CHOICE,
            text="Questao {}".format(indice),
            points="1.00",
            order=indice + 1,
            opcoes=[
                {"text": "A", "is_correct": True},
                {"text": "B", "is_correct": False},
                {"text": "C", "is_correct": False},
            ],
            actor=admin_user,
        )

    with django_assert_max_num_queries(8):
        erros_para_publicacao(prova)
