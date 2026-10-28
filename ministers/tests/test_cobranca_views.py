"""Cobranca Pix nas telas (Fase 2D): painel administrativo, portal e guia em PDF."""

import base64
import datetime
import re
import zlib
from decimal import Decimal

import pytest
from django.conf import settings
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from audit.models import AuditEvent, AuditLog
from ministers import cobranca, guia, pix, services
from ministers import mensalidades as servico
from ministers.models import (
    MinisterBillingSettings,
    MinisterFeeCharge,
    MinisterMonthlyFee,
)
from ministers.tests.conftest import PIX_DE_TESTE, URL_LOGOUT, dados_de_ministro, entrar_no_portal

pytestmark = pytest.mark.django_db

HOJE = timezone.localdate()
ONTEM = HOJE - datetime.timedelta(days=1)
DAQUI_A_10 = HOJE + datetime.timedelta(days=10)

URL_CONFIG = "/admin-panel/ministros/configuracoes/pagamento/"
URL_COBRANCAS = "/admin-panel/ministros/mensalidades/cobrancas/"
URL_GERAR_LOTE = "/admin-panel/ministros/mensalidades/cobrancas/gerar/"
URL_LISTA = "/admin-panel/ministros/mensalidades/lista/"

OUTUBRO = "/ministros/mensalidades/2026/10/"
GUIA_OUTUBRO = "/ministros/mensalidades/2026/10/guia.pdf"
SENHA_DE_B = "Evangelista#2026x"


def url_ficha(mensalidade):
    return "/admin-panel/ministros/mensalidades/{}/".format(mensalidade.pk)


def url_gerar(mensalidade):
    return "/admin-panel/ministros/mensalidades/{}/cobranca/gerar/".format(mensalidade.pk)


def url_guia_admin(mensalidade):
    return "/admin-panel/ministros/mensalidades/{}/guia.pdf".format(mensalidade.pk)


def nova(ministro, competencia="2026-10", valor="35.00", vencimento=DAQUI_A_10):
    return servico.create_fee(
        minister=ministro, reference_month=competencia, due_date=vencimento, amount=valor
    )


def com_cobranca(ministro, **kwargs):
    mensalidade = nova(ministro, **kwargs)
    charge, _ = cobranca.gerar_cobranca(mensalidade)
    return mensalidade, charge


def html(resposta):
    assert resposta.status_code == 200, resposta.status_code
    return resposta.content.decode()


def conteudo_do_pdf(pdf):
    """O texto das paginas: streams ASCII85 + Flate do ReportLab, sem imagens."""
    partes = []
    for m in re.finditer(rb"<<(.*?)>>\s*stream\r?\n(.*?)endstream", pdf, re.S):
        if b"/Subtype /Image" in m.group(1):
            continue
        dados = m.group(2).strip()
        if b"ASCII85Decode" in m.group(1):
            # So o terminador "~>": ">" tambem e digito valido do ASCII85, e
            # rstrip("~>") comeria o ultimo digito de alguns streams.
            dados = dados.replace(b"\n", b"").replace(b"\r", b"")
            if dados.endswith(b"~>"):
                dados = dados[:-2]
            dados = base64.a85decode(dados)
        if b"FlateDecode" in m.group(1):
            dados = zlib.decompress(dados)
        partes.append(dados.decode("latin-1"))
    return "\n".join(partes)


def paginas_do_pdf(pdf):
    return len(re.findall(rb"/Type /Page[^s]", pdf))


@pytest.fixture
def cliente_de_b(outro_ministro):
    client = Client()
    assert entrar_no_portal(client, identificador="132927", senha=SENHA_DE_B).status_code == 302
    return client


# ---------------------------------------------------------------------------
# Configuracao — somente ADMIN
# ---------------------------------------------------------------------------


def test_admin_abre_a_configuracao(admin_client_logado):
    pagina = html(admin_client_logado.get(URL_CONFIG))
    assert "Configuração de pagamento" in pagina
    assert "Pagamento ainda não configurado." in pagina
    for campo in ("pix_key_type", "pix_key", "pix_receiver_name", "pix_receiver_city", "payment_instructions"):
        assert 'name="{}"'.format(campo) in pagina


