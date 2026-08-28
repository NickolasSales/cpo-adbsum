"""Testes dos helpers de autorizacao e leitura da requisicao."""

import pytest
from django.test import RequestFactory

from accounts.models import UserRole
from common.http import MAX_USER_AGENT, get_client_ip, get_user_agent
from common.navigation import url_do_painel


def test_url_do_painel_por_papel(admin_user, student_user):
    assert url_do_painel(admin_user) == "admin_panel:dashboard"
    assert url_do_painel(student_user) == "student:dashboard"


def test_url_do_painel_para_anonimo():
    from django.contrib.auth.models import AnonymousUser

    assert url_do_painel(AnonymousUser()) == "accounts:login"


def test_papel_desconhecido_nao_ganha_destino_permissivo(student_user):
    student_user.role = "OUTRO"
    assert url_do_painel(student_user) == "accounts:login"


def test_get_client_ip_usa_remote_addr_por_padrao(settings):
    settings.TRUST_PROXY_HEADERS = False
    pedido = RequestFactory().get("/", REMOTE_ADDR="203.0.113.7")
    assert get_client_ip(pedido) == "203.0.113.7"


def test_get_client_ip_ignora_forwarded_for_sem_proxy_confiavel(settings):
    """
    Sem proxy reverso na frente, X-Forwarded-For e escrito pelo cliente.
    Confiar nele encheria a auditoria de IPs forjados.
    """
    settings.TRUST_PROXY_HEADERS = False
    pedido = RequestFactory().get(
        "/", REMOTE_ADDR="203.0.113.7", HTTP_X_FORWARDED_FOR="1.2.3.4"
    )
    assert get_client_ip(pedido) == "203.0.113.7"


def test_get_client_ip_usa_forwarded_for_com_proxy_confiavel(settings):
    settings.TRUST_PROXY_HEADERS = True
    pedido = RequestFactory().get(
        "/", REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="1.2.3.4, 10.0.0.1"
    )
    assert get_client_ip(pedido) == "1.2.3.4"


def test_get_client_ip_descarta_valor_invalido(settings):
    settings.TRUST_PROXY_HEADERS = True
    pedido = RequestFactory().get(
        "/", REMOTE_ADDR="203.0.113.7", HTTP_X_FORWARDED_FOR="nao-e-um-ip"
    )
    assert get_client_ip(pedido) == "203.0.113.7"


def test_get_client_ip_com_remote_addr_invalido_devolve_none():
    pedido = RequestFactory().get("/", REMOTE_ADDR="lixo")
    assert get_client_ip(pedido) is None


def test_get_user_agent_trunca():
    pedido = RequestFactory().get("/", HTTP_USER_AGENT="x" * (MAX_USER_AGENT + 200))
    assert len(get_user_agent(pedido)) == MAX_USER_AGENT


def test_get_user_agent_ausente():
    assert get_user_agent(RequestFactory().get("/")) == ""


def test_mixin_sem_papel_definido_falha_ruidosamente(rf, student_user):
    """Uma view mal configurada precisa quebrar, nunca liberar acesso."""
    from common.mixins import RoleRequiredMixin

    class ViewMalConfigurada(RoleRequiredMixin):
        pass

    view = ViewMalConfigurada()
    view.request = rf.get("/")
    view.request.user = student_user

    with pytest.raises(ValueError):
        view.test_func()


def test_papeis_disponiveis():
    assert UserRole.ADMIN == "ADMIN"
    assert UserRole.STUDENT == "STUDENT"
