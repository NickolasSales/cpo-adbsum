"""Mensalidades no Portal do Ministro (Fase 2C): somente leitura, so as proprias."""

import datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone

from audit.models import AuditLog
from ministers import mensalidades as servico
from ministers import services
from ministers.auth import SESSAO_CONTA, SESSAO_HASH
from ministers.models import MinisterAccount, MinisterMonthlyFee
from ministers.tests.conftest import (
    URL_LOGIN,
    URL_LOGOUT,
    URL_PAINEL,
    URL_TROCAR_SENHA,
    entrar_no_portal,
)

pytestmark = pytest.mark.django_db

URL = "/ministros/mensalidades/"

HOJE = timezone.localdate()
ONTEM = HOJE - datetime.timedelta(days=1)
DAQUI_A_10 = HOJE + datetime.timedelta(days=10)

# Valores e competencias que so existem para um dos dois ministros: se
# aparecerem na tela do outro, vazou.
VALORES_DE_A = ("R$ 111,11", "R$ 222,22", "R$ 333,33", "R$ 444,44", "R$ 555,55")
VALORES_DE_B = ("R$ 777,77", "R$ 888,88")
SENHA_DE_B = "Evangelista#2026x"


def nova(ministro, competencia, valor, vencimento=DAQUI_A_10, **extra):
    return servico.create_fee(
        minister=ministro, reference_month=competencia, due_date=vencimento, amount=valor, **extra
    )


@pytest.fixture
def mensalidades_de_a(ministro_com_acesso):
    """Uma mensalidade em cada situacao."""
    pendente = nova(ministro_com_acesso, "2026-11", "111.11", notes="anotacao interna da secretaria")
    vencida = nova(ministro_com_acesso, "2026-09", "222.22", vencimento=ONTEM)
    paga = nova(ministro_com_acesso, "2026-08", "333.33", vencimento=ONTEM)
    servico.register_payment(
        paga,
        paid_at=datetime.date(2026, 8, 5),
        payment_method="PIX",
        payment_reference="E2E-REFERENCIA-INTERNA",
        notes="baixa conferida no extrato",
    )
    isenta = nova(ministro_com_acesso, "2026-07", "444.44")
    servico.exempt_fee(isenta, reason="motivo interno da isencao")
    cancelada = nova(ministro_com_acesso, "2026-06", "555.55")
    servico.cancel_fee(cancelada, reason="motivo interno do cancelamento")
    return {"pendente": pendente, "vencida": vencida, "paga": paga, "isenta": isenta, "cancelada": cancelada}


@pytest.fixture
def mensalidades_de_b(outro_ministro):
    return [
        nova(outro_ministro, "2025-03", "777.77"),
        nova(outro_ministro, "2026-11", "888.88", vencimento=ONTEM),
    ]


@pytest.fixture
def cliente_de_b(outro_ministro):
    client = Client()
    assert entrar_no_portal(client, identificador="132927", senha=SENHA_DE_B).status_code == 302
    return client


def html(client, url=URL, **params):
    resposta = client.get(url, params)
    assert resposta.status_code == 200
    return resposta.content.decode()


# ---------------------------------------------------------------------------
# Autenticacao
# ---------------------------------------------------------------------------


def test_nao_autenticado_vai_para_o_login_do_portal(client):
    resposta = client.get(URL)
    assert resposta.status_code == 302
    assert resposta.url == "{}?next={}".format(URL_LOGIN, URL)


def test_ministro_autenticado_ve_a_tela(ministro_client_logado):
    resposta = ministro_client_logado.get(URL)
    assert resposta.status_code == 200
    assert resposta.templates[0].name == "ministers/portal/mensalidades.html"


def test_login_com_next_leva_as_mensalidades(client, ministro_com_acesso):
    resposta = client.post(
        URL_LOGIN + "?next=" + URL, {"identificador": "17899", "senha": "Pastor#Seguro2026", "next": URL}
    )
    assert resposta.url == URL


def test_conta_bloqueada_perde_o_acesso(ministro_client_logado, ministro_com_acesso, mensalidades_de_a):
    services.block_account(ministro_com_acesso)
    resposta = ministro_client_logado.get(URL)
    assert resposta.status_code == 302 and resposta.url.startswith(URL_LOGIN)