def test_configuracao_nao_mostra_segredo_de_infraestrutura(admin_client_logado, configuracao_pix):
    pagina = html(admin_client_logado.get(URL_CONFIG))
    assert settings.SECRET_KEY not in pagina
    assert "DATABASE_URL" not in pagina and "DEFAULT_MINISTER_PASSWORD" not in pagina
    assert "pix.teste@example.com" in pagina


def test_admin_salva_a_configuracao(admin_client_logado, admin_user):
    resposta = admin_client_logado.post(URL_CONFIG, PIX_DE_TESTE)
    assert resposta.status_code == 302 and resposta.url == URL_CONFIG
    config = MinisterBillingSettings.objects.get()
    assert (config.pix_receiver_name, config.updated_by) == ("AD BRAS SUMARE TESTE", admin_user)
    assert AuditLog.objects.filter(event=AuditEvent.MINISTER_BILLING_SETTINGS_UPDATED).count() == 1


def test_configuracao_invalida_mostra_os_erros(admin_client_logado):
    dados = dict(PIX_DE_TESTE, pix_key="xx", pix_receiver_city="Santa Bárbara d'Oeste")
    pagina = html(admin_client_logado.post(URL_CONFIG, dados))
    assert "E-mail inválido para chave Pix." in pagina
    assert "A cidade pode ter no máximo 15 caracteres." in pagina
    assert not MinisterBillingSettings.objects.exists()


def test_configuracao_exige_csrf(admin_user):
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)
    assert client.post(URL_CONFIG, PIX_DE_TESTE).status_code == 403
    assert not MinisterBillingSettings.objects.exists()


def test_aluno_nao_acessa_a_configuracao(student_client_logado):
    assert student_client_logado.get(URL_CONFIG).status_code == 403
    assert student_client_logado.post(URL_CONFIG, PIX_DE_TESTE).status_code == 403
    assert not MinisterBillingSettings.objects.exists()


def test_ministro_nao_acessa_a_configuracao(ministro_client_logado):
    for metodo in (ministro_client_logado.get, ministro_client_logado.post):
        resposta = metodo(URL_CONFIG)
        assert resposta.status_code == 302 and resposta.url.startswith("/login/")
    assert not MinisterBillingSettings.objects.exists()


def test_anonimo_vai_para_o_login(client):
    resposta = client.get(URL_CONFIG)
    assert resposta.status_code == 302 and resposta.url.startswith("/login/")


def test_menu_tem_configuracoes(admin_client_logado):
    pagina = html(admin_client_logado.get("/admin-panel/ministros/"))
    assert 'href="{}"'.format(URL_CONFIG) in pagina
    assert ">Configurações</a>" in pagina


# ---------------------------------------------------------------------------
# Ficha e lista administrativas
# ---------------------------------------------------------------------------


def test_ficha_sem_cobranca_oferece_gerar(admin_client_logado, ministro, configuracao_pix):
    mensalidade = nova(ministro)
    pagina = html(admin_client_logado.get(url_ficha(mensalidade)))
    assert "Cobrança gerada" in pagina and "Não" in pagina
    assert 'action="{}"'.format(url_gerar(mensalidade)) in pagina
    assert "csrfmiddlewaretoken" in pagina


def test_ficha_sem_configuracao_avisa(admin_client_logado, ministro):
    mensalidade = nova(ministro)
    pagina = html(admin_client_logado.get(url_ficha(mensalidade)))
    assert "Configure os dados de pagamento antes de gerar as cobranças." in pagina
    assert url_gerar(mensalidade) not in pagina


def test_gerar_cobranca_pela_ficha(admin_client_logado, ministro, configuracao_pix, admin_user):
    mensalidade = nova(ministro)
    resposta = admin_client_logado.post(url_gerar(mensalidade))
    assert resposta.status_code == 302 and resposta.url == url_ficha(mensalidade)
    charge = MinisterFeeCharge.objects.get()
    assert charge.generated_by == admin_user

    pagina = html(admin_client_logado.get(url_ficha(mensalidade)))
    assert charge.txid in pagina
    assert "Sim" in pagina
    assert charge.pix_payload in pagina
    assert 'href="{}"'.format(url_guia_admin(mensalidade)) in pagina

    admin_client_logado.post(url_gerar(mensalidade))
    assert MinisterFeeCharge.objects.count() == 1


