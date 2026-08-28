"""
Servicos de dominio de alunos.

Toda criacao, edicao e bloqueio de aluno passa por aqui. As views nao
manipulam User nem StudentProfile diretamente: validam a requisicao, chamam um
servico e apresentam o resultado. Concentrar as regras neste modulo permite
que a importacao em lote reutilize exatamente a mesma logica da criacao
individual, sem risco de as duas divergirem.
"""

from django.conf import settings
from django.db import transaction

from accounts.managers import normalizar_email
from accounts.models import User, UserRole
from audit.models import AuditEvent
from audit.services import record
from common.exceptions import DomainError, campos_alterados
from students.models import StudentProfile, StudentSource

MENSAGEM_SENHA_NAO_CONFIGURADA = (
    "A senha inicial padrao dos alunos nao esta configurada. "
    "Defina DEFAULT_STUDENT_PASSWORD nas variaveis de ambiente."
)


def senha_inicial_configurada():
    """Se e possivel criar alunos no momento."""
    return bool((settings.DEFAULT_STUDENT_PASSWORD or "").strip())


def obter_senha_inicial():
    """
    Devolve a senha inicial padrao ou recusa a operacao.

    Nunca criar conta com senha vazia: uma conta assim seria acessivel por
    qualquer pessoa. Se a variavel nao estiver configurada, a operacao inteira
    falha com mensagem administrativa clara.

    O valor devolvido nunca pode ser registrado em log, auditoria, template
    ou mensagem de erro.
    """
    senha = (settings.DEFAULT_STUDENT_PASSWORD or "").strip()
    if not senha:
        raise DomainError(MENSAGEM_SENHA_NAO_CONFIGURADA)
    return senha


def alunos_queryset():
    """
    Base de consulta de alunos.

    Um aluno e um User com papel STUDENT. O select_related evita uma consulta
    por linha ao exibir a origem do cadastro nas listagens.
    """
    return User.objects.filter(role=UserRole.STUDENT).select_related("student_profile")


def verificar_email_disponivel(email, *, ignorando=None):
    """
    Garante que o e-mail pode ser usado por um aluno.

    Recusa e-mail ja cadastrado e, em especial, e-mail pertencente a um
    administrador: converter um ADMIN em aluno automaticamente seria uma
    escalada de privilegio ao contrario, com perda silenciosa de acesso.
    """
    email = normalizar_email(email)
    consulta = User.objects.filter(email=email)
    if ignorando is not None:
        consulta = consulta.exclude(pk=ignorando.pk)

    existente = consulta.first()
    if existente is None:
        return email

    if existente.role == UserRole.ADMIN:
        raise DomainError(
            "Este e-mail pertence a um administrador e nao pode ser usado "
            "para um aluno."
        )
    raise DomainError("Ja existe um aluno cadastrado com este e-mail.")


@transaction.atomic
def create_student(
    *,
    full_name,
    email,
    notes="",
    source=StudentSource.MANUAL,
    actor=None,
    request=None,
    auditar=True,
):
    """
    Cria um aluno completo: User com papel STUDENT mais StudentProfile.

    O perfil e criado explicitamente aqui, e nao por signal, para que o
    momento da criacao seja obvio em importacoes e testes.
    """
    senha = obter_senha_inicial()
    email = verificar_email_disponivel(email)

    full_name = (full_name or "").strip()
    if not full_name:
        raise DomainError("O nome completo do aluno e obrigatorio.")

    user = User.objects.create_user(
        email=email,
        full_name=full_name,
        password=senha,
        role=UserRole.STUDENT,
        is_active=True,
        must_change_password=True,
    )
    StudentProfile.objects.create(user=user, notes=notes or "", source=source)

    if auditar:
        # A metadata guarda apenas a origem. O e-mail e o nome ja estao
        # acessiveis pela FK student; duplica-los na trilha seria armazenar
        # dado pessoal sem necessidade operacional.
        record(
            AuditEvent.STUDENT_CREATED,
            request=request,
            actor=actor,
            student=user,
            entity_type="User",
            entity_id=user.pk,
            metadata={"source": str(source)},
        )

    return user


@transaction.atomic
def update_student(student, *, full_name, email, notes="", actor=None, request=None):
    """
    Atualiza nome, e-mail e observacoes de um aluno.

    Papel, permissoes e flags de acesso nao sao editaveis por esta via: o
    formulario administrativo nao expoe esses campos e o servico nao os
    aceita, de modo que nem um POST forjado consegue promover um aluno.
    """
    if student.role != UserRole.STUDENT:
        raise DomainError("Somente alunos podem ser editados por esta tela.")

    email = verificar_email_disponivel(email, ignorando=student)
    full_name = (full_name or "").strip()
    if not full_name:
        raise DomainError("O nome completo do aluno e obrigatorio.")

    perfil = obter_ou_criar_perfil(student)

    alterados = campos_alterados(student, {"full_name": full_name, "email": email})
    alterados += campos_alterados(perfil, {"notes": notes or ""})

    student.full_name = full_name
    student.email = email
    student.save(update_fields=["full_name", "email"])

    perfil.notes = notes or ""
    perfil.save(update_fields=["notes", "updated_at"])

    if alterados:
        record(
            AuditEvent.STUDENT_UPDATED,
            request=request,
            actor=actor,
            student=student,
            entity_type="User",
            entity_id=student.pk,
            metadata={"changed_fields": sorted(alterados)},
        )

    return student


def obter_ou_criar_perfil(student, *, source=StudentSource.MANUAL):
    """
    Perfil do aluno, criado sob demanda se ainda nao existir.

    Cobre contas de aluno que tenham nascido fora do servico de criacao, como
    um superusuario convertido manualmente pelo shell.
    """
    perfil = getattr(student, "student_profile", None)
    if perfil is not None:
        return perfil
    perfil, _ = StudentProfile.objects.get_or_create(
        user=student, defaults={"source": source}
    )
    return perfil


def block_student(student, *, actor=None, request=None):
    """
    Bloqueia o acesso do aluno ao sistema.

    Marcar is_active=False basta: o ModelBackend do Django recusa a
    autenticacao e tambem devolve None em get_user(), de modo que uma sessao
    ja aberta passa a resolver como anonima na proxima requisicao.
    """
    if student.role != UserRole.STUDENT:
        raise DomainError("Somente alunos podem ser bloqueados por esta tela.")

    if not student.is_active:
        return student

    student.is_active = False
    student.save(update_fields=["is_active"])

    record(
        AuditEvent.STUDENT_BLOCKED,
        request=request,
        actor=actor,
        student=student,
        entity_type="User",
        entity_id=student.pk,
    )
    return student


def unblock_student(student, *, actor=None, request=None):
    """
    Libera novamente o acesso do aluno.

    A senha nao e alterada nem reiniciada: desbloquear e apenas devolver o
    acesso que existia antes.
    """
    if student.role != UserRole.STUDENT:
        raise DomainError("Somente alunos podem ser desbloqueados por esta tela.")

    if student.is_active:
        return student

    student.is_active = True
    student.save(update_fields=["is_active"])

    record(
        AuditEvent.STUDENT_UNBLOCKED,
        request=request,
        actor=actor,
        student=student,
        entity_type="User",
        entity_id=student.pk,
    )
    return student
