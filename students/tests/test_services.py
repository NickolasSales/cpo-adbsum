"""Testes dos servicos de dominio de alunos e do StudentProfile."""

import pytest

from accounts.models import User, UserRole
from audit.models import AuditEvent, AuditLog
from common.exceptions import DomainError
from students.models import StudentProfile, StudentSource
from students.services import (
    MENSAGEM_SENHA_NAO_CONFIGURADA,
    alunos_queryset,
    block_student,
    create_student,
    obter_ou_criar_perfil,
    senha_inicial_configurada,
    unblock_student,
    update_student,
)

pytestmark = pytest.mark.django_db


def eventos(evento, **filtros):
    return AuditLog.objects.filter(event=evento, **filtros)


def aluno_sem_perfil(email="sem.perfil@exemplo.test", nome="Aluno Sem Perfil"):
    """Conta de aluno nascida fora do servico, como pelo shell."""
    return User.objects.create_user(
        email=email,
        full_name=nome,
        password="Qualquer#Senha2026",
        role=UserRole.STUDENT,
    )


# ---------------------------------------------------------------------------
# create_student
# ---------------------------------------------------------------------------


def test_create_student_cria_usuario_e_perfil_na_mesma_operacao():
    aluno = create_student(full_name="Ana Prado", email="ana.prado@exemplo.test")

    assert aluno.role == UserRole.STUDENT
    assert aluno.is_active is True
    # Sem password explicito, cai na senha padrao do ambiente — e o caminho
    # da importacao em lote. A troca obrigatoria deixou de existir.
    assert aluno.must_change_password is False

    perfil = StudentProfile.objects.get(user=aluno)
    assert perfil.source == StudentSource.MANUAL
    assert perfil.notes == ""


def test_create_student_grava_as_observacoes_e_a_origem_informadas():
    aluno = create_student(
        full_name="Bruno Lima",
        email="bruno.lima@exemplo.test",
        notes="Turma da noite",
        source=StudentSource.IMPORT,
    )

    perfil = StudentProfile.objects.get(user=aluno)
    assert perfil.notes == "Turma da noite"
    assert perfil.source == StudentSource.IMPORT


def test_senha_do_novo_aluno_e_a_padrao_com_hash_pbkdf2(senha_padrao):
    aluno = create_student(full_name="Caio Reis", email="caio.reis@exemplo.test")

    gravado = User.objects.get(pk=aluno.pk)
    assert gravado.check_password(senha_padrao) is True
    assert gravado.password.startswith("pbkdf2_")


def test_senha_em_texto_puro_nao_fica_no_campo_password(senha_padrao):
    """O campo password guarda apenas o hash; o texto puro nunca e persistido."""
    aluno = create_student(full_name="Dora Melo", email="dora.melo@exemplo.test")

    gravado = User.objects.get(pk=aluno.pk)
    assert senha_padrao not in gravado.password


def test_sem_senha_padrao_configurada_nenhum_aluno_e_criado(settings):
    settings.DEFAULT_STUDENT_PASSWORD = ""
    antes = User.objects.count()

    with pytest.raises(DomainError) as erro:
        create_student(full_name="Elias Nunes", email="elias.nunes@exemplo.test")

    assert str(erro.value) == MENSAGEM_SENHA_NAO_CONFIGURADA
    assert User.objects.count() == antes
    assert User.objects.filter(email="elias.nunes@exemplo.test").exists() is False


def test_senha_inicial_configurada_devolve_true_quando_ha_senha():
    assert senha_inicial_configurada() is True


@pytest.mark.parametrize("valor", ["", "   ", None])
def test_senha_inicial_configurada_devolve_false_quando_nao_ha_senha(settings, valor):
    settings.DEFAULT_STUDENT_PASSWORD = valor
    assert senha_inicial_configurada() is False


def test_email_duplicado_e_recusado(student_user):
    with pytest.raises(DomainError) as erro:
        create_student(full_name="Outro Joao", email=student_user.email)

    assert "Ja existe um aluno cadastrado com este e-mail." in str(erro.value)
    assert User.objects.filter(email=student_user.email).count() == 1


def test_email_duplicado_com_caixa_diferente_e_recusado(student_user):
    """Maiusculas nao criam uma segunda conta: e o mesmo e-mail."""
    with pytest.raises(DomainError):
        create_student(full_name="Outro Joao", email="JOAO.ALUNO@EXEMPLO.TEST")

    assert User.objects.filter(email="joao.aluno@exemplo.test").count() == 1


def test_email_de_admin_nao_vira_aluno(admin_user):
    with pytest.raises(DomainError) as erro:
        create_student(full_name="Falso Aluno", email=admin_user.email)

    assert "administrador" in str(erro.value)

    admin_user.refresh_from_db()
    assert admin_user.role == UserRole.ADMIN
    assert StudentProfile.objects.filter(user=admin_user).exists() is False


