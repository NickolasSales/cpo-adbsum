"""
Troca de senha e a flag must_change_password, depois da Etapa 5.

O que mudou
-----------
A senha do aluno passou a ser definida pelo administrador, e o aluno nao a
altera mais. A rota /alterar-senha/ continua existindo — o ADMIN troca a
propria senha por ela —, mas responde 403 a um STUDENT.

Consequencia para must_change_password: a flag continua no modelo e continua
valendo para ADMIN, mas deixou de comandar o fluxo do STUDENT. Se continuasse,
um aluno criado antes desta etapa seria mandado para uma tela que agora
recusa, e voltaria para la a cada request — um loop sem saida, em producao,
para quem ja existia.

Este arquivo foi reescrito, e nao apagado. Os testes que exercitavam o
bloqueio do middleware continuam existindo; o que mudou foi o papel do
usuario, porque e nele que a regra ainda se aplica. Os testes especificos da
nova politica do aluno ficam em students/tests/test_password_policy.py.
"""

import pytest

pytestmark = pytest.mark.django_db

URL_LOGIN = "/login/"
URL_TROCAR_SENHA = "/alterar-senha/"
URL_PAINEL_ALUNO = "/aluno/"
URL_PAINEL_ADMIN = "/admin-panel/"
URL_HEALTH = "/health/"


def fazer_login(client, email, senha):
    return client.post(URL_LOGIN, {"username": email, "password": senha})


# ---------------------------------------------------------------------------
# A flag, no papel onde ela ainda vale: ADMIN
# ---------------------------------------------------------------------------


def test_flag_bloqueia_o_painel_do_admin(client, admin_com_troca_pendente, senha):
    fazer_login(client, admin_com_troca_pendente.email, senha)

    resposta = client.get(URL_PAINEL_ADMIN)

    assert resposta.status_code == 302
    assert resposta.url == URL_TROCAR_SENHA


def test_flag_bloqueia_qualquer_outra_rota(client, admin_com_troca_pendente, senha):
    """
    Middleware, e nao decorator.

    Um decorator precisa ser lembrado em cada view nova, e uma view esquecida
    vira um furo na regra. O middleware cobre por construcao toda rota
    presente e futura — inclusive as que ainda nao existem.
    """
    fazer_login(client, admin_com_troca_pendente.email, senha)

    for url in (URL_PAINEL_ALUNO, "/", "/django-admin/"):
        resposta = client.get(url)
        assert resposta.status_code == 302, url
        assert resposta.url == URL_TROCAR_SENHA, url


def test_flag_libera_a_propria_tela_de_troca(client, admin_com_troca_pendente, senha):
    fazer_login(client, admin_com_troca_pendente.email, senha)

    assert client.get(URL_TROCAR_SENHA).status_code == 200


def test_flag_libera_logout_e_health(client, admin_com_troca_pendente, senha):
    """Sem estas excecoes o usuario ficaria preso, sem conseguir nem sair."""
    fazer_login(client, admin_com_troca_pendente.email, senha)

    assert client.get(URL_HEALTH).status_code == 200

    resposta = client.post("/logout/")
    assert resposta.status_code == 302


def test_troca_valida_limpa_a_flag_e_libera_o_painel(
    client, admin_com_troca_pendente, senha, senha_nova
):
    fazer_login(client, admin_com_troca_pendente.email, senha)

    resposta = client.post(
        URL_TROCAR_SENHA,
        {
            "old_password": senha,
            "new_password1": senha_nova,
            "new_password2": senha_nova,
        },
    )
    assert resposta.status_code == 302
    assert resposta.url == URL_PAINEL_ADMIN

    admin_com_troca_pendente.refresh_from_db()
    assert admin_com_troca_pendente.must_change_password is False

    # A sessao continua valida apos a troca (update_session_auth_hash). Sem
    # isso o proprio usuario seria deslogado ao trocar a senha, porque a
    # sessao carrega um hash derivado da senha antiga.
    assert client.get(URL_PAINEL_ADMIN).status_code == 200


