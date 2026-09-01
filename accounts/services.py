"""
Gestao de contas administrativas.

O que e um administrador aqui
-----------------------------
`User.role = ADMIN`. Nada mais.

Isso NAO significa `is_staff` nem `is_superuser`. Os dois pertencem ao Django
Admin, que neste projeto e ferramenta tecnica de emergencia — quem administra
o produto usa /admin-panel/. Um administrador criado por esta tela nasce sem
nenhum dos dois, e nao existe caminho na interface que os conceda.

Por que isso importa
--------------------
`is_superuser=True` ignora todo o sistema de permissoes do Django: o usuario
passa em qualquer verificacao, inclusive nas que ainda nao foram escritas. Uma
tela de cadastro que aceitasse esse campo do navegador seria escalonamento de
privilegio por formulario.

Duas protecoes que so podem viver aqui
--------------------------------------
    nao bloquear a si mesmo   o administrador se trancaria para fora
    nao bloquear o ultimo     o sistema ficaria sem ninguem para destrancar

As duas sao verificadas no servico, e nao no template. Botao escondido nao e
controle de acesso: o POST continua alcancavel.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.managers import normalizar_email
from accounts.models import UserRole
from audit.models import AuditEvent
from audit.services import record
from common.exceptions import DomainError

User = get_user_model()


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------


def administradores():
    return User.objects.filter(role=UserRole.ADMIN)


def administradores_ativos():
    return administradores().filter(is_active=True)


def administrador_ou_none(pk):
    """
    O administrador de id `pk`, ou None.

    O filtro por papel e o que impede que uma rota de administrador seja usada
    contra um aluno: passar o id de um STUDENT em
    /admin-panel/administradores/<id>/bloquear/ nao encontra nada, e a view
    responde 404.
    """
    return administradores().filter(pk=pk).first()


def validar_senha_administrativa(senha, *, usuario=None):
    """Aplica os validadores do Django e devolve a senha aprovada."""
    if not senha:
        raise DomainError("Informe a senha.")
    try:
        validate_password(senha, user=usuario)
    except ValidationError as erro:
        raise DomainError(list(erro.messages))
    return senha


def _verificar_email_livre(email, *, ignorando=None):
    consulta = User.objects.filter(email=normalizar_email(email))
    if ignorando is not None:
        consulta = consulta.exclude(pk=ignorando.pk)
    if consulta.exists():
        raise DomainError("Ja existe um usuario com este e-mail.")


# ---------------------------------------------------------------------------
# Criacao e edicao
# ---------------------------------------------------------------------------


def create_admin_user(*, full_name, email, password, actor=None, request=None):
    """
    Cria uma conta administrativa.

    Os cinco campos privilegiados sao definidos AQUI, com valor literal, e nao
    a partir de nada que tenha vindo do navegador:

        role                 ADMIN
        is_active            True
        is_staff             False
        is_superuser         False
        must_change_password False

    `must_change_password=False` porque quem digitou a senha foi o
    administrador que esta criando a conta, e ele a entregara pessoalmente. A
    flag continua existindo e continua valendo para quem a ligar de proposito.
    """
    nome = (full_name or "").strip()
    if not nome:
        raise DomainError("Informe o nome completo.")

    endereco = normalizar_email(email)
    if not endereco:
        raise DomainError("Informe o e-mail.")
    _verificar_email_livre(endereco)

    senha = validar_senha_administrativa(password)

    with transaction.atomic():
        admin = User.objects.create_user(
            email=endereco,
            full_name=nome,
            password=senha,
            role=UserRole.ADMIN,
        )
        # create_user ja aplica is_staff=False e is_superuser=False, mas a
        # garantia fica explicita: se um dia o manager mudar de padrao, esta
        # linha continua valendo e o teste continua passando.
        admin.is_active = True
        admin.is_staff = False
        admin.is_superuser = False
        admin.must_change_password = False
        admin.save(
            update_fields=[
                "is_active",
                "is_staff",
                "is_superuser",
                "must_change_password",
            ]
        )

        record(
            AuditEvent.ADMIN_USER_CREATED,
            request=request,
            actor=actor,
            entity_type="User",
            entity_id=admin.pk,
            metadata={"role": admin.role},
        )
    return admin


def update_admin_user(admin, *, full_name, email, actor=None, request=None):
    """
    Edita nome e e-mail. Nada alem disso.

    Papel, is_staff e is_superuser nao aparecem na assinatura de proposito: o
    que a funcao nao recebe, ela nao pode alterar por descuido.
    """
    _exigir_administrador(admin)

    nome = (full_name or "").strip()
    if not nome:
        raise DomainError("Informe o nome completo.")

    endereco = normalizar_email(email)
    if not endereco:
        raise DomainError("Informe o e-mail.")
    _verificar_email_livre(endereco, ignorando=admin)

    alterados = []
    if admin.full_name != nome:
        admin.full_name = nome
        alterados.append("full_name")
    if admin.email != endereco:
        admin.email = endereco
        alterados.append("email")

    if not alterados:
        return admin

    admin.save(update_fields=alterados)

    record(
        AuditEvent.ADMIN_USER_UPDATED,
        request=request,
        actor=actor,
        entity_type="User",
        entity_id=admin.pk,
        # Somente os NOMES dos campos alterados. Copiar valor antigo e novo
        # levaria dado pessoal para a trilha sem necessidade.
        metadata={"changed_fields": alterados},
    )
    return admin


def _exigir_administrador(usuario):
    if getattr(usuario, "role", None) != UserRole.ADMIN:
        raise DomainError("Esta operacao vale apenas para contas administrativas.")


# ---------------------------------------------------------------------------
# Bloqueio
# ---------------------------------------------------------------------------


def block_admin_user(admin, *, actor=None, request=None):
    """
    Desativa uma conta administrativa.

    Nao existe exclusao fisica: apagar a linha quebraria a leitura da trilha
    de auditoria, onde o autor de cada acao aponta para este usuario.

    Duas recusas, nesta ordem:

        a propria conta   o administrador se trancaria para fora na hora
        o ultimo ativo    ninguem sobraria para desbloquear qualquer um

    A contagem do ultimo ativo acontece dentro da transacao, com
    select_for_update sobre as contas administrativas ativas: sem o lock, dois
    administradores bloqueando um ao outro ao mesmo tempo poderiam passar os
    dois pela verificacao e zerar o sistema.
    """
    _exigir_administrador(admin)

    if actor is not None and getattr(actor, "pk", None) == admin.pk:
        raise DomainError(
            "Voce nao pode bloquear a sua propria conta administrativa."
        )

    with transaction.atomic():
        ativos = list(
            administradores_ativos().select_for_update().values_list("pk", flat=True)
        )
        if admin.pk in ativos and len(ativos) <= 1:
            raise DomainError(
                "Esta e a unica conta administrativa ativa. Crie ou desbloqueie "
                "outra antes de bloquear esta."
            )

        if not admin.is_active:
            return admin

        admin.is_active = False
        admin.save(update_fields=["is_active"])

        record(
            AuditEvent.ADMIN_USER_BLOCKED,
            request=request,
            actor=actor,
            entity_type="User",
            entity_id=admin.pk,
            metadata={},
        )
    return admin


def unblock_admin_user(admin, *, actor=None, request=None):
    _exigir_administrador(admin)

    if admin.is_active:
        return admin

    admin.is_active = True
    admin.save(update_fields=["is_active"])

    record(
        AuditEvent.ADMIN_USER_UNBLOCKED,
        request=request,
        actor=actor,
        entity_type="User",
        entity_id=admin.pk,
        metadata={},
    )
    return admin


# ---------------------------------------------------------------------------
# Senha
# ---------------------------------------------------------------------------


def reset_admin_password(admin, *, new_password, actor=None, request=None):
    """
    Redefine a senha de uma conta administrativa.

    Nao pede a senha atual: quem executa e outro administrador, que nao a
    conhece e nao deve conhecer. Para trocar a PROPRIA senha existe o fluxo de
    /alterar-senha/, que exige a senha vigente.

    Sessoes antigas caem sozinhas. O hash entra no calculo da chave de sessao
    do Django (AbstractBaseUser.get_session_auth_hash), entao as sessoes
    abertas com a senha anterior deixam de validar no request seguinte. Nao ha
    sistema paralelo de sessao aqui.
    """
    _exigir_administrador(admin)

    senha = validar_senha_administrativa(new_password, usuario=admin)

    admin.set_password(senha)
    admin.must_change_password = False
    admin.save(update_fields=["password", "must_change_password"])

    record(
        AuditEvent.ADMIN_PASSWORD_RESET,
        request=request,
        actor=actor,
        entity_type="User",
        entity_id=admin.pk,
        # Somente o fato. Nem senha, nem hash, nem comprimento.
        #
        # A chave se chama "redefinida", e nao "password_reset", porque o
        # sanitizador de audit.services descarta por SUBSTRING qualquer chave
        # que contenha "password" — e ele esta certo. Renomear a chave e mais
        # barato, e mais seguro, do que abrir excecao na regra que protege a
        # trilha inteira. O evento ja se chama ADMIN_PASSWORD_RESET.
        metadata={"redefinida": True},
    )
    return admin
