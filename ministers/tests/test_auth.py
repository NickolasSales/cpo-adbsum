"""Autenticacao propria do Portal do Ministro."""

import pytest
from django.contrib.auth import SESSION_KEY

from audit.models import AuditEvent, AuditLog
from conftest import SENHA_VALIDA
from ministers import services
from ministers.auth import LIMITE_POR_IDENTIFICADOR, SESSAO_CONTA
from ministers.models import MinisterAccount
from ministers.tests.conftest import (
    SENHA_MINISTRO,
    SENHA_MINISTRO_NOVA,
    URL_LOGIN,
    URL_LOGOUT,
    URL_PAINEL,
    URL_PERFIL,
    URL_TROCAR_SENHA,
    entrar_no_portal,
)

pytestmark = pytest.mark.django_db

MENSAGEM_GENERICA = "Matrícula / inscrição ou senha inválidas."


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def test_tela_de_login_tem_a_identidade_do_portal(client):
    resposta = client.get(URL_LOGIN)
    html = resposta.content.decode()

    assert resposta.status_code == 200
    assert "Portal do Ministro" in html
    assert "AD Brás Sumaré" in html
    assert "Matrícula / Inscrição" in html
    assert "Area do aluno" not in html
    assert "Área do Aluno" not in html


def test_login_valido(client, ministro_com_acesso):
    resposta = entrar_no_portal(client)

    assert resposta.status_code == 302
    assert resposta.url == URL_PAINEL
    assert client.session[SESSAO_CONTA] == ministro_com_acesso.account.pk
    assert client.get(URL_PAINEL).status_code == 200

    acesso = MinisterAccount.objects.get(minister=ministro_com_acesso)
    assert acesso.last_login is not None
    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_LOGIN_SUCCESS)
    assert evento.entity_id == str(ministro_com_acesso.pk)
    assert evento.actor is None


def test_login_aceita_identificador_com_espacos(client, ministro_com_acesso):
    resposta = entrar_no_portal(client, identificador="  17899 ")
    assert resposta.status_code == 302


def test_ministro_logado_nao_vira_usuario_do_cpo(ministro_client_logado):
    resposta = ministro_client_logado.get(URL_PAINEL)
    assert resposta.wsgi_request.user.is_authenticated is False
    assert SESSION_KEY not in ministro_client_logado.session


def test_sessao_e_rotacionada_no_login(client, ministro_com_acesso):
    client.get(URL_LOGIN)
    client.session.save()
    chave_antes = client.session.session_key
    entrar_no_portal(client)
    assert client.session.session_key != chave_antes


def test_senha_errada(client, ministro_com_acesso):
    resposta = entrar_no_portal(client, senha="Errada#Senha2026")

    assert resposta.status_code == 200
    assert MENSAGEM_GENERICA in resposta.content.decode()
    assert SESSAO_CONTA not in client.session

    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_LOGIN_FAILED)
    assert evento.metadata == {"identificador": "17899", "motivo": "senha_incorreta"}
    assert "Errada#Senha2026" not in str(evento.metadata)


def test_identificador_inexistente_tem_a_mesma_mensagem(client, ministro_com_acesso):
    resposta = entrar_no_portal(client, identificador="999999")
    assert MENSAGEM_GENERICA in resposta.content.decode()


def test_conta_bloqueada_nao_entra(client, ministro_com_acesso):
    services.block_account(ministro_com_acesso)
    resposta = entrar_no_portal(client)

    assert resposta.status_code == 200
    assert MENSAGEM_GENERICA in resposta.content.decode()
    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_LOGIN_FAILED)
    assert evento.metadata["motivo"] == "acesso_bloqueado"


def test_ministro_inativo_nao_entra(client, ministro_com_acesso):
    services.deactivate_minister(ministro_com_acesso)
    resposta = entrar_no_portal(client)

    assert MENSAGEM_GENERICA in resposta.content.decode()
    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_LOGIN_FAILED)
    assert evento.metadata["motivo"] == "ministro_inativo"


def test_bloqueio_derruba_sessao_aberta(ministro_client_logado, ministro_com_acesso):
    services.block_account(ministro_com_acesso)
    resposta = ministro_client_logado.get(URL_PAINEL)
    assert resposta.status_code == 302
    assert resposta.url.startswith(URL_LOGIN)


def test_inativacao_derruba_sessao_aberta(ministro_client_logado, ministro_com_acesso):
    services.deactivate_minister(ministro_com_acesso)
    assert ministro_client_logado.get(URL_PERFIL).status_code == 302


def test_senha_nunca_volta_no_html(client, ministro_com_acesso):
    resposta = entrar_no_portal(client, senha="Errada#Senha2026")
    assert "Errada#Senha2026" not in resposta.content.decode()