@pytest.mark.parametrize("nome", ["", "   ", None])
def test_nome_vazio_e_recusado(nome):
    antes = User.objects.count()

    with pytest.raises(DomainError) as erro:
        create_student(full_name=nome, email="sem.nome@exemplo.test")

    assert "nome completo" in str(erro.value)
    assert User.objects.count() == antes


def test_email_e_normalizado_para_minusculas():
    aluno = create_student(full_name="Fabio Cruz", email="  Fabio.CRUZ@Exemplo.TEST  ")

    assert aluno.email == "fabio.cruz@exemplo.test"
    assert User.objects.filter(email="fabio.cruz@exemplo.test").count() == 1


def test_create_student_registra_auditoria(admin_user):
    aluno = create_student(
        full_name="Gina Alves", email="gina.alves@exemplo.test", actor=admin_user
    )

    log = eventos(AuditEvent.STUDENT_CREATED, student=aluno).get()
    assert log.actor == admin_user
    assert log.entity_type == "User"
    assert log.entity_id == str(aluno.pk)
    assert log.metadata["source"] == StudentSource.MANUAL


def test_create_student_sem_auditoria_nao_gera_log():
    aluno = create_student(
        full_name="Hugo Prado", email="hugo.prado@exemplo.test", auditar=False
    )

    assert eventos(AuditEvent.STUDENT_CREATED, student=aluno).exists() is False


# ---------------------------------------------------------------------------
# update_student
# ---------------------------------------------------------------------------


def test_update_student_altera_nome_email_e_observacoes(student_user):
    update_student(
        student_user,
        full_name="Joao da Silva Junior",
        email="joao.junior@exemplo.test",
        notes="Mudou de turma",
    )

    student_user.refresh_from_db()
    assert student_user.full_name == "Joao da Silva Junior"
    assert student_user.email == "joao.junior@exemplo.test"
    assert student_user.student_profile.notes == "Mudou de turma"


def test_update_student_nao_altera_o_papel(student_user):
    update_student(student_user, full_name="Joao Editado", email=student_user.email)

    student_user.refresh_from_db()
    assert student_user.role == UserRole.STUDENT
    assert student_user.is_staff is False
    assert student_user.is_superuser is False


def test_update_student_com_o_proprio_email_nao_acusa_duplicidade(student_user):
    """O e-mail atual do proprio aluno nao pode ser lido como ja cadastrado."""
    update_student(student_user, full_name="Joao Renomeado", email=student_user.email)

    student_user.refresh_from_db()
    assert student_user.full_name == "Joao Renomeado"
    assert student_user.email == "joao.aluno@exemplo.test"


def test_update_student_com_email_de_outro_aluno_e_recusado(student_user, outro_student):
    with pytest.raises(DomainError):
        update_student(
            student_user, full_name="Joao da Silva", email=outro_student.email
        )

    student_user.refresh_from_db()
    assert student_user.email == "joao.aluno@exemplo.test"


def test_update_student_em_admin_e_recusado(admin_user):
    with pytest.raises(DomainError) as erro:
        update_student(
            admin_user, full_name="Carla Invadida", email="carla.nova@exemplo.test"
        )

    assert "Somente alunos" in str(erro.value)

    admin_user.refresh_from_db()
    assert admin_user.full_name == "Carla Coordenadora"
    assert admin_user.role == UserRole.ADMIN


def test_update_student_registra_changed_fields(student_user, admin_user):
    update_student(
        student_user,
        full_name="Joao Atualizado",
        email="joao.atualizado@exemplo.test",
        notes="Observacao nova",
        actor=admin_user,
    )

    log = eventos(AuditEvent.STUDENT_UPDATED, student=student_user).get()
    assert log.actor == admin_user
    assert sorted(log.metadata["changed_fields"]) == ["email", "full_name", "notes"]


def test_update_student_sem_mudanca_nao_gera_log(student_user):
    """Sem alteracao real nao ha o que auditar; log vazio so polui a trilha."""
    update_student(
        student_user,
        full_name=student_user.full_name,
        email=student_user.email,
        notes=student_user.student_profile.notes,
    )

    assert eventos(AuditEvent.STUDENT_UPDATED, student=student_user).exists() is False


def test_update_student_de_apenas_um_campo_audita_so_ele(student_user):
    update_student(
        student_user,
        full_name=student_user.full_name,
        email=student_user.email,
        notes="Somente a observacao",
    )

    log = eventos(AuditEvent.STUDENT_UPDATED, student=student_user).get()
    assert log.metadata["changed_fields"] == ["notes"]


# ---------------------------------------------------------------------------
# block_student / unblock_student
# ---------------------------------------------------------------------------


def test_block_student_desativa_a_conta_e_audita(student_user, admin_user):
    block_student(student_user, actor=admin_user)

    student_user.refresh_from_db()
    assert student_user.is_active is False

    log = eventos(AuditEvent.STUDENT_BLOCKED, student=student_user).get()
    assert log.actor == admin_user
    assert log.entity_type == "User"
    assert log.entity_id == str(student_user.pk)