def test_ministro_inativo_perde_o_acesso_mesmo_com_mensalidades(
    ministro_client_logado, ministro_com_acesso, mensalidades_de_a
):
    services.deactivate_minister(ministro_com_acesso)
    resposta = ministro_client_logado.get(URL)
    assert resposta.status_code == 302 and resposta.url.startswith(URL_LOGIN)
    # As mensalidades continuam no banco; so o acesso acabou.
    assert MinisterMonthlyFee.objects.filter(minister=ministro_com_acesso).count() == 5


def test_troca_de_senha_pendente_bloqueia_as_mensalidades(ministro_client_logado, ministro_com_acesso):
    MinisterAccount.objects.filter(minister=ministro_com_acesso).update(must_change_password=True)
    resposta = ministro_client_logado.get(URL)
    assert resposta.status_code == 302 and resposta.url == URL_TROCAR_SENHA


def test_reset_de_senha_pelo_admin_derruba_a_sessao(ministro_client_logado, ministro_com_acesso):
    services.reset_password(ministro_com_acesso, new_password="Redefinida#2026x")
    resposta = ministro_client_logado.get(URL)
    assert resposta.status_code == 302 and resposta.url.startswith(URL_LOGIN)


def test_sessao_com_assinatura_invalida(ministro_client_logado):
    sessao = ministro_client_logado.session
    sessao[SESSAO_HASH] = "assinatura-forjada"
    sessao.save()
    resposta = ministro_client_logado.get(URL)
    assert resposta.status_code == 302 and resposta.url.startswith(URL_LOGIN)


def test_trocar_a_conta_na_sessao_nao_entrega_o_outro_ministro(
    ministro_client_logado, outro_ministro, mensalidades_de_a, mensalidades_de_b
):
    # Sessao de A apontando para a conta de B: a assinatura e da senha de A,
    # nao confere com a de B, e a sessao do portal cai inteira.
    sessao = ministro_client_logado.session
    sessao[SESSAO_CONTA] = MinisterAccount.objects.get(minister=outro_ministro).pk
    sessao.save()
    resposta = ministro_client_logado.get(URL)
    assert resposta.status_code == 302 and resposta.url.startswith(URL_LOGIN)
    assert SESSAO_CONTA not in ministro_client_logado.session


def test_logout_encerra_o_acesso_as_mensalidades(ministro_client_logado, mensalidades_de_a):
    assert ministro_client_logado.get(URL).status_code == 200
    ministro_client_logado.post(URL_LOGOUT)
    resposta = ministro_client_logado.get(URL)
    assert resposta.status_code == 302 and resposta.url.startswith(URL_LOGIN)


# ---------------------------------------------------------------------------
# Isolamento entre ministros
# ---------------------------------------------------------------------------


def _so_de_a(pagina):
    for valor in VALORES_DE_A:
        assert valor in pagina, valor
    for valor in VALORES_DE_B:
        assert valor not in pagina, valor
    assert "Março/2025" not in pagina
    assert "Bruna Exemplo Souza" not in pagina


def test_a_ve_so_as_de_a(ministro_client_logado, ministro_com_acesso, mensalidades_de_a, mensalidades_de_b):
    resposta = ministro_client_logado.get(URL)
    _so_de_a(resposta.content.decode())
    assert {m.minister_id for m in resposta.context["mensalidades"]} == {ministro_com_acesso.pk}
    assert resposta.context["totais"]["qtd_total"] == 5


def test_b_ve_so_as_de_b(cliente_de_b, outro_ministro, mensalidades_de_a, mensalidades_de_b):
    resposta = cliente_de_b.get(URL)
    pagina = resposta.content.decode()
    for valor in VALORES_DE_B:
        assert valor in pagina
    for valor in VALORES_DE_A:
        assert valor not in pagina
    assert "Rodrigo Exemplo Xavier" not in pagina
    assert {m.minister_id for m in resposta.context["mensalidades"]} == {outro_ministro.pk}
    assert resposta.context["totais"]["qtd_total"] == 2


def test_os_dois_logados_ao_mesmo_tempo(
    ministro_client_logado, cliente_de_b, mensalidades_de_a, mensalidades_de_b
):
    _so_de_a(html(ministro_client_logado))
    assert "R$ 777,77" in html(cliente_de_b)
    _so_de_a(html(ministro_client_logado))