def test_limite_de_tentativas(client, ministro_com_acesso):
    for _ in range(LIMITE_POR_IDENTIFICADOR):
        entrar_no_portal(client, senha="Errada#Senha2026")

    # Mesmo com a senha certa, o identificador esta em espera.
    resposta = entrar_no_portal(client)
    assert resposta.status_code == 200
    assert "Muitas tentativas" in resposta.content.decode()
    assert SESSAO_CONTA not in client.session
    assert AuditLog.objects.filter(
        event=AuditEvent.MINISTER_LOGIN_FAILED, metadata__motivo="limite_de_tentativas"
    ).exists()


def test_limite_de_um_identificador_nao_afeta_outro(client, ministro_com_acesso, outro_ministro):
    for _ in range(LIMITE_POR_IDENTIFICADOR):
        entrar_no_portal(client, senha="Errada#Senha2026")
    resposta = entrar_no_portal(client, identificador="132927", senha="Evangelista#2026x")
    assert resposta.status_code == 302


def test_next_externo_e_ignorado(client, ministro_com_acesso):
    resposta = client.post(
        URL_LOGIN + "?next=https://malicioso.example/",
        {"identificador": "17899", "senha": SENHA_MINISTRO, "next": "https://malicioso.example/"},
    )
    assert resposta.url == URL_PAINEL


def test_next_para_fora_do_portal_e_ignorado(client, ministro_com_acesso):
    resposta = client.post(
        URL_LOGIN, {"identificador": "17899", "senha": SENHA_MINISTRO, "next": "/admin-panel/"}
    )
    assert resposta.url == URL_PAINEL


def test_next_do_portal_e_respeitado(client, ministro_com_acesso):
    resposta = client.post(
        URL_LOGIN, {"identificador": "17899", "senha": SENHA_MINISTRO, "next": URL_PERFIL}
    )
    assert resposta.url == URL_PERFIL


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


def test_logout_exige_post(ministro_client_logado):
    assert ministro_client_logado.get(URL_LOGOUT).status_code == 405
    assert ministro_client_logado.get(URL_PAINEL).status_code == 200


def test_logout_encerra_a_sessao(ministro_client_logado):
    resposta = ministro_client_logado.post(URL_LOGOUT)
    assert resposta.status_code == 302
    assert SESSAO_CONTA not in ministro_client_logado.session
    assert ministro_client_logado.get(URL_PAINEL).status_code == 302


# ---------------------------------------------------------------------------
# Troca de senha
# ---------------------------------------------------------------------------


def _trocar(client, atual=SENHA_MINISTRO, nova=SENHA_MINISTRO_NOVA, confirmacao=None):
    return client.post(
        URL_TROCAR_SENHA,
        {
            "senha_atual": atual,
            "nova_senha1": nova,
            "nova_senha2": confirmacao if confirmacao is not None else nova,
        },
    )


def test_troca_de_senha(ministro_client_logado, client, ministro_com_acesso):
    resposta = _trocar(ministro_client_logado)
    assert resposta.status_code == 302

    # Continua logado depois de trocar.
    assert ministro_client_logado.get(URL_PAINEL).status_code == 200

    acesso = MinisterAccount.objects.get(minister=ministro_com_acesso)
    assert acesso.check_password(SENHA_MINISTRO_NOVA)
    assert not acesso.check_password(SENHA_MINISTRO)
    assert acesso.password_changed_at is not None

    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_PASSWORD_CHANGED)
    assert SENHA_MINISTRO_NOVA not in str(evento.metadata)

    ministro_client_logado.post(URL_LOGOUT)
    assert entrar_no_portal(client, senha=SENHA_MINISTRO).status_code == 200
    assert entrar_no_portal(client, senha=SENHA_MINISTRO_NOVA).status_code == 302


def test_troca_exige_a_senha_atual(ministro_client_logado, ministro_com_acesso):
    resposta = _trocar(ministro_client_logado, atual="Errada#Senha2026")
    assert resposta.status_code == 200
    assert "A senha atual está incorreta." in resposta.content.decode()
    assert MinisterAccount.objects.get(minister=ministro_com_acesso).check_password(SENHA_MINISTRO)


def test_troca_exige_confirmacao_igual(ministro_client_logado):
    resposta = _trocar(ministro_client_logado, confirmacao="Outra#Coisa2026")
    assert "As duas senhas não coincidem." in resposta.content.decode()


def test_troca_aplica_validadores(ministro_client_logado):
    resposta = _trocar(ministro_client_logado, nova="12345678")
    assert resposta.status_code == 200
    assert not AuditLog.objects.filter(event=AuditEvent.MINISTER_PASSWORD_CHANGED).exists()


