"""Helpers de leitura da requisicao, reutilizaveis por toda a aplicacao."""

import ipaddress

from django.conf import settings

# O user-agent e um cabecalho controlado pelo cliente e pode vir com tamanho
# arbitrario. Truncamos antes de persistir.
MAX_USER_AGENT = 500


def _ip_valido(valor):
    if not valor:
        return None
    try:
        ipaddress.ip_address(valor)
    except ValueError:
        return None
    return valor


def get_client_ip(request):
    """
    Devolve o IP do cliente, ou None quando nao for possivel determina-lo.

    X-Forwarded-For so e consultado quando settings.TRUST_PROXY_HEADERS
    estiver ligado. Sem um proxy reverso confiavel na frente da aplicacao,
    esse cabecalho e escrito pelo proprio cliente e serviria apenas para
    poluir a auditoria com IPs forjados.

    O valor e validado antes de ser devolvido: um cabecalho com lixo nao pode
    derrubar a gravacao do log de auditoria.
    """
    if request is None:
        return None

    if getattr(settings, "TRUST_PROXY_HEADERS", False):
        encaminhado = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if encaminhado:
            primeiro = encaminhado.split(",")[0].strip()
            ip = _ip_valido(primeiro)
            if ip:
                return ip

    return _ip_valido(request.META.get("REMOTE_ADDR"))


def get_user_agent(request):
    """Devolve o user-agent truncado, ou string vazia."""
    if request is None:
        return ""
    return request.META.get("HTTP_USER_AGENT", "")[:MAX_USER_AGENT]