@pytest.mark.parametrize("nome", ["ministro", "minister", "minister_id", "conta", "account", "id", "pk", "q"])
def test_parametros_na_query_string_sao_ignorados(
    ministro_client_logado, outro_ministro, mensalidades_de_a, mensalidades_de_b, nome
):
    conta_de_b = MinisterAccount.objects.get(minister=outro_ministro)
    for valor in (outro_ministro.pk, conta_de_b.pk, outro_ministro.conamad_registration):
        _so_de_a(html(ministro_client_logado, **{nome: valor}))


def test_parametros_combinados_e_competencia_de_b(
    ministro_client_logado, outro_ministro, mensalidades_de_a, mensalidades_de_b
):
    pagina = html(
        ministro_client_logado,
        ministro=outro_ministro.pk,
        competencia="2025-03",
        situacao="PENDING",
        page=2,
    )
    _so_de_a(pagina)


def test_post_nao_e_aceito_e_nao_altera_nada(
    ministro_client_logado, outro_ministro, mensalidades_de_a, mensalidades_de_b
):
    antes = list(MinisterMonthlyFee.objects.order_by("id").values())
    eventos = AuditLog.objects.count()
    for dados in ({}, {"ministro": outro_ministro.pk}, {"status": "PAID", "amount": "1.00"}):
        resposta = ministro_client_logado.post(URL, dados)
        assert resposta.status_code == 405
        assert b"777,77" not in resposta.content
    assert list(MinisterMonthlyFee.objects.order_by("id").values()) == antes
    assert AuditLog.objects.count() == eventos


def test_outros_metodos_tambem_nao(ministro_client_logado, mensalidades_de_a):
    assert ministro_client_logado.put(URL).status_code == 405
    assert ministro_client_logado.delete(URL).status_code == 405


def test_nao_existe_rota_por_id(ministro_client_logado, outro_ministro, mensalidades_de_b):
    fee_de_b = mensalidades_de_b[0]
    for rota in (
        "/ministros/mensalidades/{}/".format(fee_de_b.pk),
        "/ministros/{}/mensalidades/".format(outro_ministro.pk),
        "/ministros/mensalidades/{}/".format(outro_ministro.pk),
        "/ministros/mensalidades/2025-03/",
    ):
        assert ministro_client_logado.get(rota).status_code == 404, rota


def test_rotas_administrativas_das_mensalidades_de_b(ministro_client_logado, mensalidades_de_b):
    fee_de_b = mensalidades_de_b[0]
    for rota in (
        "/admin-panel/ministros/mensalidades/",
        "/admin-panel/ministros/mensalidades/lista/",
        "/admin-panel/ministros/mensalidades/{}/".format(fee_de_b.pk),
        "/admin-panel/ministros/mensalidades/gerar/",
        "/django-admin/ministers/ministermonthlyfee/{}/change/".format(fee_de_b.pk),
    ):
        resposta = ministro_client_logado.get(rota)
        assert resposta.status_code == 302, rota
        assert b"777,77" not in resposta.content


# ---------------------------------------------------------------------------
# Outras identidades
# ---------------------------------------------------------------------------


def test_aluno_do_cpo_nao_acessa(student_client_logado, mensalidades_de_a):
    resposta = student_client_logado.get(URL)
    assert resposta.status_code == 302 and resposta.url.startswith(URL_LOGIN)


def test_admin_do_cpo_e_visitante_no_portal(admin_client_logado, mensalidades_de_a):
    # O ADMIN ve as mensalidades no painel administrativo; o portal e so do ministro.
    resposta = admin_client_logado.get(URL)
    assert resposta.status_code == 302 and resposta.url.startswith(URL_LOGIN)
    assert admin_client_logado.get("/admin-panel/ministros/mensalidades/lista/").status_code == 200


# ---------------------------------------------------------------------------
# Situacoes, totais e historico
# ---------------------------------------------------------------------------


def test_cada_situacao_aparece_com_o_rotulo_certo(ministro_client_logado, mensalidades_de_a):
    resposta = ministro_client_logado.get(URL)
    situacoes = {m.reference_month.strftime("%Y-%m"): m.situacao for m in resposta.context["mensalidades"]}
    assert situacoes == {
        "2026-11": "PENDING",
        "2026-09": "OVERDUE",
        "2026-08": "PAID",
        "2026-07": "EXEMPT",
        "2026-06": "CANCELLED",
    }
    pagina = resposta.content.decode()
    for rotulo in ("Pendente", "Vencido", "Pago", "Isento", "Cancelado"):
        assert ">{}</span>".format(rotulo) in pagina