def test_gerar_cobranca_so_por_post(admin_client_logado, ministro, configuracao_pix):
    mensalidade = nova(ministro)
    assert admin_client_logado.get(url_gerar(mensalidade)).status_code == 405
    assert not MinisterFeeCharge.objects.exists()


def test_gerar_cobranca_exige_csrf(admin_user, ministro, configuracao_pix):
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)
    assert client.post(url_gerar(nova(ministro))).status_code == 403
    assert not MinisterFeeCharge.objects.exists()


def test_aluno_e_ministro_nao_geram_cobranca(student_client_logado, ministro_com_acesso, configuracao_pix):
    mensalidade = nova(ministro_com_acesso)
    ministro_client_logado = Client()
    assert entrar_no_portal(ministro_client_logado).status_code == 302
    assert student_client_logado.post(url_gerar(mensalidade)).status_code == 403
    assert student_client_logado.post(URL_GERAR_LOTE, {"confirmar": "1"}).status_code == 403
    for url in (url_gerar(mensalidade), URL_GERAR_LOTE):
        resposta = ministro_client_logado.post(url, {"confirmar": "1"})
        assert resposta.status_code == 302 and resposta.url.startswith("/login/")
    for url in (URL_COBRANCAS, url_guia_admin(mensalidade)):
        assert student_client_logado.get(url).status_code == 403
        assert ministro_client_logado.get(url).url.startswith("/login/")
    assert not MinisterFeeCharge.objects.exists()


def test_lista_mostra_se_tem_cobranca_sem_consulta_por_linha(admin_client_logado, configuracao_pix):
    def ministro(i):
        return services.create_minister(dados=dados_de_ministro(conamad_registration=str(4000 + i), field_registration=""))

    com_cobranca(ministro(0))
    nova(ministro(1))
    pagina = html(admin_client_logado.get(URL_LISTA))
    assert "Cobrança" in pagina
    with CaptureQueriesContext(connection) as poucas:
        admin_client_logado.get(URL_LISTA)
    for i in range(2, 12):
        com_cobranca(ministro(i))
    with CaptureQueriesContext(connection) as muitas:
        admin_client_logado.get(URL_LISTA)
    assert len(muitas.captured_queries) == len(poucas.captured_queries)


def test_tela_de_cobrancas_mostra_o_resumo_sem_gerar(admin_client_logado, ministro, configuracao_pix):
    nova(ministro)
    pagina = html(admin_client_logado.get(URL_COBRANCAS))
    assert "Gerar 1 cobrança" in pagina
    assert not MinisterFeeCharge.objects.exists()


def test_lote_exige_confirmacao(admin_client_logado, ministro, configuracao_pix):
    nova(ministro)
    admin_client_logado.post(URL_GERAR_LOTE)
    assert not MinisterFeeCharge.objects.exists()
    assert admin_client_logado.get(URL_GERAR_LOTE).status_code == 405
    resposta = admin_client_logado.post(URL_GERAR_LOTE, {"confirmar": "1"})
    assert resposta.status_code == 302 and resposta.url == URL_COBRANCAS
    assert MinisterFeeCharge.objects.count() == 1


def test_tela_de_cobrancas_sem_configuracao(admin_client_logado, ministro):
    nova(ministro)
    pagina = html(admin_client_logado.get(URL_COBRANCAS))
    assert "Configure os dados de pagamento antes de gerar as cobranças." in pagina
    assert 'action="{}"'.format(URL_GERAR_LOTE) not in pagina
    admin_client_logado.post(URL_GERAR_LOTE, {"confirmar": "1"})
    assert not MinisterFeeCharge.objects.exists()


def test_guia_administrativa(admin_client_logado, ministro, configuracao_pix):
    mensalidade, charge = com_cobranca(ministro)
    resposta = admin_client_logado.get(url_guia_admin(mensalidade))
    assert resposta.status_code == 200
    assert resposta["Content-Type"] == "application/pdf"
    assert "no-store" in resposta["Cache-Control"]
    servico.register_payment(mensalidade, paid_at=HOJE, payment_method="PIX")
    assert admin_client_logado.get(url_guia_admin(mensalidade)).status_code == 404


