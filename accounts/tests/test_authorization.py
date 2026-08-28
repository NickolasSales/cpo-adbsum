"""Testes de segregacao de acesso entre ADMIN e STUDENT."""

import pytest

pytestmark = pytest.mark.django_db

URL_LOGIN = "/login/"
URL_PAINEL_ADMIN = "/admin-panel/"
URL_PAINEL_ALUNO = "/aluno/"


def fazer_login(client, email, senha):
    return client.post(URL_LOGIN, {"username": email, "password": senha})


def test_admin_acessa_o_painel_administrativo(client, admin_user, senha):
    fazer_login(client, admin_user.email, senha)
    resposta = client.get(URL_PAINEL_ADMIN)
    assert resposta.status_code == 200


def test_aluno_acessa_o_proprio_painel(client, student_user, senha):
    fazer_login(client, student_user.email, senha)
    resposta = client.get(URL_PAINEL_ALUNO)
    assert resposta.status_code == 200


def test_aluno_e_bloqueado_no_painel_administrativo(client, student_user, senha):
    """Validacao server-side: nao basta esconder o link na interface."""
    fazer_login(client, student_user.email, senha)
    resposta = client.get(URL_PAINEL_ADMIN)
    assert resposta.status_code == 403


def test_admin_nao_e_tratado_como_aluno(client, admin_user, senha):
    fazer_login(client, admin_user.email, senha)
    resposta = client.get(URL_PAINEL_ALUNO)
    assert resposta.status_code == 403


@pytest.mark.parametrize("url", [URL_PAINEL_ADMIN, URL_PAINEL_ALUNO])
def test_anonimo_e_redirecionado_para_o_login(client, url):
    resposta = client.get(url)
    assert resposta.status_code == 302
    assert URL_LOGIN in resposta.url


@pytest.mark.parametrize("url", [URL_PAINEL_ADMIN, URL_PAINEL_ALUNO])
def test_redirecionamento_preserva_o_destino(client, url):
    resposta = client.get(url)
    assert "next=" in resposta.url


def test_aluno_nao_acessa_o_django_admin(client, student_user, senha):
    fazer_login(client, student_user.email, senha)
    resposta = client.get("/django-admin/", follow=True)
    # O Django Admin exige is_staff; o aluno cai na tela de login dele.
    assert resposta.status_code == 200
    assert "/django-admin/login/" in resposta.request["PATH_INFO"]
