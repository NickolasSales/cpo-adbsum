"""Testes de login, logout e redirecionamento por papel."""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

URL_LOGIN = "/login/"
URL_LOGOUT = "/logout/"
URL_PAINEL_ADMIN = "/admin-panel/"
URL_PAINEL_ALUNO = "/aluno/"


def fazer_login(client, email, senha):
    return client.post(
        URL_LOGIN, {"username": email, "password": senha}, follow=False
    )


def test_pagina_de_login_responde(client):
    resposta = client.get(URL_LOGIN)
    assert resposta.status_code == 200
    assert "accounts/login.html" in [t.name for t in resposta.templates]


def test_login_de_admin_redireciona_para_o_painel_administrativo(client, admin_user, senha):
    resposta = fazer_login(client, admin_user.email, senha)
    assert resposta.status_code == 302
    assert resposta.url == URL_PAINEL_ADMIN


def test_login_de_aluno_redireciona_para_o_painel_do_aluno(client, student_user, senha):
    resposta = fazer_login(client, student_user.email, senha)
    assert resposta.status_code == 302
    assert resposta.url == URL_PAINEL_ALUNO


def test_login_aceita_email_com_maiusculas(client, student_user, senha):
    resposta = fazer_login(client, "JOAO.ALUNO@EXEMPLO.TEST", senha)
    assert resposta.status_code == 302
    assert resposta.url == URL_PAINEL_ALUNO


def test_senha_incorreta_nao_autentica(client, student_user):
    resposta = fazer_login(client, student_user.email, "senha-errada")
    assert resposta.status_code == 200
    assert resposta.wsgi_request.user.is_authenticated is False


def test_email_inexistente_nao_autentica(client):
    resposta = fazer_login(client, "ninguem@exemplo.test", "qualquer-senha")
    assert resposta.status_code == 200
    assert resposta.wsgi_request.user.is_authenticated is False


def test_usuario_inativo_nao_autentica(client, student_user, senha):
    student_user.is_active = False
    student_user.save(update_fields=["is_active"])

    resposta = fazer_login(client, student_user.email, senha)
    assert resposta.status_code == 200
    assert resposta.wsgi_request.user.is_authenticated is False


@pytest.mark.parametrize(
    "email,senha_informada",
    [
        ("ninguem@exemplo.test", "qualquer-senha"),
        ("joao.aluno@exemplo.test", "senha-errada"),
    ],
)
def test_mensagem_de_erro_e_generica(client, student_user, email, senha_informada):
    """
    A mensagem nao pode diferenciar e-mail inexistente de senha errada nem de
    conta bloqueada: isso permitiria enumerar os e-mails cadastrados.
    """
    resposta = fazer_login(client, email, senha_informada)
    conteudo = resposta.content.decode()
    assert "E-mail ou senha invalidos." in conteudo


def test_mensagem_de_conta_bloqueada_e_a_mesma(client, student_user, senha):
    student_user.is_active = False
    student_user.save(update_fields=["is_active"])

    resposta = fazer_login(client, student_user.email, senha)
    conteudo = resposta.content.decode()
    assert "E-mail ou senha invalidos." in conteudo
    for termo in ("bloquead", "inativ", "desativad"):
        assert termo not in conteudo.lower()


def test_logout_encerra_a_sessao(client, student_user, senha):
    fazer_login(client, student_user.email, senha)
    assert client.get(URL_PAINEL_ALUNO).status_code == 200

    resposta = client.post(URL_LOGOUT)
    assert resposta.status_code == 302

    seguinte = client.get(URL_PAINEL_ALUNO)
    assert seguinte.status_code == 302
    assert URL_LOGIN in seguinte.url


def test_logout_recusa_get(client, student_user, senha):
    """
    Logout por GET permitiria deslogar o usuario com uma simples tag de
    imagem hospedada em outro site.
    """
    fazer_login(client, student_user.email, senha)
    resposta = client.get(URL_LOGOUT)
    assert resposta.status_code == 405


def test_raiz_encaminha_admin_para_o_painel_administrativo(client, admin_user, senha):
    fazer_login(client, admin_user.email, senha)
    resposta = client.get("/")
    assert resposta.status_code == 302
    assert resposta.url == URL_PAINEL_ADMIN


def test_raiz_encaminha_anonimo_para_o_login(client):
    resposta = client.get("/")
    assert resposta.status_code == 302
    assert reverse("accounts:login") in resposta.url