def test_vencido_e_calculado(ministro_client_logado, ministro_com_acesso):
    m = nova(ministro_com_acesso, "2026-05", "50.00", vencimento=ONTEM)
    assert m.status == "PENDING"
    assert 'cpo-badge--erro">Vencido' in html(ministro_client_logado)


def test_totais_so_contam_pendente_e_vencido_como_divida(ministro_client_logado, mensalidades_de_a):
    contexto = ministro_client_logado.get(URL).context
    t = contexto["totais"]
    assert t["pendente"] == Decimal("111.11")
    assert t["vencido"] == Decimal("222.22")
    assert t["recebido"] == Decimal("333.33")
    assert contexto["em_aberto"] == Decimal("333.33")  # pendente + vencido; paga, isenta e cancelada fora
    assert (t["qtd_total"], t["qtd_pagas"], t["qtd_pendentes"], t["qtd_vencidas"]) == (5, 1, 1, 1)
    pagina = ministro_client_logado.get(URL).content.decode()
    assert 'Total em aberto (pendentes + vencidas): <strong class="text-nowrap">R$ 333,33</strong>' in pagina
    assert "1 mensalidade" in pagina and "R$ 222,22</strong>" in pagina


def test_isenta_e_cancelada_nao_entram_na_divida(ministro_client_logado, ministro_com_acesso):
    isenta = nova(ministro_com_acesso, "2026-07", "40.00")
    servico.exempt_fee(isenta, reason="x")
    cancelada = nova(ministro_com_acesso, "2026-06", "60.00", vencimento=ONTEM)
    servico.cancel_fee(cancelada, reason="x")
    contexto = ministro_client_logado.get(URL).context
    assert contexto["em_aberto"] == Decimal("0.00")
    assert contexto["totais"]["qtd_vencidas"] == 0
    assert contexto["totais"]["qtd_total"] == 2
    pagina = ministro_client_logado.get(URL).content.decode()
    assert "Total em aberto" not in pagina
    assert "alert-danger" not in pagina


def test_historico_ordenado_e_formatado(ministro_client_logado, mensalidades_de_a):
    resposta = ministro_client_logado.get(URL)
    competencias = [m.reference_month for m in resposta.context["mensalidades"]]
    assert competencias == sorted(competencias, reverse=True)
    pagina = resposta.content.decode()
    ordem = [pagina.index(rotulo) for rotulo in ("Novembro/2026", "Setembro/2026", "Agosto/2026", "Julho/2026", "Junho/2026")]
    assert ordem == sorted(ordem)
    assert DAQUI_A_10.strftime("%d/%m/%Y") in pagina
    assert "R$ 111,11" in pagina


def test_pagamento_mostra_data_e_forma(ministro_client_logado, mensalidades_de_a):
    pagina = html(ministro_client_logado)
    assert "05/08/2026 &middot; Pix" in pagina


def test_sem_pagamento_mostra_traco_so_no_desktop(ministro_client_logado, mensalidades_de_a):
    pagina = html(ministro_client_logado)
    assert 'd-none d-md-table-cell text-body-secondary" data-rotulo="Pagamento">&mdash;' in pagina


def test_informacoes_internas_nao_aparecem(ministro_client_logado, admin_user, ministro_com_acesso):
    m = nova(ministro_com_acesso, "2026-10", "50.00", notes="anotacao interna da secretaria", actor=admin_user)
    servico.register_payment(
        m, paid_at=HOJE, payment_method="TRANSFER", payment_reference="E2E-REFERENCIA-INTERNA",
        notes="baixa conferida", actor=admin_user,
    )
    servico.reverse_payment(m, reason="motivo interno do estorno", actor=admin_user)
    pagina = html(ministro_client_logado)
    for interno in (
        "anotacao interna",
        "baixa conferida",
        "E2E-REFERENCIA-INTERNA",
        "motivo interno",
        admin_user.full_name,
        admin_user.email,
        "/admin-panel/",
        "Registrar pagamento",
        "Lote ",
        " lote",
        'href="/ministros/mensalidades/{}'.format(m.pk),
    ):
        assert interno not in pagina, interno


def test_consulta_nao_carrega_colunas_internas(ministro_client_logado, mensalidades_de_a):
    mensalidade = ministro_client_logado.get(URL).context["mensalidades"][0]
    adiadas = mensalidade.get_deferred_fields()
    assert {"notes", "status_reason", "payment_reference"} <= adiadas