def test_troca_de_senha_derruba_as_outras_sessoes(ministro_client_logado):
    from django.test import Client

    outro_aparelho = Client()
    entrar_no_portal(outro_aparelho)
    assert outro_aparelho.get(URL_PAINEL).status_code == 200

    _trocar(ministro_client_logado)

    assert ministro_client_logado.get(URL_PAINEL).status_code == 200
    assert outro_aparelho.get(URL_PAINEL).status_code == 302


def test_reset_pelo_admin_derruba_a_sessao_do_ministro(ministro_client_logado, ministro_com_acesso):
    services.reset_password(ministro_com_acesso, new_password="Redefinida#2026x")
    resposta = ministro_client_logado.get(URL_PAINEL)
    assert resposta.status_code == 302
    assert resposta.url.startswith(URL_LOGIN)


def test_acesso_criado_pela_importacao_obriga_troca(client, ministro):
    from django.contrib.auth.hashers import make_password

    services._criar_acesso(
        ministro, hash_pronto=make_password("Inicial#Ministro2026"), must_change_password=True
    )
    resposta = entrar_no_portal(client, senha="Inicial#Ministro2026")
    assert resposta.url == URL_TROCAR_SENHA

    # Enquanto nao trocar, o portal so oferece a troca.
    assert client.get(URL_PAINEL).url == URL_TROCAR_SENHA
    assert client.get(URL_PERFIL).url == URL_TROCAR_SENHA

    _trocar(client, atual="Inicial#Ministro2026", nova=SENHA_MINISTRO_NOVA)
    assert client.get(URL_PAINEL).status_code == 200
    assert MinisterAccount.objects.get(minister=ministro).must_change_password is False


# ---------------------------------------------------------------------------
# Isolamento entre as identidades
# ---------------------------------------------------------------------------


def test_ministro_nao_acessa_o_painel_administrativo(ministro_client_logado):
    resposta = ministro_client_logado.get("/admin-panel/")
    assert resposta.status_code == 302
    assert resposta.url.startswith("/login/")

    resposta = ministro_client_logado.get("/admin-panel/ministros/")
    assert resposta.status_code == 302
    assert resposta.url.startswith("/login/")


def test_ministro_nao_acessa_o_django_admin(ministro_client_logado):
    resposta = ministro_client_logado.get("/django-admin/")
    assert resposta.status_code == 302
    assert "/django-admin/login/" in resposta.url


def test_ministro_nao_acessa_a_area_do_aluno(ministro_client_logado):
    resposta = ministro_client_logado.get("/aluno/")
    assert resposta.status_code == 302
    assert resposta.url.startswith("/login/")


def test_aluno_nao_acessa_o_portal(student_client_logado):
    resposta = student_client_logado.get(URL_PAINEL)
    assert resposta.status_code == 302
    assert resposta.url.startswith(URL_LOGIN)


def test_login_do_ministro_encerra_sessao_do_cpo(client, admin_user, ministro_com_acesso):
    client.force_login(admin_user)
    assert client.get("/admin-panel/").status_code == 200

    entrar_no_portal(client)
    assert client.get("/admin-panel/").status_code == 302
    assert client.get(URL_PAINEL).status_code == 200


def test_login_no_cpo_encerra_sessao_do_portal(ministro_client_logado, student_user):
    ministro_client_logado.post(
        "/login/", {"username": student_user.email, "password": SENHA_VALIDA}
    )
    assert SESSAO_CONTA not in ministro_client_logado.session
    assert ministro_client_logado.get(URL_PAINEL).status_code == 302


def test_portal_nao_usa_id_na_url(ministro_client_logado, outro_ministro):
    # Nao existe rota de perfil por id: a identidade vem da sessao.
    assert ministro_client_logado.get("/ministros/perfil/{}/".format(outro_ministro.pk)).status_code == 404
    html = ministro_client_logado.get(URL_PERFIL).content.decode()
    assert "Rodrigo Exemplo Xavier" in html
    assert outro_ministro.full_name not in html


def test_paginas_do_portal_nao_ficam_em_cache(ministro_client_logado):
    resposta = ministro_client_logado.get(URL_PERFIL)
    assert "no-store" in resposta["Cache-Control"]


def test_falha_de_inscricao_existente_aponta_para_o_ministro(client, ministro_com_acesso):
    entrar_no_portal(client, senha="Errada#Senha2026")
    entrar_no_portal(client, identificador="999999")

    conhecida, desconhecida = AuditLog.objects.filter(
        event=AuditEvent.MINISTER_LOGIN_FAILED
    ).order_by("id")
    assert (conhecida.entity_type, conhecida.entity_id) == ("Minister", str(ministro_com_acesso.pk))
    assert (desconhecida.entity_type, desconhecida.entity_id) == ("Minister", "")