# ---------------------------------------------------------------------------
# Portal — lista
# ---------------------------------------------------------------------------


def test_lista_do_portal_oferece_ver_cobranca(ministro_client_logado, ministro_com_acesso, configuracao_pix):
    com_cobranca(ministro_com_acesso)
    nova(ministro_com_acesso, competencia="2026-11")
    pagina = html(ministro_client_logado.get("/ministros/mensalidades/"))
    assert 'href="{}"'.format(OUTUBRO) in pagina
    assert "Ver cobrança" in pagina
    # Novembro ainda nao tem cobranca: so os detalhes.
    assert 'href="/ministros/mensalidades/2026/11/"' in pagina
    assert pagina.count("Ver cobrança") == 1


# ---------------------------------------------------------------------------
# Portal — Ver cobranca, por situacao
# ---------------------------------------------------------------------------


def test_pendente_mostra_qr_copia_e_cola_e_guia(ministro_client_logado, ministro_com_acesso, configuracao_pix):
    _, charge = com_cobranca(ministro_com_acesso)
    pagina = html(ministro_client_logado.get(OUTUBRO))
    assert "Outubro/2026" in pagina
    assert "R$ 35,00" in pagina
    assert DAQUI_A_10.strftime("%d/%m/%Y") in pagina
    assert "Pendente" in pagina
    assert "Pague com Pix" in pagina
    assert pix.svg_qr(charge.pix_payload) in pagina
    assert ">{}</code>".format(charge.pix_payload) in pagina
    assert "Copiar Pix" in pagina and 'data-copiar="pix-copia-e-cola"' in pagina
    assert 'href="{}"'.format(GUIA_OUTUBRO) in pagina
    assert "js/copiar-pix" in pagina
    assert "Envie o comprovante para a secretaria." in pagina


def test_qr_e_copia_e_cola_sao_o_mesmo_payload(ministro_client_logado, ministro_com_acesso, configuracao_pix, monkeypatch):
    _, charge = com_cobranca(ministro_com_acesso)
    recebidos = []
    original = pix.svg_qr
    monkeypatch.setattr(pix, "svg_qr", lambda payload, **kw: recebidos.append(payload) or original(payload, **kw))
    pagina = html(ministro_client_logado.get(OUTUBRO))
    assert recebidos == [charge.pix_payload]
    assert ">{}</code>".format(charge.pix_payload) in pagina


def test_vencida_mostra_o_pix_com_aviso(ministro_client_logado, ministro_com_acesso, configuracao_pix):
    _, charge = com_cobranca(ministro_com_acesso, vencimento=ONTEM)
    pagina = html(ministro_client_logado.get(OUTUBRO))
    assert "Vencido" in pagina and "Esta mensalidade está vencida." in pagina
    assert charge.pix_payload in pagina and "Copiar Pix" in pagina


def test_pendente_sem_cobranca(ministro_client_logado, ministro_com_acesso):
    nova(ministro_com_acesso)
    pagina = html(ministro_client_logado.get(OUTUBRO))
    assert "Cobrança ainda não disponível" in pagina
    assert "<svg" not in pagina and "Copiar Pix" not in pagina
    assert ministro_client_logado.get(GUIA_OUTUBRO).status_code == 404
    assert not MinisterFeeCharge.objects.exists()


def _sem_pix(pagina, charge):
    assert charge.pix_payload not in pagina
    assert "<svg" not in pagina
    assert "Copiar Pix" not in pagina
    assert "Baixar guia" not in pagina
    assert "js/copiar-pix" not in pagina


def test_paga_nao_incentiva_novo_pagamento(ministro_client_logado, ministro_com_acesso, configuracao_pix):
    mensalidade, charge = com_cobranca(ministro_com_acesso)
    servico.register_payment(mensalidade, paid_at=HOJE, payment_method="PIX")
    pagina = html(ministro_client_logado.get(OUTUBRO))
    assert "Mensalidade paga" in pagina
    assert HOJE.strftime("%d/%m/%Y") in pagina
    _sem_pix(pagina, charge)
    assert ministro_client_logado.get(GUIA_OUTUBRO).status_code == 404


