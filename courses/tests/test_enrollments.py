"""Testes de Enrollment e dos servicos de matricula."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from audit.models import AuditEvent, AuditLog
from common.exceptions import DomainError
from courses.models import Enrollment, EnrollmentStatus
from courses.services import (
    block_enrollment_access,
    complete_enrollment,
    create_enrollment,
    disable_enrollment,
    reactivate_enrollment,
    unblock_enrollment_access,
    validar_aluno,
)

pytestmark = pytest.mark.django_db


def contar(evento):
    return AuditLog.objects.filter(event=evento).count()


# ---------------------------------------------------------------------------
# Criacao de matricula
# ---------------------------------------------------------------------------


def test_create_enrollment_nasce_ativa_e_com_acesso_liberado(student_user, modulo):
    matricula = create_enrollment(student=student_user, module=modulo)

    matricula.refresh_from_db()
    assert matricula.student_id == student_user.pk
    assert matricula.module_id == modulo.pk
    assert matricula.status == EnrollmentStatus.ACTIVE
    assert matricula.access_enabled is True
    assert matricula.libera_acesso is True


def test_create_enrollment_gera_registro_de_auditoria(student_user, modulo, admin_user):
    matricula = create_enrollment(student=student_user, module=modulo, actor=admin_user)

    log = AuditLog.objects.get(event=AuditEvent.ENROLLMENT_CREATED)
    assert log.actor_id == admin_user.pk
    assert log.student_id == student_user.pk
    assert log.entity_type == "Enrollment"
    assert log.entity_id == str(matricula.pk)
    assert log.metadata["module_code"] == modulo.code


def test_matricular_administrador_e_recusado_e_nao_cria_matricula(admin_user, modulo):
    with pytest.raises(DomainError) as erro:
        create_enrollment(student=admin_user, module=modulo)

    assert "ALUNO" in str(erro.value)
    assert Enrollment.objects.count() == 0
    assert contar(AuditEvent.ENROLLMENT_CREATED) == 0


def test_validar_aluno_recusa_administrador(admin_user):
    with pytest.raises(DomainError):
        validar_aluno(admin_user)


def test_validar_aluno_recusa_nenhum_usuario():
    with pytest.raises(DomainError):
        validar_aluno(None)


def test_validar_aluno_devolve_o_proprio_aluno(student_user):
    assert validar_aluno(student_user) is student_user


def test_matricular_em_modulo_inativo_e_recusado(student_user, modulo_inativo):
    with pytest.raises(DomainError) as erro:
        create_enrollment(student=student_user, module=modulo_inativo)

    assert modulo_inativo.code in str(erro.value)
    assert Enrollment.objects.count() == 0


def test_matricular_sem_modulo_e_recusado(student_user):
    with pytest.raises(DomainError):
        create_enrollment(student=student_user, module=None)

    assert Enrollment.objects.count() == 0


def test_matricula_duplicada_ativa_e_recusada_e_mantem_uma_unica_linha(
    matricula, student_user, modulo
):
    with pytest.raises(DomainError) as erro:
        create_enrollment(student=student_user, module=modulo)

    assert "ja esta matriculado" in str(erro.value)
    assert Enrollment.objects.filter(student=student_user, module=modulo).count() == 1
    assert contar(AuditEvent.ENROLLMENT_CREATED) == 1


def test_matricula_duplicada_inativa_orienta_a_reativar_sem_criar_segunda_linha(
    matricula, student_user, modulo
):
    """
    Reativar precisa ser uma decisao consciente do administrador.

    Se a tentativa de matricular de novo reativasse sozinha, um clique de
    rotina desfaria em silencio um bloqueio academico deliberado.
    """
    disable_enrollment(matricula)

    with pytest.raises(DomainError) as erro:
        create_enrollment(student=student_user, module=modulo)

    assert "reativar" in str(erro.value).lower()
    assert Enrollment.objects.filter(student=student_user, module=modulo).count() == 1

    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.INACTIVE
    assert matricula.access_enabled is False


def test_banco_recusa_segunda_matricula_para_o_mesmo_par_aluno_e_modulo(
    matricula, student_user, modulo
):
    """
    A checagem no servico so produz mensagem legivel; a garantia de fato e a
    constraint do banco, que cobre bulk_create, shell e SQL direto.
    """
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Enrollment.objects.create(student=student_user, module=modulo)

    assert Enrollment.objects.filter(student=student_user, module=modulo).count() == 1


def test_mesmo_aluno_pode_ter_matriculas_em_modulos_diferentes(
    matricula, student_user, outro_modulo
):
    create_enrollment(student=student_user, module=outro_modulo)

    assert Enrollment.objects.filter(student=student_user).count() == 2


# ---------------------------------------------------------------------------
# Bloqueio e liberacao de acesso
# ---------------------------------------------------------------------------


def test_block_enrollment_access_bloqueia_sem_alterar_a_situacao_academica(
    matricula, admin_user
):
    """Bloquear as vesperas da prova e operacional: o aluno segue matriculado."""
    block_enrollment_access(matricula, actor=admin_user)

    matricula.refresh_from_db()
    assert matricula.access_enabled is False
    assert matricula.status == EnrollmentStatus.ACTIVE
    assert matricula.libera_acesso is False
    assert contar(AuditEvent.ENROLLMENT_BLOCKED) == 1


def test_block_enrollment_access_e_idempotente(matricula):
    block_enrollment_access(matricula)
    block_enrollment_access(matricula)

    assert contar(AuditEvent.ENROLLMENT_BLOCKED) == 1


def test_unblock_enrollment_access_libera_o_acesso_de_novo(matricula, admin_user):
    block_enrollment_access(matricula)
    unblock_enrollment_access(matricula, actor=admin_user)

    matricula.refresh_from_db()
    assert matricula.access_enabled is True
    assert matricula.status == EnrollmentStatus.ACTIVE
    assert contar(AuditEvent.ENROLLMENT_UNBLOCKED) == 1


def test_unblock_enrollment_access_e_idempotente(matricula):
    block_enrollment_access(matricula)
    unblock_enrollment_access(matricula)
    unblock_enrollment_access(matricula)

    assert contar(AuditEvent.ENROLLMENT_UNBLOCKED) == 1


def test_unblock_enrollment_access_nao_reativa_matricula_inativa(matricula):
    """
    Acesso e situacao academica sao chaves independentes: liberar o acesso
    nao pode ressuscitar uma matricula desativada.
    """
    disable_enrollment(matricula)
    unblock_enrollment_access(matricula)

    matricula.refresh_from_db()
    assert matricula.access_enabled is True
    assert matricula.status == EnrollmentStatus.INACTIVE
    assert matricula.libera_acesso is False


# ---------------------------------------------------------------------------
# Desativacao e reativacao
# ---------------------------------------------------------------------------


def test_disable_enrollment_desativa_e_bloqueia_o_acesso(matricula, admin_user):
    disable_enrollment(matricula, actor=admin_user)

    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.INACTIVE
    assert matricula.access_enabled is False
    assert matricula.libera_acesso is False
    assert contar(AuditEvent.ENROLLMENT_REMOVED) == 1


def test_disable_enrollment_nunca_apaga_a_linha_do_banco(matricula, student_user):
    """
    Remover matricula na interface e desativacao logica: apagar a linha
    destruiria o registro de que o aluno esteve matriculado.
    """
    pk = matricula.pk
    disable_enrollment(matricula)

    assert Enrollment.objects.filter(pk=pk).exists()
    assert Enrollment.objects.filter(student=student_user).count() == 1


def test_disable_enrollment_e_idempotente(matricula):
    disable_enrollment(matricula)
    disable_enrollment(matricula)

    assert contar(AuditEvent.ENROLLMENT_REMOVED) == 1


def test_reactivate_enrollment_volta_ao_estado_ativo_e_liberado(matricula, admin_user):
    disable_enrollment(matricula)
    reactivate_enrollment(matricula, actor=admin_user)

    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.ACTIVE
    assert matricula.access_enabled is True
    assert matricula.libera_acesso is True
    assert contar(AuditEvent.ENROLLMENT_REACTIVATED) == 1


def test_reactivate_enrollment_e_idempotente(matricula):
    disable_enrollment(matricula)
    reactivate_enrollment(matricula)
    reactivate_enrollment(matricula)

    assert contar(AuditEvent.ENROLLMENT_REACTIVATED) == 1


def test_reactivate_enrollment_em_matricula_ja_ativa_nao_registra_nada(matricula):
    reactivate_enrollment(matricula)

    assert contar(AuditEvent.ENROLLMENT_REACTIVATED) == 0


def test_reactivate_enrollment_com_modulo_inativo_e_recusado(matricula, modulo):
    disable_enrollment(matricula)
    modulo.is_active = False
    modulo.save(update_fields=["is_active"])
    matricula.refresh_from_db()

    with pytest.raises(DomainError) as erro:
        reactivate_enrollment(matricula)

    assert modulo.code in str(erro.value)

    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.INACTIVE
    assert matricula.access_enabled is False
    assert contar(AuditEvent.ENROLLMENT_REACTIVATED) == 0


def test_reactivate_enrollment_recusa_usuario_que_nao_e_aluno(admin_user, modulo):
    """
    Uma matricula de ADMIN so pode existir por escrita direta no banco; o
    servico nao pode devolve-la ao estado ativo.
    """
    matricula = Enrollment.objects.create(
        student=admin_user,
        module=modulo,
        status=EnrollmentStatus.INACTIVE,
        access_enabled=False,
    )

    with pytest.raises(DomainError):
        reactivate_enrollment(matricula)

    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.INACTIVE


# ---------------------------------------------------------------------------
# Conclusao
# ---------------------------------------------------------------------------


def test_complete_enrollment_marca_a_matricula_como_concluida(matricula, admin_user):
    complete_enrollment(matricula, actor=admin_user)

    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.COMPLETED
    assert matricula.libera_acesso is False
    assert contar(AuditEvent.ENROLLMENT_COMPLETED) == 1


def test_complete_enrollment_e_idempotente(matricula):
    complete_enrollment(matricula)
    complete_enrollment(matricula)

    assert contar(AuditEvent.ENROLLMENT_COMPLETED) == 1


# ---------------------------------------------------------------------------
# Validacao do modelo
# ---------------------------------------------------------------------------


def test_clean_recusa_matricula_de_usuario_com_papel_admin(admin_user, modulo):
    """
    O PostgreSQL nao consegue expressar esta regra em constraint; clean()
    cobre formularios e Django Admin, que nao passam pelo servico.
    """
    matricula = Enrollment(student=admin_user, module=modulo)

    with pytest.raises(ValidationError) as erro:
        matricula.clean()

    assert "student" in erro.value.message_dict


def test_clean_aceita_matricula_de_aluno(student_user, modulo):
    Enrollment(student=student_user, module=modulo).full_clean()

    assert Enrollment.objects.count() == 0


def test_str_da_matricula_mostra_aluno_e_codigo_do_modulo(
    matricula, student_user, modulo
):
    assert str(matricula) == "{} em {}".format(student_user.full_name, modulo.code)


# ---------------------------------------------------------------------------
# Criterio de liberacao de acesso
# ---------------------------------------------------------------------------


def aplicar_cenario(matricula, cenario):
    if cenario == "situacao_inativa":
        matricula.status = EnrollmentStatus.INACTIVE
        matricula.save(update_fields=["status"])
    elif cenario == "acesso_bloqueado":
        matricula.access_enabled = False
        matricula.save(update_fields=["access_enabled"])
    elif cenario == "modulo_inativo":
        matricula.module.is_active = False
        matricula.module.save(update_fields=["is_active"])


CENARIOS_DE_ACESSO = [
    ("tudo_liberado", True),
    ("situacao_inativa", False),
    ("acesso_bloqueado", False),
    ("modulo_inativo", False),
]


@pytest.mark.parametrize("cenario,esperado", CENARIOS_DE_ACESSO)
def test_libera_acesso_exige_as_tres_condicoes(matricula, cenario, esperado):
    aplicar_cenario(matricula, cenario)

    assert matricula.libera_acesso is esperado


@pytest.mark.parametrize("cenario,esperado", CENARIOS_DE_ACESSO)
def test_queryset_liberadas_usa_o_mesmo_criterio(matricula, cenario, esperado):
    aplicar_cenario(matricula, cenario)

    encontrada = Enrollment.objects.liberadas().filter(pk=matricula.pk).exists()
    assert encontrada is esperado


def test_queryset_do_aluno_nao_devolve_matricula_de_outro(
    matricula, student_user, outro_student, outro_modulo
):
    create_enrollment(student=outro_student, module=outro_modulo)

    do_aluno = Enrollment.objects.do_aluno(student_user)
    assert list(do_aluno.values_list("pk", flat=True)) == [matricula.pk]


# ---------------------------------------------------------------------------
# Trilha de auditoria das acoes de matricula
# ---------------------------------------------------------------------------


def test_todo_evento_de_matricula_registra_aluno_e_codigo_do_modulo(
    matricula, student_user, modulo, admin_user
):
    block_enrollment_access(matricula, actor=admin_user)
    unblock_enrollment_access(matricula, actor=admin_user)
    disable_enrollment(matricula, actor=admin_user)
    reactivate_enrollment(matricula, actor=admin_user)
    complete_enrollment(matricula, actor=admin_user)

    eventos = (
        AuditEvent.ENROLLMENT_CREATED,
        AuditEvent.ENROLLMENT_BLOCKED,
        AuditEvent.ENROLLMENT_UNBLOCKED,
        AuditEvent.ENROLLMENT_REMOVED,
        AuditEvent.ENROLLMENT_REACTIVATED,
        AuditEvent.ENROLLMENT_COMPLETED,
    )
    for evento in eventos:
        log = AuditLog.objects.get(event=evento)
        assert log.student_id == student_user.pk
        assert log.entity_type == "Enrollment"
        assert log.entity_id == str(matricula.pk)
        assert log.metadata["module_code"] == modulo.code
