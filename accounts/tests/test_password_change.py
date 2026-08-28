"""Testes da troca de senha e da flag must_change_password."""

import pytest

pytestmark = pytest.mark.django_db

URL_LOGIN = "/login/"
URL_TROCAR_SENHA = "/alterar-senha/"
URL_PAINEL_ALUNO = "/aluno/"
URL_PAINEL_ADMIN = "/admin-panel/"
URL_HEALTH = "/health/"


def fazer_login(client, email, senha):
    return client.post(URL_LOGIN, {"username": email, "password": senha})


def test_flag_bloqueia_o_painel(client, student_com_troca_pendente, senha):
    fazer_login(client, student_com_troca_pendente.email, senha)
    resposta = client.get(URL_PAINEL_ALUNO)
    assert resposta.status_code == 302
    assert resposta.url == URL_TROCAR_SENHA


def test_flag_bloqueia_qualquer_outra_rota(client, student_com_troca_pendente, senha):
    fazer_login(client, student_com_troca_pendente.email, senha)
    for url in (URL_PAINEL_ADMIN, "/", "/django-admin/"):
        resposta = client.get(url)
        assert resposta.status_code == 302, url
        assert resposta.url == URL_TROCAR_SENHA, url


def test_flag_libera_a_propria_tela_de_troca(client, student_com_troca_pendente, senha):
    fazer_login(client, student_com_troca_pendente.email, senha)
    resposta = client.get(URL_TROCAR_SENHA)
    assert resposta.status_code == 200


def test_flag_libera_logout_e_health(client, student_com_troca_pendente, senha):
    """Sem estas excecoes o usuario ficaria preso, sem conseguir nem sair."""
    fazer_login(client, student_com_troca_pendente.email, senha)

    assert client.get(URL_HEALTH).status_code == 200

    resposta = client.post("/logout/")
    assert resposta.status_code == 302


def test_troca_valida_limpa_a_flag_e_libera_o_painel(
    client, student_com_troca_pendente, senha, senha_nova
):
    fazer_login(client, student_com_troca_pendente.email, senha)

    resposta = client.post(
        URL_TROCAR_SENHA,
        {
            "old_password": senha,
            "new_password1": senha_nova,
            "new_password2": senha_nova,
        },
    )
    assert resposta.status_code == 302
    assert resposta.url == URL_PAINEL_ALUNO

    student_com_troca_pendente.refresh_from_db()
    assert student_com_troca_pendente.must_change_password is False

    # A sessao continua valida apos a troca (update_session_auth_hash).
    assert client.get(URL_PAINEL_ALUNO).status_code == 200


def test_apos_a_troca_a_senha_nova_autentica_e_a_antiga_nao(
    client, student_com_troca_pendente, senha, senha_nova
):
    fazer_login(client, student_com_troca_pendente.email, senha)
    client.post(
        URL_TROCAR_SENHA,
        {
            "old_password": senha,
            "new_password1": senha_nova,
            "new_password2": senha_nova,
        },
    )
    client.post("/logout/")

    recusado = fazer_login(client, student_com_troca_pendente.email, senha)
    assert recusado.status_code == 200
    assert recusado.wsgi_request.user.is_authenticated is False

    aceito = fazer_login(client, student_com_troca_pendente.email, senha_nova)
    assert aceito.status_code == 302
    assert aceito.url == URL_PAINEL_ALUNO


def test_senha_fraca_e_recusada(client, student_com_troca_pendente, senha):
    fazer_login(client, student_com_troca_pendente.email, senha)

    resposta = client.post(
        URL_TROCAR_SENHA,
        {"old_password": senha, "new_password1": "123", "new_password2": "123"},
    )
    assert resposta.status_code == 200
    student_com_troca_pendente.refresh_from_db()
    assert student_com_troca_pendente.must_change_password is True


def test_confirmacao_divergente_e_recusada(
    client, student_com_troca_pendente, senha, senha_nova
):
    fazer_login(client, student_com_troca_pendente.email, senha)

    resposta = client.post(
        URL_TROCAR_SENHA,
        {
            "old_password": senha,
            "new_password1": senha_nova,
            "new_password2": senha_nova + "x",
        },
    )
    assert resposta.status_code == 200
    student_com_troca_pendente.refresh_from_db()
    assert student_com_troca_pendente.must_change_password is True


def test_senha_atual_incorreta_e_recusada(
    client, student_com_troca_pendente, senha_nova
):
    fazer_login(client, student_com_troca_pendente.email, "Prova#Segura2026")

    resposta = client.post(
        URL_TROCAR_SENHA,
        {
            "old_password": "senha-que-nao-e-a-atual",
            "new_password1": senha_nova,
            "new_password2": senha_nova,
        },
    )
    assert resposta.status_code == 200
    student_com_troca_pendente.refresh_from_db()
    assert student_com_troca_pendente.must_change_password is True


def test_anonimo_nao_acessa_a_troca_de_senha(client):
    resposta = client.get(URL_TROCAR_SENHA)
    assert resposta.status_code == 302
    assert URL_LOGIN in resposta.url
