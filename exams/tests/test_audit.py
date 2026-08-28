"""
Auditoria das operacoes de prova.

Dois eixos: o evento certo e gravado, e a metadata nao carrega nada que nao
possa ser guardado. Senha, hash da senha e gabarito nunca entram na trilha.
"""

import json

import pytest

from audit.models import AuditEvent, AuditLog
from exams.models import QuestionType
from exams.services import (
    close_exam,
    create_question,
    delete_question,
    duplicate_exam,
    publish_exam,
    remove_exam_password,
    set_exam_password,
    update_exam,
    update_question,
)

pytestmark = pytest.mark.django_db


def eventos(prova=None, evento=None):
    consulta = AuditLog.objects.all()
    if evento is not None:
        consulta = consulta.filter(event=evento)
    if prova is not None:
        consulta = consulta.filter(entity_id=str(prova.pk))
    return list(consulta)


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
# Eventos gravados
# ---------------------------------------------------------------------------


def test_criacao_registra_evento(prova, admin_user):
    registros = eventos(prova, AuditEvent.EXAM_CREATED)

    assert len(registros) == 1
    assert registros[0].actor_id == admin_user.pk
    assert registros[0].entity_type == "Exam"
    assert registros[0].metadata["version"] == 1


def test_edicao_registra_somente_os_campos_alterados(prova, admin_user):
    update_exam(prova, actor=admin_user, **dados_da_prova(prova, duration_minutes=90))

    registro = eventos(prova, AuditEvent.EXAM_UPDATED)[0]
    assert registro.metadata["changed_fields"] == ["duration_minutes"]


def test_edicao_sem_mudanca_nao_registra_nada(prova, admin_user):
    update_exam(prova, actor=admin_user, **dados_da_prova(prova))
    assert eventos(prova, AuditEvent.EXAM_UPDATED) == []


def test_publicacao_e_fechamento_registram(prova_pronta, admin_user):
    publish_exam(prova_pronta, actor=admin_user)
    close_exam(prova_pronta, actor=admin_user)

    assert len(eventos(prova_pronta, AuditEvent.EXAM_PUBLISHED)) == 1
    assert len(eventos(prova_pronta, AuditEvent.EXAM_CLOSED)) == 1


def test_duplicacao_registra_a_origem(prova_publicada, admin_user):
    copia = duplicate_exam(prova_publicada, actor=admin_user)

    registro = eventos(copia, AuditEvent.EXAM_DUPLICATED)[0]
    assert registro.metadata["source_exam_id"] == prova_publicada.pk
    assert registro.metadata["source_version"] == 1
    assert registro.metadata["new_version"] == 2


def test_questoes_registram_criacao_edicao_e_exclusao(prova, admin_user):
    questao = create_question(
        prova,
        type=QuestionType.ESSAY,
        text="Enunciado",
        points="2.00",
        actor=admin_user,
    )
    assert AuditLog.objects.filter(
        event=AuditEvent.QUESTION_CREATED, entity_id=str(questao.pk)
    ).exists()

    update_question(
        questao,
        type=QuestionType.ESSAY,
        text="Enunciado revisado",
        points="3.00",
        actor=admin_user,
    )
    assert AuditLog.objects.filter(
        event=AuditEvent.QUESTION_UPDATED, entity_id=str(questao.pk)
    ).exists()

    identificador = questao.pk
    delete_question(questao, actor=admin_user)
    assert AuditLog.objects.filter(
        event=AuditEvent.QUESTION_DELETED, entity_id=str(identificador)
    ).exists()


def test_exclusao_guarda_o_que_a_questao_era(prova, admin_user):
    """
    Depois do hard delete a linha nao existe mais. A trilha precisa preservar
    o suficiente para alguem entender o que sumiu.
    """
    questao = create_question(
        prova,
        type=QuestionType.SHORT_TEXT,
        text="Enunciado",
        points="2.50",
        order=3,
        actor=admin_user,
    )
    delete_question(questao, actor=admin_user)

    registro = AuditLog.objects.filter(event=AuditEvent.QUESTION_DELETED).first()
    assert registro.metadata["type"] == QuestionType.SHORT_TEXT
    assert registro.metadata["points"] == "2.50"
    assert registro.metadata["order"] == 3


def test_operacao_recusada_nao_registra_nada(prova_publicada, admin_user):
    from common.exceptions import DomainError

    antes = AuditLog.objects.count()

    with pytest.raises(DomainError):
        create_question(
            prova_publicada,
            type=QuestionType.ESSAY,
            text="Nao deveria entrar",
            points="1.00",
            actor=admin_user,
        )

    assert AuditLog.objects.count() == antes


# ---------------------------------------------------------------------------
# Senha na trilha
# ---------------------------------------------------------------------------


def test_troca_de_senha_registra_apenas_o_fato(prova, admin_user):
    senha = "Turma#Alpha2026"
    set_exam_password(prova, senha, actor=admin_user)
    prova.refresh_from_db()

    registro = eventos(prova, AuditEvent.EXAM_PASSWORD_CHANGED)[0]
    corpo = json.dumps(registro.metadata)

    assert registro.metadata.get("password_changed") in (True, "[REMOVIDO]")
    assert senha not in corpo
    assert prova.access_password_hash not in corpo


def test_nenhum_registro_contem_a_senha_da_prova(prova, admin_user):
    """
    Varredura na trilha inteira, e nao apenas no registro esperado.

    A sanitizacao de audit.services remove chaves sensiveis por substring, e
    este teste confirma que a protecao vale para as operacoes novas.
    """
    senha = "Turma#Alpha2026"
    set_exam_password(prova, senha, actor=admin_user)
    prova.refresh_from_db()
    remove_exam_password(prova, actor=admin_user)

    for registro in AuditLog.objects.all():
        corpo = json.dumps(registro.metadata)
        assert senha not in corpo


def test_remocao_de_senha_registra_evento(prova, admin_user):
    set_exam_password(prova, "Turma#Alpha2026", actor=admin_user)
    remove_exam_password(prova, actor=admin_user)

    assert len(eventos(prova, AuditEvent.EXAM_PASSWORD_REMOVED)) == 1


def test_remover_senha_inexistente_nao_registra(prova, admin_user):
    remove_exam_password(prova, actor=admin_user)
    assert eventos(prova, AuditEvent.EXAM_PASSWORD_REMOVED) == []


# ---------------------------------------------------------------------------
# Gabarito na trilha
# ---------------------------------------------------------------------------


def test_trilha_nao_guarda_o_gabarito(prova_pronta, admin_user):
    """
    Um AuditLog com o gabarito inteiro seria uma copia do gabarito num lugar
    que nao tem a protecao da tela de gabarito.
    """
    publish_exam(prova_pronta, actor=admin_user)

    for registro in AuditLog.objects.all():
        corpo = json.dumps(registro.metadata).lower()
        assert "is_correct" not in corpo
        assert "brasilia" not in corpo
        assert "apostila" not in corpo


def test_trilha_nao_guarda_o_enunciado_das_questoes(prova, admin_user):
    create_question(
        prova,
        type=QuestionType.ESSAY,
        text="Enunciado que nao deve ir para a trilha",
        points="1.00",
        actor=admin_user,
    )

    for registro in AuditLog.objects.all():
        corpo = json.dumps(registro.metadata)
        assert "nao deve ir para a trilha" not in corpo