def test_isenta_nao_mostra_pix(ministro_client_logado, ministro_com_acesso, configuracao_pix):
    mensalidade, charge = com_cobranca(ministro_com_acesso)
    servico.exempt_fee(mensalidade, reason="motivo interno")
    pagina = html(ministro_client_logado.get(OUTUBRO))
    assert "Mensalidade isenta" in pagina and "Nenhum pagamento é necessário." in pagina
    assert "motivo interno" not in pagina
    _sem_pix(pagina, charge)
    assert ministro_client_logado.get(GUIA_OUTUBRO).status_code == 404


def test_cancelada_nao_mostra_pix(ministro_client_logado, ministro_com_acesso, configuracao_pix):
    mensalidade, charge = com_cobranca(ministro_com_acesso)
    servico.cancel_fee(mensalidade, reason="motivo interno")
    pagina = html(ministro_client_logado.get(OUTUBRO))
    assert "Mensalidade cancelada" in pagina
    _sem_pix(pagina, charge)
    assert ministro_client_logado.get(GUIA_OUTUBRO).status_code == 404


def test_cobranca_nao_mostra_dado_interno(ministro_client_logado, ministro_com_acesso, configuracao_pix, admin_user):
    mensalidade, charge = com_cobranca(ministro_com_acesso)
    servico.update_fee(mensalidade, due_date=DAQUI_A_10, amount="35.00", notes="anotacao interna")
    pagina = html(ministro_client_logado.get(OUTUBRO))
    assert "anotacao interna" not in pagina
    # O TXID esta DENTRO do Copia e Cola (campo 62-05), e so ali.
    assert pagina.count(charge.txid) == 1 and "TXID" not in pagina
    assert admin_user.full_name not in pagina
    assert 'value="{}"'.format(mensalidade.pk) not in pagina


# ---------------------------------------------------------------------------
# Portal — anti-IDOR, rotas e metodos
# ---------------------------------------------------------------------------


def test_competencia_de_outro_ministro_e_404(ministro_client_logado, outro_ministro, configuracao_pix):
    com_cobranca(outro_ministro, competencia="2025-03")
    assert ministro_client_logado.get("/ministros/mensalidades/2025/03/").status_code == 404
    assert ministro_client_logado.get("/ministros/mensalidades/2025/03/guia.pdf").status_code == 404


def test_mesma_competencia_cada_um_ve_a_sua(
    ministro_client_logado, cliente_de_b, ministro_com_acesso, outro_ministro, configuracao_pix
):
    _, de_a = com_cobranca(ministro_com_acesso, valor="111.11")
    _, de_b = com_cobranca(outro_ministro, valor="777.77")
    pagina_a = html(ministro_client_logado.get(OUTUBRO))
    pagina_b = html(cliente_de_b.get(OUTUBRO))
    assert "R$ 111,11" in pagina_a and de_a.pix_payload in pagina_a
    assert "R$ 777,77" not in pagina_a and de_b.pix_payload not in pagina_a
    assert "R$ 777,77" in pagina_b and de_b.pix_payload in pagina_b
    assert de_a.pix_payload not in pagina_b

    guia_a = ministro_client_logado.get(GUIA_OUTUBRO).content
    assert "111,11" in conteudo_do_pdf(guia_a) and "777,77" not in conteudo_do_pdf(guia_a)


@pytest.mark.parametrize("parametro", ["ministro", "minister", "minister_id", "conta", "account", "id", "pk", "fee", "txid"])
def test_query_string_nao_troca_o_dono(ministro_client_logado, ministro_com_acesso, outro_ministro, configuracao_pix, parametro):
    _, de_a = com_cobranca(ministro_com_acesso, valor="111.11")
    mensalidade_b, de_b = com_cobranca(outro_ministro, valor="777.77")
    for valor in (outro_ministro.pk, mensalidade_b.pk, de_b.txid):
        url = "{}?{}={}".format(OUTUBRO, parametro, valor)
        pagina = html(ministro_client_logado.get(url))
        assert de_a.pix_payload in pagina and de_b.pix_payload not in pagina
        guia_pdf = ministro_client_logado.get("{}?{}={}".format(GUIA_OUTUBRO, parametro, valor)).content
        assert "777,77" not in conteudo_do_pdf(guia_pdf)