def test_apos_a_troca_a_senha_nova_autentica_e_a_antiga_nao(
    client, admin_com_troca_pendente, senha, senha_nova
):
    fazer_login(client, admin_com_troca_pendente.email, senha)
    client.post(
        URL_TROCAR_SENHA,
        {
            "old_password": senha,
            "new_password1": senha_nova,
            "new_password2": senha_nova,
        },
    )
    client.post("/logout/")

    recusado = fazer_login(client, admin_com_troca_pendente.email, senha)
    assert recusado.status_code == 200
    assert recusado.wsgi_request.user.is_authenticated is False

    aceito = fazer_login(client, admin_com_troca_pendente.email, senha_nova)
    assert aceito.status_code == 302
    assert aceito.url == URL_PAINEL_ADMIN


# ---------------------------------------------------------------------------
# Validacao do formulario
# ---------------------------------------------------------------------------


def test_senha_fraca_e_recusada(client, admin_com_troca_pendente, senha):
    fazer_login(client, admin_com_troca_pendente.email, senha)

    resposta = client.post(
        URL_TROCAR_SENHA,
        {"old_password": senha, "new_password1": "123", "new_password2": "123"},
    )

    assert resposta.status_code == 200
    admin_com_troca_pendente.refresh_from_db()
    assert admin_com_troca_pendente.must_change_password is True


def test_confirmacao_divergente_e_recusada(
    client, admin_com_troca_pendente, senha, senha_nova
):
    fazer_login(client, admin_com_troca_pendente.email, senha)

    resposta = client.post(
        URL_TROCAR_SENHA,
        {
            "old_password": senha,
            "new_password1": senha_nova,
            "new_password2": senha_nova + "x",
        },
    )

    assert resposta.status_code == 200
    admin_com_troca_pendente.refresh_from_db()
    assert admin_com_troca_pendente.must_change_password is True


def test_senha_atual_incorreta_e_recusada(
    client, admin_com_troca_pendente, senha, senha_nova
):
    """
    Exigir a senha atual protege contra sequestro de sessao.

    Quem encontrar uma sessao aberta num computador de laboratorio nao
    consegue assumir a conta sem conhecer a senha vigente.
    """
    fazer_login(client, admin_com_troca_pendente.email, senha)

    resposta = client.post(
        URL_TROCAR_SENHA,
        {
            "old_password": "senha-que-nao-e-a-atual",
            "new_password1": senha_nova,
            "new_password2": senha_nova,
        },
    )

    assert resposta.status_code == 200
    admin_com_troca_pendente.refresh_from_db()
    assert admin_com_troca_pendente.must_change_password is True


def test_anonimo_nao_acessa_a_troca_de_senha(client):
    resposta = client.get(URL_TROCAR_SENHA)

    assert resposta.status_code == 302
    assert URL_LOGIN in resposta.url


# ---------------------------------------------------------------------------
# O aluno saiu deste fluxo
# ---------------------------------------------------------------------------


def test_aluno_nao_acessa_a_tela_de_troca(client, student_user, senha):
    """
    403, e nao 404.

    A rota existe e o aluno sabe que existe — e o mesmo endereco que ele usava
    antes. O que mudou foi a permissao, e e isso que a resposta precisa dizer.
    Um 404 sugeriria que a tela sumiu e produziria chamado de suporte por um
    comportamento que e intencional.
    """
    fazer_login(client, student_user.email, senha)

    assert client.get(URL_TROCAR_SENHA).status_code == 403


def test_aluno_antigo_com_a_flag_nao_fica_preso(client, student_com_troca_pendente):
    """
    O risco concreto da mudanca de politica.

    Alunos criados antes da Etapa 5 tem must_change_password=True. Se o
    middleware continuasse redirecionando por causa dessa flag, eles seriam
    mandados para /alterar-senha/, receberiam 403, e voltariam para la no
    request seguinte. Loop sem saida, em producao, para quem ja existia.
    """
    client.force_login(student_com_troca_pendente)

    resposta = client.get(URL_PAINEL_ALUNO)

    assert resposta.status_code == 200