def test_block_student_repetido_e_idempotente(student_user, admin_user):
    """Bloquear de novo nao muda nada, entao nao pode gerar um segundo log."""
    block_student(student_user, actor=admin_user)
    block_student(student_user, actor=admin_user)

    student_user.refresh_from_db()
    assert student_user.is_active is False
    assert eventos(AuditEvent.STUDENT_BLOCKED, student=student_user).count() == 1


def test_unblock_student_reativa_a_conta_e_audita(student_user, admin_user):
    block_student(student_user)
    unblock_student(student_user, actor=admin_user)

    student_user.refresh_from_db()
    assert student_user.is_active is True

    log = eventos(AuditEvent.STUDENT_UNBLOCKED, student=student_user).get()
    assert log.actor == admin_user


def test_unblock_student_nao_altera_a_senha(student_user, senha):
    hash_antes = User.objects.get(pk=student_user.pk).password

    block_student(student_user)
    unblock_student(student_user)

    depois = User.objects.get(pk=student_user.pk)
    assert depois.password == hash_antes
    assert depois.check_password(senha) is True


def test_unblock_student_repetido_e_idempotente(student_user):
    block_student(student_user)
    unblock_student(student_user)
    unblock_student(student_user)

    assert eventos(AuditEvent.STUDENT_UNBLOCKED, student=student_user).count() == 1


def test_block_student_em_admin_e_recusado(admin_user):
    with pytest.raises(DomainError) as erro:
        block_student(admin_user)

    assert "Somente alunos" in str(erro.value)

    admin_user.refresh_from_db()
    assert admin_user.is_active is True
    assert eventos(AuditEvent.STUDENT_BLOCKED).exists() is False


def test_unblock_student_em_admin_e_recusado(admin_user):
    admin_user.is_active = False
    admin_user.save(update_fields=["is_active"])

    with pytest.raises(DomainError) as erro:
        unblock_student(admin_user)

    assert "Somente alunos" in str(erro.value)

    admin_user.refresh_from_db()
    assert admin_user.is_active is False


# ---------------------------------------------------------------------------
# Sigilo da senha padrao na trilha de auditoria
# ---------------------------------------------------------------------------


def test_nenhum_log_de_auditoria_contem_a_senha_padrao(admin_user, senha_padrao):
    aluno = create_student(
        full_name="Ivo Barros", email="ivo.barros@exemplo.test", actor=admin_user
    )
    update_student(
        aluno,
        full_name="Ivo Barros Neto",
        email="ivo.neto@exemplo.test",
        notes="Revisado",
        actor=admin_user,
    )
    block_student(aluno, actor=admin_user)
    unblock_student(aluno, actor=admin_user)

    assert AuditLog.objects.exists() is True
    for log in AuditLog.objects.all():
        assert senha_padrao not in str(log.metadata)
        assert senha_padrao not in str(log.entity_id)
        assert senha_padrao not in str(log.user_agent)


# ---------------------------------------------------------------------------
# obter_ou_criar_perfil e alunos_queryset
# ---------------------------------------------------------------------------


def test_obter_ou_criar_perfil_cria_o_perfil_ausente():
    aluno = aluno_sem_perfil()
    assert StudentProfile.objects.filter(user=aluno).exists() is False

    perfil = obter_ou_criar_perfil(aluno)

    assert perfil.pk is not None
    assert perfil.user == aluno
    assert perfil.source == StudentSource.MANUAL
    assert StudentProfile.objects.filter(user=aluno).count() == 1


def test_obter_ou_criar_perfil_respeita_a_origem_informada():
    aluno = aluno_sem_perfil(email="importado@exemplo.test", nome="Aluno Importado")

    perfil = obter_ou_criar_perfil(aluno, source=StudentSource.IMPORT)

    assert perfil.source == StudentSource.IMPORT


def test_obter_ou_criar_perfil_devolve_o_perfil_existente(student_user):
    perfil = obter_ou_criar_perfil(student_user)

    assert perfil.pk == student_user.student_profile.pk
    assert StudentProfile.objects.filter(user=student_user).count() == 1


def test_alunos_queryset_traz_apenas_alunos(student_user, outro_student, admin_user):
    alunos = list(alunos_queryset())

    assert student_user in alunos
    assert outro_student in alunos
    assert admin_user not in alunos
    assert all(usuario.role == UserRole.STUDENT for usuario in alunos)


def test_alunos_queryset_inclui_aluno_bloqueado(student_user):
    """Bloqueado continua sendo aluno: some da lista seria perder o registro."""
    block_student(student_user)

    assert student_user in list(alunos_queryset())


def test_alunos_queryset_inclui_aluno_sem_perfil():
    aluno = aluno_sem_perfil()

    assert aluno in list(alunos_queryset())