def test_sem_mensalidades(ministro_client_logado):
    pagina = html(ministro_client_logado)
    assert "Você ainda não possui mensalidades registradas." in pagina
    assert "<table" not in pagina
    assert "cpo-indicador" not in pagina


def test_visualizar_nao_grava_nada(ministro_client_logado, mensalidades_de_a):
    eventos = AuditLog.objects.count()
    antes = list(MinisterMonthlyFee.objects.order_by("id").values())
    html(ministro_client_logado)
    html(ministro_client_logado, url=URL_PAINEL)
    assert AuditLog.objects.count() == eventos
    assert list(MinisterMonthlyFee.objects.order_by("id").values()) == antes


# ---------------------------------------------------------------------------
# Inicio do portal
# ---------------------------------------------------------------------------


def test_inicio_tem_resumo_e_acesso(ministro_client_logado, mensalidades_de_a):
    resposta = ministro_client_logado.get(URL_PAINEL)
    resumo = resposta.context["resumo"]
    assert (resumo["qtd_vencidas"], resumo["qtd_pendentes"], resumo["qtd_pagas"]) == (1, 1, 1)
    pagina = resposta.content.decode()
    assert 'href="{}"'.format(URL) in pagina
    assert "Ver mensalidades" in pagina
    assert "Valor vencido" in pagina and "R$ 222,22" in pagina


def test_inicio_sem_mensalidades(ministro_client_logado):
    pagina = html(ministro_client_logado, url=URL_PAINEL)
    assert "Você ainda não possui mensalidades registradas." in pagina
    assert "Ver mensalidades" in pagina


def test_inicio_de_a_nao_mostra_numeros_de_b(ministro_client_logado, mensalidades_de_b):
    resumo = ministro_client_logado.get(URL_PAINEL).context["resumo"]
    assert resumo["qtd_total"] == 0


def test_menu_da_conta_tem_mensalidades(ministro_client_logado):
    assert 'class="dropdown-item" href="{}"'.format(URL) in html(ministro_client_logado, url=URL_PAINEL)


# ---------------------------------------------------------------------------
# Cache e privacidade
# ---------------------------------------------------------------------------


def test_pagina_financeira_nao_fica_em_cache(ministro_client_logado, mensalidades_de_a):
    for rota in (URL, URL_PAINEL):
        resposta = ministro_client_logado.get(rota)
        controle = resposta["Cache-Control"]
        for diretiva in ("no-store", "no-cache", "must-revalidate", "private", "max-age=0"):
            assert diretiva in controle, (rota, diretiva)
        assert resposta.has_header("Expires")


def test_redirecionamento_sem_sessao_tambem_nao_fica_em_cache(client):
    assert "no-store" in client.get(URL)["Cache-Control"]


# ---------------------------------------------------------------------------
# Desempenho e layout
# ---------------------------------------------------------------------------


def _consultas(client):
    with CaptureQueriesContext(connection) as ctx:
        assert client.get(URL).status_code == 200
    return len(ctx.captured_queries)


def test_numero_de_consultas_nao_cresce_com_o_historico(ministro_client_logado, ministro_com_acesso):
    nova(ministro_com_acesso, "2020-01", "50.00")
    poucas = _consultas(ministro_client_logado)
    for mes in range(2, 13):
        nova(ministro_com_acesso, "2020-{:02d}".format(mes), "50.00")
    for mes in range(1, 13):
        nova(ministro_com_acesso, "2021-{:02d}".format(mes), "50.00")
    assert _consultas(ministro_client_logado) == poucas


def test_lista_vira_cartoes_no_celular(ministro_client_logado, mensalidades_de_a):
    pagina = html(ministro_client_logado)
    assert "cpo-lista--cartoes" in pagina
    for rotulo in ("Vencimento", "Valor", "Situação", "Pagamento"):
        assert 'data-rotulo="{}"'.format(rotulo) in pagina
    assert "cpo-lateral" not in pagina


def test_nao_ha_cobranca_pdf_nem_pix(ministro_client_logado, mensalidades_de_a):
    pagina = html(ministro_client_logado).lower()
    # Termos inteiros: a pagina tem um token CSRF aleatorio, onde "qr" ou
    # "pdf" sozinhos poderiam aparecer por acaso.
    for termo in (".pdf", "qr code", "qrcode", "copia e cola", "baixar", "cobrança", "pagar agora"):
        assert termo not in pagina, termo