@pytest.mark.parametrize(
    "url",
    [
        "/ministros/mensalidades/2026/13/",
        "/ministros/mensalidades/2026/00/",
        "/ministros/mensalidades/0000/10/",
        "/ministros/mensalidades/2026/1/",
        "/ministros/mensalidades/26/10/",
        "/ministros/mensalidades/2026/10/1/",
        "/ministros/mensalidades/2026/13/guia.pdf",
        "/ministros/mensalidades/2027/01/",
    ],
)
def test_competencia_invalida_ou_inexistente_e_404(ministro_client_logado, ministro_com_acesso, configuracao_pix, url):
    com_cobranca(ministro_com_acesso)
    assert ministro_client_logado.get(url).status_code == 404


def test_nao_existe_rota_por_id(ministro_client_logado, ministro_com_acesso, configuracao_pix):
    mensalidade, charge = com_cobranca(ministro_com_acesso)
    for url in (
        "/ministros/mensalidades/{}/".format(mensalidade.pk),
        "/ministros/mensalidades/{}/guia.pdf".format(mensalidade.pk),
        "/ministros/cobranca/{}/".format(charge.pk),
        "/ministros/mensalidades/{}/".format(charge.txid),
    ):
        assert ministro_client_logado.get(url).status_code == 404, url


@pytest.mark.parametrize("url", [OUTUBRO, GUIA_OUTUBRO])
def test_portal_nao_aceita_escrita(ministro_client_logado, ministro_com_acesso, configuracao_pix, url):
    mensalidade, _ = com_cobranca(ministro_com_acesso)
    antes = MinisterMonthlyFee.objects.filter(pk=mensalidade.pk).values().get()
    for metodo in (ministro_client_logado.post, ministro_client_logado.put, ministro_client_logado.delete):
        assert metodo(url).status_code == 405
    assert ministro_client_logado.post(url, {"status": "PAID", "paid_at": "2026-10-01"}).status_code == 405
    assert MinisterMonthlyFee.objects.filter(pk=mensalidade.pk).values().get() == antes
    assert MinisterFeeCharge.objects.count() == 1


def test_get_do_portal_nao_grava_nada(ministro_client_logado, ministro_com_acesso, configuracao_pix):
    mensalidade, charge = com_cobranca(ministro_com_acesso)
    sem_cobranca = nova(ministro_com_acesso, competencia="2026-11")
    eventos = AuditLog.objects.count()
    cobrancas = list(MinisterFeeCharge.objects.values())
    mensalidades = list(MinisterMonthlyFee.objects.order_by("id").values())

    with CaptureQueriesContext(connection) as consultas:
        for url in ("/ministros/mensalidades/", OUTUBRO, GUIA_OUTUBRO, "/ministros/mensalidades/2026/11/"):
            assert ministro_client_logado.get(url).status_code == 200
    escritas = [
        q["sql"] for q in consultas.captured_queries
        if re.match(r"\s*(INSERT|UPDATE|DELETE)", q["sql"], re.I) and "django_session" not in q["sql"]
    ]
    assert escritas == []
    assert AuditLog.objects.count() == eventos
    assert list(MinisterFeeCharge.objects.values()) == cobrancas
    assert list(MinisterMonthlyFee.objects.order_by("id").values()) == mensalidades
    assert cobranca.cobranca_vigente(sem_cobranca) is None


# ---------------------------------------------------------------------------
# Portal — autenticacao, cache, XSS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [OUTUBRO, GUIA_OUTUBRO])
def test_sem_sessao_vai_para_o_login(client, ministro_com_acesso, configuracao_pix, url):
    com_cobranca(ministro_com_acesso)
    resposta = client.get(url)
    assert resposta.status_code == 302
    assert resposta.url == "/ministros/login/?next={}".format(url)
    assert "no-store" in resposta["Cache-Control"]


def test_aluno_e_admin_do_cpo_nao_entram(student_client_logado, ministro_com_acesso, configuracao_pix):
    com_cobranca(ministro_com_acesso)
    for url in (OUTUBRO, GUIA_OUTUBRO):
        resposta = student_client_logado.get(url)
        assert resposta.status_code == 302 and resposta.url.startswith("/ministros/login/")


