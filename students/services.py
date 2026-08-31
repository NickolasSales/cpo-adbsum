"""
Servicos de dominio de alunos.

Toda criacao, edicao e bloqueio de aluno passa por aqui. As views nao
manipulam User nem StudentProfile diretamente: validam a requisicao, chamam um
servico e apresentam o resultado. Concentrar as regras neste modulo permite
que a importacao em lote reutilize exatamente a mesma logica da criacao
individual, sem risco de as duas divergirem.
"""

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
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


def validar_senha_de_aluno(senha, *, usuario=None):
    """
    Aplica os validadores de senha do Django e devolve a senha.

    Roda os mesmos validadores configurados em AUTH_PASSWORD_VALIDATORS, para
    que a senha definida pelo administrador nao seja mais fraca do que a que
    o proprio aluno poderia escolher antes.

    Recebe `usuario` quando ele ja existe: e o que permite ao
    UserAttributeSimilarityValidator recusar uma senha parecida com o nome ou
    o e-mail da pessoa.

    A senha nunca e devolvida em mensagem de erro, log ou auditoria — apenas
    para quem chamou, que a entrega imediatamente a set_password.
    """
    senha = senha or ""
    if not senha:
        raise DomainError("A senha e obrigatoria.")

    try:
        validate_password(senha, user=usuario)
    except ValidationError as erro:
        # As mensagens do Django descrevem a regra violada ("muito curta",
        # "muito comum"), nunca o valor digitado.
        raise DomainError(" ".join(erro.messages))

    return senha


@transaction.atomic
def create_student(
    *,
    full_name,
    email,
    password=None,
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

    Politica de senha, a partir da Etapa 5
    --------------------------------------
    Quem define a senha do aluno e o administrador. Na criacao individual ela
    chega em `password` e e obrigatoria. Na importacao em lote `password` vem
    vazio e cai na senha padrao do ambiente, porque nao ha como digitar uma
    senha diferente para cada linha de uma planilha de duzentos alunos.

    Em ambos os casos `must_change_password` nasce False: o aluno nao troca a
    propria senha, entao obriga-lo a trocar no primeiro acesso o deixaria
    preso num formulario que ele nao tem permissao de enviar.

    A senha entra por create_user, que chama set_password internamente.
    Nenhum caminho deste modulo atribui user.password diretamente.
    """
    email = verificar_email_disponivel(email)

    if password:
        senha = validar_senha_de_aluno(password)
    else:
        senha = obter_senha_inicial()

    full_name = (full_name or "").strip()
    if not full_name:
        raise DomainError("O nome completo do aluno e obrigatorio.")

    user = User.objects.create_user(
        email=email,
        full_name=full_name,
        password=senha,
        role=UserRole.STUDENT,
        is_active=True,
        must_change_password=False,
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


@transaction.atomic
def reset_student_password(student, *, new_password, actor=None, request=None):
    """
    Redefine a senha de um aluno por decisao administrativa.

    A partir da Etapa 5 o aluno nao troca a propria senha, entao este e o
    unico caminho existente para a senha de um aluno mudar. Esquecer a senha
    deixou de ser um problema do aluno e passou a ser uma tarefa do
    administrador.

    O que este servico garante:

        so aluno            um ADMIN nao pode ter a senha trocada por aqui;
                            para isso existe o fluxo proprio dele
        set_password        nunca atribuicao direta a user.password, que
                            gravaria a senha em claro no banco
        must_change_password  volta a False: obrigar a troca prenderia o aluno
                            num formulario que ele nao tem permissao de enviar
        auditoria           registra que houve reset, sem a senha

    Sessoes antigas
    ---------------
    Trocar a senha muda o hash, e o hash entra no calculo da chave de sessao
    do Django (AbstractBaseUser.get_session_auth_hash). Com
    SessionAuthenticationMiddleware ativo — o padrao —, as sessoes abertas com
    a senha anterior deixam de validar no request seguinte. Nao ha sistema
    paralelo de sessao aqui: o comportamento vem do proprio Django, e existe
    teste que o exercita de ponta a ponta.
    """
    if student.role != UserRole.STUDENT:
        raise DomainError(
            "Somente a senha de alunos pode ser redefinida por esta tela."
        )

    senha = validar_senha_de_aluno(new_password, usuario=student)

    student.set_password(senha)
    student.must_change_password = False
    student.save(update_fields=["password", "must_change_password"])

    record(
        AuditEvent.STUDENT_PASSWORD_RESET,
        request=request,
        actor=actor,
        student=student,
        entity_type="User",
        entity_id=student.pk,
        # Somente o fato. Senha, hash e comprimento ficam de fora — o
        # comprimento ajuda quem tenta adivinhar e nao ajuda quem investiga.
        #
        # A chave e "redefinida", e nao "password_reset", porque o sanitizador
        # de audit.services descarta por SUBSTRING qualquer chave que contenha
        # "password". Ele esta certo: e essa regra grosseira que garante que
        # nenhuma chave futura carregue segredo por descuido. Renomear a chave
        # aqui e mais barato — e mais seguro — do que abrir uma excecao na
        # unica barreira que protege a trilha inteira.
        #
        # O evento ja se chama STUDENT_PASSWORD_RESET, entao o significado nao
        # se perde.
        metadata={"redefinida": True},
    )
    return student
