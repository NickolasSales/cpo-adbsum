"""Telas administrativas do Controle de Ministros."""

import pytest

from audit.models import AuditEvent, AuditLog
from ministers.models import Minister, MinisterAccount, MinisterSource
from ministers.tests.conftest import SENHA_MINISTRO, dados_de_ministro

pytestmark = pytest.mark.django_db

URL_DASHBOARD = "/admin-panel/ministros/"
URL_LISTA = "/admin-panel/ministros/cadastro/"
URL_NOVO = "/admin-panel/ministros/novo/"


def url(ministro, sufixo=""):
    return "/admin-panel/ministros/{}/{}".format(ministro.pk, sufixo)


def formulario(**extra):
    dados = dados_de_ministro()
    dados["birth_date"] = "1970-05-10"
    dados.update(extra)
    return {chave: ("" if valor is None else valor) for chave, valor in dados.items()}


# ---------------------------------------------------------------------------
# Permissoes
# ---------------------------------------------------------------------------


ROTAS_GET = [
    URL_DASHBOARD,
    URL_LISTA,
    URL_NOVO,
    "/admin-panel/ministros/importar/",
]


@pytest.mark.parametrize("rota", ROTAS_GET)
def test_aluno_recebe_403(student_client_logado, rota):
    assert student_client_logado.get(rota).status_code == 403


@pytest.mark.parametrize("rota", ROTAS_GET)
def test_anonimo_vai_para_o_login(client, rota):
    resposta = client.get(rota)
    assert resposta.status_code == 302
    assert resposta.url.startswith("/login/")


def test_aluno_nao_inativa_ministro(student_client_logado, ministro):
    assert student_client_logado.post(url(ministro, "inativar/")).status_code == 403
    ministro.refresh_from_db()
    assert ministro.is_active is True


@pytest.mark.parametrize(
    "sufixo",
    ["inativar/", "reativar/", "acesso/bloquear/", "acesso/desbloquear/"],
)
def test_mutacoes_exigem_post(admin_client_logado, ministro_com_acesso, sufixo):
    assert admin_client_logado.get(url(ministro_com_acesso, sufixo)).status_code == 405


# ---------------------------------------------------------------------------
# Menu e dashboard
# ---------------------------------------------------------------------------


def test_menu_separa_as_areas(admin_client_logado):
    html = admin_client_logado.get(URL_DASHBOARD).content.decode()
    for area in ("CPO", "Ministros", "Administração"):
        assert '<div class="cpo-lateral__area' in html and ">{}</div>".format(area) in html
    # A legenda e exclusiva de itens futuros, e nao ha nenhum.
    assert "cpo-lateral__legenda" not in html
    assert 'href="/admin-panel/ministros/cadastro/"' in html
    # Mensalidades existe desde a Fase 2A e Comprovantes desde a 2E; o que
    # ainda nao existe nao aparece.
    assert 'href="/admin-panel/ministros/mensalidades/"' in html
    assert 'href="/admin-panel/ministros/comprovantes/"' in html
    assert "Relatórios" not in html


def test_dashboard_conta(admin_client_logado, ministro_com_acesso, outro_ministro):
    from ministers import services

    services.create_minister(dados=dados_de_ministro(conamad_registration="5", field_registration=""))
    services.deactivate_minister(outro_ministro)

    numeros = admin_client_logado.get(URL_DASHBOARD).context["numeros"]
    assert numeros["total"] == 3
    assert numeros["ativos"] == 2
    assert numeros["inativos"] == 1
    assert numeros["com_acesso"] == 2
    assert numeros["sem_acesso"] == 1


# ---------------------------------------------------------------------------
# Lista e filtros
# ---------------------------------------------------------------------------


def test_lista(admin_client_logado, ministro_com_acesso, outro_ministro):
    resposta = admin_client_logado.get(URL_LISTA)
    html = resposta.content.decode()

    assert resposta.status_code == 200
    assert "Rodrigo Exemplo Xavier" in html
    assert "Bruna Exemplo Souza" in html
    assert "cpo-lista--cartoes" in html
    assert 'data-rotulo="CONAMAD"' in html