def test_conta_bloqueada_e_ministro_inativo(ministro_client_logado, ministro_com_acesso, configuracao_pix, admin_user):
    com_cobranca(ministro_com_acesso)
    services.block_account(ministro_com_acesso, actor=admin_user)
    assert ministro_client_logado.get(OUTUBRO).status_code == 302
    services.unblock_account(ministro_com_acesso, actor=admin_user)
    entrar_no_portal(ministro_client_logado)
    assert ministro_client_logado.get(OUTUBRO).status_code == 200
    services.deactivate_minister(ministro_com_acesso, actor=admin_user)
    assert ministro_client_logado.get(OUTUBRO).status_code == 302
    assert ministro_client_logado.get(GUIA_OUTUBRO).status_code == 302


def test_logout_encerra_o_acesso_a_cobranca(ministro_client_logado, ministro_com_acesso, configuracao_pix):
    com_cobranca(ministro_com_acesso)
    ministro_client_logado.post(URL_LOGOUT)
    for url in (OUTUBRO, GUIA_OUTUBRO):
        resposta = ministro_client_logado.get(url)
        assert resposta.status_code == 302 and resposta.url.startswith("/ministros/login/")


@pytest.mark.parametrize("url", [OUTUBRO, GUIA_OUTUBRO])
def test_cobranca_e_guia_nao_ficam_em_cache(ministro_client_logado, ministro_com_acesso, configuracao_pix, url):
    com_cobranca(ministro_com_acesso)
    resposta = ministro_client_logado.get(url)
    controle = resposta["Cache-Control"]
    for diretiva in ("no-store", "no-cache", "must-revalidate", "private", "max-age=0"):
        assert diretiva in controle, diretiva
    assert resposta.has_header("Expires")


def test_orientacoes_sao_escapadas(ministro_client_logado, ministro_com_acesso):
    cobranca.salvar_configuracao(dict(PIX_DE_TESTE, payment_instructions="<script>alert('x')</script> <b>negrito</b>"))
    com_cobranca(ministro_com_acesso)
    pagina = html(ministro_client_logado.get(OUTUBRO))
    assert "<script>alert" not in pagina and "<b>negrito</b>" not in pagina
    assert "&lt;script&gt;" in pagina
    guia_pdf = ministro_client_logado.get(GUIA_OUTUBRO)
    assert guia_pdf.status_code == 200
    assert "<script>alert" in conteudo_do_pdf(guia_pdf.content).replace("\\(", "(").replace("\\)", ")")


def test_nome_com_marcacao_vira_texto(admin_user, configuracao_pix):
    ministro = services.create_minister(
        dados=dados_de_ministro(full_name="Jose <b>Teste</b> Silva", conamad_registration="31337", field_registration=""),
        password="Pastor#Seguro2026",
    )
    com_cobranca(ministro)
    client = Client()
    assert entrar_no_portal(client, identificador="31337").status_code == 302
    pagina = html(client.get(OUTUBRO))
    assert "<b>Teste</b>" not in pagina
    assert "Jose <b>Teste</b> Silva" in conteudo_do_pdf(client.get(GUIA_OUTUBRO).content)


# ---------------------------------------------------------------------------
# Guia em PDF
# ---------------------------------------------------------------------------


def test_guia_do_portal(ministro_client_logado, ministro_com_acesso, configuracao_pix):
    _, charge = com_cobranca(ministro_com_acesso, valor="35.00")
    resposta = ministro_client_logado.get(GUIA_OUTUBRO)
    assert resposta.status_code == 200
    assert resposta["Content-Type"] == "application/pdf"
    assert resposta["Content-Disposition"] == 'attachment; filename="mensalidade-outubro-2026.pdf"'
    pdf = resposta.content
    assert pdf.startswith(b"%PDF-") and len(pdf) > 5000
    assert resposta["Content-Length"] == str(len(pdf))
    assert paginas_do_pdf(pdf) == 1

    texto = conteudo_do_pdf(pdf)
    assert "(MENSALIDADE DE MINISTRO)" in texto
    assert "OUTUBRO DE 2026" in texto
    assert "(Rodrigo Exemplo Xavier)" in texto
    assert "(7764)" in texto and "(17899)" in texto
    assert "(Sede)" in texto and "(Pastor Presidente)" in texto
    assert "(R$ 35,00)" in texto
    assert "VENCIMENTO: {}".format(DAQUI_A_10.strftime("%d/%m/%Y")) in texto
    assert "(PAGUE COM PIX)" in texto
    # O Copia e Cola inteiro, em linhas consecutivas, sem faltar caractere.
    assert charge.pix_payload in "".join(re.findall(r"\((.*?)\) Tj", texto))


