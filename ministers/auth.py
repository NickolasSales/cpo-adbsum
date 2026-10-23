"""
Autenticacao do Portal do Ministro.

Por que nao o sistema de autenticacao do Django
-----------------------------------------------
O login do CPO autentica accounts.User, e todo User tem papel, permissoes e
um caminho possivel ate /admin-panel/ e /django-admin/. Um ministro nao pode
ter nada disso. Em vez de criar Users "sem poder" e torcer para que nenhuma
checagem futura esqueca de olhar o papel, o ministro simplesmente nao e um
User: request.user continua anonimo durante toda a navegacao no portal, e
qualquer tela do CPO o trata como visitante.

O que fica na sessao
--------------------
    _minister_account_id    o id do MinisterAccount
    _minister_auth_hash     assinatura derivada do hash da senha vigente

A assinatura e o mesmo mecanismo do Django para invalidar sessoes quando a
senha muda: trocou ou resetou a senha, toda sessao aberta com a anterior cai
no request seguinte.

As duas identidades nao convivem
--------------------------------
Entrar no portal encerra qualquer sessao do CPO no mesmo navegador, e entrar
no CPO encerra a do portal (ministers.signals). Num computador compartilhado
da secretaria isso evita que o ministro herde, sem saber, a sessao de um
administrador — ou o contrario.

Limite de tentativas
--------------------
O projeto nao tinha protecao contra forca bruta, e nao ha cache compartilhado
(o Gunicorn roda com dois workers e cache em memoria e por processo). A
contagem usa a propria trilha: falhas recentes de MINISTER_LOGIN_FAILED para
o mesmo identificador, e para o mesmo IP. Nao exige Redis nem tabela nova, e
sobrevive a reinicio do servico.
"""

from datetime import timedelta

from django.contrib.auth import logout as django_logout
from django.contrib.auth.hashers import make_password
from django.middleware.csrf import rotate_token
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from audit.models import AuditEvent, AuditLog
from audit.services import record
from common.http import get_client_ip
from ministers.models import MinisterAccount
from ministers.normalizacao import normalizar_registro

SESSAO_CONTA = "_minister_account_id"
SESSAO_HASH = "_minister_auth_hash"

JANELA_DE_TENTATIVAS = timedelta(minutes=15)
LIMITE_POR_IDENTIFICADOR = 5
LIMITE_POR_IP = 30

MENSAGEM_CREDENCIAL_INVALIDA = "Matrícula / inscrição ou senha inválidas."
MENSAGEM_LIMITE = (
    "Muitas tentativas sem sucesso. Aguarde 15 minutos e tente novamente, "
    "ou procure a secretaria."
)


class FalhaDeLogin(Exception):
    """A autenticacao foi recusada. A mensagem e a que o ministro pode ver."""


def _registrar_falha(request, identificador, motivo, acesso=None):
    # O motivo fica na trilha, que so o ADMIN le, e nunca na tela: na tela a
    # mensagem e sempre a mesma, para nao confirmar quais inscricoes existem.
    # Quando a inscricao existe, o evento aponta para o ministro e aparece na
    # ficha dele.
    record(
        AuditEvent.MINISTER_LOGIN_FAILED,
        request=request,
        entity_type="Minister",
        entity_id=acesso.minister_id if acesso is not None else "",
        metadata={"identificador": identificador[:30], "motivo": motivo},
    )


def tentativas_esgotadas(request, identificador):
    """Se o identificador ou o IP passaram do limite na janela recente."""
    desde = timezone.now() - JANELA_DE_TENTATIVAS
    falhas = AuditLog.objects.filter(
        event=AuditEvent.MINISTER_LOGIN_FAILED, timestamp__gte=desde
    )
    if identificador and (
        falhas.filter(metadata__identificador=identificador[:30]).count()
        >= LIMITE_POR_IDENTIFICADOR
    ):
        return True
    ip = get_client_ip(request)
    if ip and falhas.filter(ip_address=ip).count() >= LIMITE_POR_IP:
        return True
    return False