@pytest.mark.parametrize(
    "filtro, esperado",
    [
        ("q=rodrigo", {"17899"}),
        ("q=13292", {"132927"}),
        ("q=7764", {"17899"}),
        ("congregacao=Sede", {"17899"}),
        ("titulo=EVANGELISTA", {"132927"}),
        ("situacao=inativos", set()),
        ("acesso=com", {"17899", "132927"}),
        ("acesso=sem", {"5"}),
    ],
)
def test_filtros(admin_client_logado, ministro_com_acesso, outro_ministro, filtro, esperado):
    from ministers import services

    services.create_minister(
        dados=dados_de_ministro(
            full_name="Sem Acesso", conamad_registration="5", field_registration="", congregation=""
        )
    )
    resposta = admin_client_logado.get(URL_LISTA + "?" + filtro)
    encontrados = {m.conamad_registration for m in resposta.context["ministros"]}
    assert encontrados == esperado


def test_lista_pagina(admin_client_logado):
    from ministers import services

    for numero in range(30):
        services.create_minister(
            dados=dados_de_ministro(
                full_name="Ministro {:02d}".format(numero),
                conamad_registration=str(1000 + numero),
                field_registration="",
            )
        )
    resposta = admin_client_logado.get(URL_LISTA)
    assert len(resposta.context["ministros"]) == 25
    assert resposta.context["page_obj"].paginator.num_pages == 2


# ---------------------------------------------------------------------------
# Cadastro manual
# ---------------------------------------------------------------------------


def test_cadastro_sem_acesso(admin_client_logado, admin_user):
    resposta = admin_client_logado.post(URL_NOVO, formulario())
    ministro = Minister.objects.get()

    assert resposta.status_code == 302
    assert resposta.url == url(ministro)
    assert ministro.source == MinisterSource.MANUAL
    assert not MinisterAccount.objects.exists()
    assert AuditLog.objects.get(event=AuditEvent.MINISTER_CREATED).actor == admin_user


def test_cadastro_com_acesso(admin_client_logado):
    resposta = admin_client_logado.post(
        URL_NOVO,
        formulario(create_access="on", password1=SENHA_MINISTRO, password2=SENHA_MINISTRO),
    )
    assert resposta.status_code == 302
    acesso = MinisterAccount.objects.get()
    assert acesso.check_password(SENHA_MINISTRO)
    assert acesso.must_change_password is False
    assert AuditLog.objects.filter(event=AuditEvent.MINISTER_ACCOUNT_CREATED).count() == 1
    for evento in AuditLog.objects.all():
        assert SENHA_MINISTRO not in str(evento.metadata)


def test_cadastro_com_senhas_diferentes(admin_client_logado):
    resposta = admin_client_logado.post(
        URL_NOVO,
        formulario(create_access="on", password1=SENHA_MINISTRO, password2="Outra#Senha2026"),
    )
    html = resposta.content.decode()
    assert resposta.status_code == 200
    assert "As duas senhas não coincidem." in html
    assert SENHA_MINISTRO not in html
    assert not Minister.objects.exists()


def test_cadastro_com_inscricao_repetida(admin_client_logado, ministro):
    resposta = admin_client_logado.post(URL_NOVO, formulario(field_registration=""))
    assert resposta.status_code == 200
    assert "Já existe um ministro com esta inscrição CONAMAD." in resposta.content.decode()
    assert Minister.objects.count() == 1


def test_formulario_nao_aceita_campos_privilegiados(admin_client_logado):
    admin_client_logado.post(
        URL_NOVO,
        formulario(is_active="False", source="IMPORT", password_hash="x", import_sequence="9"),
    )
    ministro = Minister.objects.get()
    assert ministro.is_active is True
    assert ministro.source == MinisterSource.MANUAL
    assert ministro.import_sequence is None


def test_nome_e_escapado(admin_client_logado):
    admin_client_logado.post(URL_NOVO, formulario(full_name="<script>alert(1)</script>"))
    html = admin_client_logado.get(URL_LISTA).content.decode()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# Edicao
# ---------------------------------------------------------------------------