def test_guia_desenha_o_qr_do_payload_gravado(ministro_client_logado, ministro_com_acesso, configuracao_pix, monkeypatch):
    _, charge = com_cobranca(ministro_com_acesso)
    recebidos = []
    original = pix.matriz_qr
    monkeypatch.setattr(pix, "matriz_qr", lambda payload: recebidos.append(payload) or original(payload))
    pdf = ministro_client_logado.get(GUIA_OUTUBRO).content
    assert recebidos == [charge.pix_payload]
    # Cada trecho escuro do QR vira um retangulo: centenas deles.
    assert conteudo_do_pdf(pdf).count(" re") > 200


def test_guia_nao_mostra_dado_administrativo(ministro_client_logado, ministro_com_acesso, configuracao_pix, admin_user):
    mensalidade, charge = com_cobranca(ministro_com_acesso)
    texto = conteudo_do_pdf(ministro_client_logado.get(GUIA_OUTUBRO).content)
    assert admin_user.full_name not in texto
    assert "({})".format(mensalidade.pk) not in texto
    assert "({})".format(charge.pk) not in texto
    assert "lote" not in texto.lower()


def test_guia_le_o_snapshot_e_e_reproduzivel(ministro_client_logado, ministro_com_acesso, configuracao_pix, admin_user):
    _, charge = com_cobranca(ministro_com_acesso)
    primeira = ministro_client_logado.get(GUIA_OUTUBRO).content

    services.update_minister(
        ministro_com_acesso,
        dados=dados_de_ministro(full_name="Rodrigo Nome Novo", position="Diácono", congregation="Jardim Novo"),
        actor=admin_user,
    )
    cobranca.salvar_configuracao(dict(PIX_DE_TESTE, pix_receiver_city="Campinas", pix_key="outra@example.com"))
    segunda = ministro_client_logado.get(GUIA_OUTUBRO).content

    assert primeira == segunda
    texto = conteudo_do_pdf(segunda)
    assert "(Rodrigo Exemplo Xavier)" in texto and "Nome Novo" not in texto
    assert "(Pastor Presidente)" in texto and "Jardim Novo" not in texto
    assert "outra@example.com" not in texto


def test_guia_tem_uma_pagina_no_pior_caso(configuracao_pix):
    cobranca.salvar_configuracao(dict(PIX_DE_TESTE, pix_key="a" * 60 + "@example.com", payment_instructions="x " * 150))
    ministro = services.create_minister(
        dados=dados_de_ministro(full_name="Nome " * 38 + "Final", congregation="C" * 120, position="P" * 100)
    )
    _, charge = com_cobranca(ministro, valor="99999999.99")
    pdf = guia.render_guia(charge)
    assert paginas_do_pdf(pdf) == 1


def test_nome_do_arquivo_nao_tem_dado_pessoal(ministro_com_acesso, configuracao_pix):
    _, charge = com_cobranca(ministro_com_acesso, competencia="2027-03")
    assert guia.nome_do_arquivo(charge) == "mensalidade-marco-2027.pdf"


def test_copiar_baixar_e_abrir_nao_mudam_a_situacao(ministro_client_logado, ministro_com_acesso, configuracao_pix):
    mensalidade, _ = com_cobranca(ministro_com_acesso)
    for _ in range(3):
        ministro_client_logado.get(OUTUBRO)
        ministro_client_logado.get(GUIA_OUTUBRO)
    assert MinisterMonthlyFee.objects.get(pk=mensalidade.pk).status == "PENDING"
    assert Decimal(MinisterFeeCharge.objects.get().amount_snapshot) == Decimal("35.00")