def autenticar(request, identificador, senha):
    """
    Devolve o MinisterAccount, ou levanta FalhaDeLogin.

    Ordem das checagens pensada para nao vazar informacao pelo tempo de
    resposta: quando o identificador nao existe, um hash e calculado mesmo
    assim, como o ModelBackend do Django faz.
    """
    identificador = normalizar_registro(identificador)
    senha = senha or ""

    if tentativas_esgotadas(request, identificador):
        _registrar_falha(request, identificador, "limite_de_tentativas")
        raise FalhaDeLogin(MENSAGEM_LIMITE)

    acesso = (
        MinisterAccount.objects.select_related("minister")
        .filter(login_identifier=identificador)
        .first()
        if identificador
        else None
    )

    if acesso is None:
        make_password(senha)
        _registrar_falha(request, identificador, "identificador_desconhecido")
        raise FalhaDeLogin(MENSAGEM_CREDENCIAL_INVALIDA)

    if not acesso.check_password(senha):
        _registrar_falha(request, identificador, "senha_incorreta", acesso)
        raise FalhaDeLogin(MENSAGEM_CREDENCIAL_INVALIDA)

    # Senha correta, mas sem permissao de entrar. A mensagem continua a
    # mesma: confirmar "sua conta esta bloqueada" a quem acertou a senha de
    # outra pessoa seria entregar que a senha estava certa.
    if not acesso.is_active:
        _registrar_falha(request, identificador, "acesso_bloqueado", acesso)
        raise FalhaDeLogin(MENSAGEM_CREDENCIAL_INVALIDA)
    if not acesso.minister.is_active:
        _registrar_falha(request, identificador, "ministro_inativo", acesso)
        raise FalhaDeLogin(MENSAGEM_CREDENCIAL_INVALIDA)

    return acesso


def entrar(request, acesso):
    """
    Abre a sessao do portal.

    A sessao anterior e descartada inteira e uma chave nova e emitida
    (protecao contra fixacao de sessao). Se havia um User do CPO logado neste
    navegador, ele sai junto.
    """
    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        django_logout(request)
    else:
        request.session.flush()

    request.session[SESSAO_CONTA] = acesso.pk
    request.session[SESSAO_HASH] = acesso.session_auth_hash()
    rotate_token(request)

    acesso.last_login = timezone.now()
    acesso.save(update_fields=["last_login"])
    request._minister_account = acesso

    record(
        AuditEvent.MINISTER_LOGIN_SUCCESS,
        request=request,
        entity_type="Minister",
        entity_id=acesso.minister_id,
    )


def sair(request):
    request.session.flush()
    request._minister_account = None


def atualizar_sessao(request, acesso):
    """Mantem o ministro logado depois que ele mesmo troca a senha."""
    request.session.cycle_key()
    request.session[SESSAO_HASH] = acesso.session_auth_hash()
    request._minister_account = acesso


def esquecer_ministro(session):
    """Remove a identidade do portal da sessao, sem tocar no resto."""
    session.pop(SESSAO_CONTA, None)
    session.pop(SESSAO_HASH, None)


def conta_autenticada(request):
    """
    O MinisterAccount da sessao, ou None.

    Revalida a cada request: o acesso pode ter sido bloqueado, o ministro
    inativado ou a senha trocada desde o login. Qualquer um dos tres encerra
    a sessao do portal na hora.
    """
    if hasattr(request, "_minister_account"):
        return request._minister_account

    acesso = None
    conta_id = request.session.get(SESSAO_CONTA)
    if conta_id is not None:
        candidato = (
            MinisterAccount.objects.select_related("minister")
            .filter(pk=conta_id)
            .first()
        )
        assinatura = request.session.get(SESSAO_HASH) or ""
        if (
            candidato is not None
            and candidato.pode_entrar
            and constant_time_compare(assinatura, candidato.session_auth_hash())
        ):
            acesso = candidato
        else:
            esquecer_ministro(request.session)

    request._minister_account = acesso
    return acesso