def test_edicao(admin_client_logado, ministro_com_acesso):
    resposta = admin_client_logado.post(
        url(ministro_com_acesso, "editar/"),
        formulario(congregation="Jardim Novo", password_hash="pbkdf2_sha256$x$y$z"),
    )
    assert resposta.status_code == 302
    ministro_com_acesso.refresh_from_db()
    assert ministro_com_acesso.congregation == "Jardim Novo"

    acesso = MinisterAccount.objects.get(minister=ministro_com_acesso)
    assert acesso.check_password(SENHA_MINISTRO)
    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_UPDATED)
    assert evento.metadata["changed_fields"] == ["congregation"]


def test_edicao_nao_tem_campo_de_senha(admin_client_logado, ministro):
    html = admin_client_logado.get(url(ministro, "editar/")).content.decode()
    assert 'name="password1"' not in html
    assert 'name="password_hash"' not in html


# ---------------------------------------------------------------------------
# Situacao e acesso
# ---------------------------------------------------------------------------


def test_inativar_e_reativar(admin_client_logado, ministro):
    admin_client_logado.post(url(ministro, "inativar/"))
    ministro.refresh_from_db()
    assert ministro.is_active is False

    admin_client_logado.post(url(ministro, "reativar/"))
    ministro.refresh_from_db()
    assert ministro.is_active is True


def test_criar_acesso_pela_ficha(admin_client_logado, ministro):
    resposta = admin_client_logado.post(
        url(ministro, "acesso/criar/"), {"password1": SENHA_MINISTRO, "password2": SENHA_MINISTRO}
    )
    assert resposta.status_code == 302
    assert MinisterAccount.objects.get(minister=ministro).login_identifier == "17899"


def test_bloquear_e_liberar_acesso(admin_client_logado, ministro_com_acesso):
    admin_client_logado.post(url(ministro_com_acesso, "acesso/bloquear/"))
    acesso = MinisterAccount.objects.get(minister=ministro_com_acesso)
    assert acesso.is_active is False
    ministro_com_acesso.refresh_from_db()
    assert ministro_com_acesso.is_active is True

    admin_client_logado.post(url(ministro_com_acesso, "acesso/desbloquear/"))
    acesso.refresh_from_db()
    assert acesso.is_active is True

    eventos = set(AuditLog.objects.values_list("event", flat=True))
    assert AuditEvent.MINISTER_ACCOUNT_BLOCKED in eventos
    assert AuditEvent.MINISTER_ACCOUNT_UNBLOCKED in eventos


def test_reset_de_senha(admin_client_logado, ministro_com_acesso):
    nova = "Redefinida#2026x"
    resposta = admin_client_logado.post(
        url(ministro_com_acesso, "acesso/resetar-senha/"), {"password1": nova, "password2": nova}
    )
    assert resposta.status_code == 302
    acesso = MinisterAccount.objects.get(minister=ministro_com_acesso)
    assert acesso.check_password(nova)
    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_PASSWORD_RESET)
    assert evento.metadata == {"redefinida": True}


def test_reset_sem_acesso_volta_para_a_ficha(admin_client_logado, ministro):
    resposta = admin_client_logado.get(url(ministro, "acesso/resetar-senha/"))
    assert resposta.status_code == 302


def test_ficha(admin_client_logado, ministro_com_acesso):
    html = admin_client_logado.get(url(ministro_com_acesso)).content.decode()
    assert "Acesso ativo" in html
    assert "Bloquear acesso" in html
    assert "Inativar" in html
    assert "pbkdf2" not in html


def test_ficha_inexistente(admin_client_logado):
    assert admin_client_logado.get("/admin-panel/ministros/999999/").status_code == 404


# ---------------------------------------------------------------------------
# Django Admin
# ---------------------------------------------------------------------------


def test_django_admin_e_somente_leitura(client, ministro_com_acesso):
    from accounts.models import User

    tecnico = User.objects.create_superuser(
        email="tecnico@exemplo.test", full_name="Suporte", password="Tecnico#Seguro2026"
    )
    client.force_login(tecnico)

    assert client.get("/django-admin/ministers/minister/").status_code == 200
    assert client.get("/django-admin/ministers/minister/add/").status_code == 403
    html = client.get(
        "/django-admin/ministers/ministeraccount/{}/change/".format(ministro_com_acesso.account.pk)
    ).content.decode()
    assert "pbkdf2" not in html
