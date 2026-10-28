"""Telas administrativas de mensalidades: permissoes, filtros, acoes."""

import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from audit.models import AuditEvent, AuditLog
from ministers import mensalidades as servico
from ministers import services
from ministers.models import MinisterMonthlyFee, MonthlyFeeStatus
from ministers.tests.conftest import dados_de_ministro

pytestmark = pytest.mark.django_db

HOJE = timezone.localdate()
ONTEM = HOJE - datetime.timedelta(days=1)
AMANHA = HOJE + datetime.timedelta(days=1)

URL_DASHBOARD = "/admin-panel/ministros/mensalidades/"
URL_LISTA = "/admin-panel/ministros/mensalidades/lista/"
URL_NOVA = "/admin-panel/ministros/mensalidades/nova/"


def url(m, sufixo=""):
    return "/admin-panel/ministros/mensalidades/{}/{}".format(m.pk, sufixo)


def nova(ministro, competencia="2026-10", vencimento=AMANHA, valor="35.00", **extra):
    return servico.create_fee(
        minister=ministro, reference_month=competencia, due_date=vencimento, amount=valor, **extra
    )


@pytest.fixture
def mensalidade(ministro):
    return nova(ministro)


# ---------------------------------------------------------------------------
# Permissoes
# ---------------------------------------------------------------------------


def _rotas(m):
    return [
        URL_DASHBOARD,
        URL_LISTA,
        URL_NOVA,
        url(m),
        url(m, "editar/"),
        url(m, "pagamento/"),
        url(m, "pagamento/corrigir/"),
        url(m, "pagamento/estornar/"),
        url(m, "cancelar/"),
        url(m, "isentar/"),
        url(m, "reativar/"),
    ]


def test_aluno_recebe_403_em_todas(student_client_logado, mensalidade):
    for rota in _rotas(mensalidade):
        assert student_client_logado.get(rota).status_code == 403, rota
        assert student_client_logado.post(rota, {"reason": "x"}).status_code == 403, rota
    mensalidade.refresh_from_db()
    assert mensalidade.status == MonthlyFeeStatus.PENDING


def test_ministro_logado_no_portal_nao_acessa(ministro_client_logado, ministro_com_acesso):
    # A mensalidade e do proprio ministro logado: nem a dele ele ve pelo painel.
    mensalidade = nova(ministro_com_acesso)
    for rota in _rotas(mensalidade):
        resposta = ministro_client_logado.get(rota)
        assert resposta.status_code == 302, rota
        assert resposta.url.startswith("/login/"), rota


def test_anonimo_vai_para_o_login(client, mensalidade):
    for rota in _rotas(mensalidade):
        resposta = client.get(rota)
        assert resposta.status_code == 302 and resposta.url.startswith("/login/"), rota


def test_portal_so_tem_a_rota_financeira_do_proprio_ministro(ministro_client_logado, ministro_com_acesso):
    # Ate a 2B o portal nao tinha financeiro (404). Na 2C ganhou UMA rota,
    # sem id: as mensalidades do ministro da sessao. Rota por id nao existe.
    mensalidade = nova(ministro_com_acesso)
    assert ministro_client_logado.get("/ministros/mensalidades/").status_code == 200
    assert ministro_client_logado.get("/ministros/mensalidades/{}/".format(mensalidade.pk)).status_code == 404


# ---------------------------------------------------------------------------
# Menu e dashboard
# ---------------------------------------------------------------------------


def test_menu_tem_mensalidades_na_area_de_ministros(admin_client_logado):
    html = admin_client_logado.get(URL_DASHBOARD).content.decode()
    assert 'href="/admin-panel/ministros/mensalidades/"' in html
    assert html.index(">Ministros</div>") < html.index(">Mensalidades</a>") < html.index(">Administração</div>")


def test_dashboard_filtra_por_competencia(admin_client_logado, ministro, outro_ministro):
    servico.register_payment(nova(ministro, valor="35.00"), paid_at=HOJE, payment_method="PIX")
    nova(outro_ministro, valor="50.00", vencimento=ONTEM)
    nova(ministro, competencia="2026-09", valor="10.00")

    t = admin_client_logado.get(URL_DASHBOARD + "?competencia=2026-10").context["totais"]
    assert (t["previsto"], t["recebido"], t["vencido"]) == (Decimal("85.00"), Decimal("35.00"), Decimal("50.00"))
    assert t["qtd_ministros"] == 2

    todas = admin_client_logado.get(URL_DASHBOARD + "?competencia=todas").context["totais"]
    assert todas["previsto"] == Decimal("95.00")


def test_dashboard_sem_escolha_abre_no_mes_corrente(admin_client_logado, ministro):
    mes = HOJE.replace(day=1)
    futuro = (mes + datetime.timedelta(days=40)).replace(day=1)
    nova(ministro, competencia=futuro)
    contexto = admin_client_logado.get(URL_DASHBOARD).context
    assert contexto["competencia_chave"] == mes.strftime("%Y-%m")
    # O mes corrente aparece no seletor mesmo sem mensalidade.
    assert (mes.strftime("%Y-%m"), mes.strftime("%m/%Y")) in contexto["competencias"]


def test_dashboard_vazio(admin_client_logado):
    resposta = admin_client_logado.get(URL_DASHBOARD)
    assert resposta.status_code == 200
    assert "Nenhuma mensalidade" in resposta.content.decode()


def test_dashboard_mostra_reais(admin_client_logado, ministro):
    nova(ministro, valor="1234.50")
    assert "R$ 1.234,50" in admin_client_logado.get(URL_DASHBOARD).content.decode()


# ---------------------------------------------------------------------------
# Lista, filtros e busca
# ---------------------------------------------------------------------------


@pytest.fixture
def cenario(ministro, outro_ministro):
    terceiro = services.create_minister(
        dados=dados_de_ministro(
            full_name="Carlos Terceiro", conamad_registration="333", field_registration="0042", congregation="Vila Nova"
        )
    )
    paga = servico.register_payment(nova(ministro), paid_at=HOJE, payment_method="PIX")
    vencida = nova(outro_ministro, vencimento=ONTEM)
    a_vencer = nova(terceiro, vencimento=AMANHA)
    setembro = nova(terceiro, competencia="2026-09")
    return {"paga": paga, "vencida": vencida, "a_vencer": a_vencer, "setembro": setembro}


@pytest.mark.parametrize(
    "filtro, esperado",
    [
        ("", {"paga", "vencida", "a_vencer", "setembro"}),
        ("competencia=2026-10", {"paga", "vencida", "a_vencer"}),
        ("competencia=2026-09", {"setembro"}),
        ("situacao=PAID", {"paga"}),
        ("situacao=OVERDUE", {"vencida"}),
        ("situacao=PENDING", {"a_vencer", "setembro"}),
        ("q=carlos", {"a_vencer", "setembro"}),
        ("q=132927", {"vencida"}),
        ("q=0042", {"a_vencer", "setembro"}),
        ("congregacao=Sede", {"paga"}),
        ("competencia=lixo&situacao=nada", {"paga", "vencida", "a_vencer", "setembro"}),
    ],
)
def test_filtros_e_busca(admin_client_logado, cenario, filtro, esperado):
    resposta = admin_client_logado.get(URL_LISTA + "?" + filtro)
    ids = {m.pk for m in resposta.context["mensalidades"]}
    assert ids == {cenario[nome].pk for nome in esperado}


def test_filtro_por_ministro(admin_client_logado, cenario, ministro):
    resposta = admin_client_logado.get(URL_LISTA + "?ministro={}".format(ministro.pk))
    assert [m.pk for m in resposta.context["mensalidades"]] == [cenario["paga"].pk]


def test_lista_responsiva_e_com_colunas(admin_client_logado, cenario):
    html = admin_client_logado.get(URL_LISTA).content.decode()
    assert "cpo-lista--cartoes" in html
    for rotulo in ("CONAMAD / Campo", "Competência", "Valor", "Vencimento", "Situação"):
        assert 'data-rotulo="{}"'.format(rotulo) in html
    # Matricula do campo e congregacao aparecem junto do CONAMAD e do nome.
    assert "Campo 0042" in html and "Vila Nova" in html
    assert ">Vencido<" in html and ">Pago<" in html and ">Pendente<" in html
    assert "Registrar pagamento" in html


def test_lista_pagina(admin_client_logado, ministro):
    for indice in range(30):
        ano, mes = divmod(indice, 12)
        nova(ministro, competencia="{}-{:02d}".format(2024 + ano, mes + 1))
    resposta = admin_client_logado.get(URL_LISTA)
    assert len(resposta.context["mensalidades"]) == 25
    assert resposta.context["page_obj"].paginator.num_pages == 2


# ---------------------------------------------------------------------------
# Cadastro manual
# ---------------------------------------------------------------------------


def _form(ministro, **extra):
    dados = {
        "minister": ministro.pk,
        "reference_month": "2026-10",
        "due_date": "2026-10-10",
        "amount": "35,00",
        "notes": "",
    }
    dados.update(extra)
    return dados


def test_cadastro_manual(admin_client_logado, ministro, admin_user):
    resposta = admin_client_logado.post(URL_NOVA, _form(ministro))
    m = MinisterMonthlyFee.objects.get()
    assert resposta.status_code == 302 and resposta.url == url(m)
    assert m.amount == Decimal("35.00")
    assert m.reference_month == datetime.date(2026, 10, 1)
    assert AuditLog.objects.get(event=AuditEvent.MINISTER_FEE_CREATED).actor == admin_user


def test_cadastro_duplicado_mostra_erro(admin_client_logado, ministro, mensalidade):
    resposta = admin_client_logado.post(URL_NOVA, _form(ministro))
    assert resposta.status_code == 200
    assert "já tem mensalidade" in resposta.content.decode()
    assert MinisterMonthlyFee.objects.count() == 1


@pytest.mark.parametrize(
    "campo, valor",
    [("reference_month", "Out/26"), ("amount", "0"), ("amount", "abc"), ("due_date", ""), ("minister", "999999")],
)
def test_cadastro_valida_campos(admin_client_logado, ministro, campo, valor):
    resposta = admin_client_logado.post(URL_NOVA, _form(ministro, **{campo: valor}))
    assert resposta.status_code == 200
    assert not MinisterMonthlyFee.objects.exists()


def test_cadastro_para_inativo_exige_confirmacao(admin_client_logado, ministro):
    services.deactivate_minister(ministro)
    resposta = admin_client_logado.post(URL_NOVA, _form(ministro))
    assert resposta.status_code == 200 and not MinisterMonthlyFee.objects.exists()

    admin_client_logado.post(URL_NOVA, _form(ministro, confirmar_inativo="on"))
    assert MinisterMonthlyFee.objects.count() == 1


def test_cadastro_nao_aceita_situacao_do_navegador(admin_client_logado, ministro):
    admin_client_logado.post(
        URL_NOVA, _form(ministro, status="PAID", paid_at="2026-10-01", payment_method="PIX")
    )
    m = MinisterMonthlyFee.objects.get()
    assert m.status == MonthlyFeeStatus.PENDING and m.paid_at is None


def test_formulario_sugere_o_ultimo_valor_do_ministro(admin_client_logado, ministro):
    nova(ministro, competencia="2026-09", valor="42.00")
    form = admin_client_logado.get(URL_NOVA + "?ministro={}".format(ministro.pk)).context["form"]
    assert form.initial["amount"] == Decimal("42.00")
    assert form.initial["minister"] == ministro


def test_formulario_sem_ministro_nao_sugere_valor(admin_client_logado):
    form = admin_client_logado.get(URL_NOVA).context["form"]
    assert "amount" not in form.initial


# ---------------------------------------------------------------------------
# Acoes
# ---------------------------------------------------------------------------


def test_registrar_pagamento_pela_tela(admin_client_logado, mensalidade):
    resposta = admin_client_logado.post(
        url(mensalidade, "pagamento/"),
        {"paid_at": HOJE.isoformat(), "payment_method": "TRANSFER", "payment_reference": "TED 99"},
    )
    assert resposta.status_code == 302
    mensalidade.refresh_from_db()
    assert mensalidade.status == MonthlyFeeStatus.PAID
    assert mensalidade.payment_reference == "TED 99"


def test_pagamento_invalido_volta_com_erro(admin_client_logado, mensalidade):
    resposta = admin_client_logado.post(
        url(mensalidade, "pagamento/"), {"paid_at": AMANHA.isoformat(), "payment_method": "PIX"}
    )
    assert resposta.status_code == 200
    assert "futuro" in resposta.content.decode()


def test_editar_pela_tela(admin_client_logado, mensalidade):
    admin_client_logado.post(
        url(mensalidade, "editar/"),
        {"due_date": "2026-10-20", "amount": "40,00", "notes": "Ajuste"},
    )
    mensalidade.refresh_from_db()
    assert mensalidade.amount == Decimal("40.00")
    assert mensalidade.due_date == datetime.date(2026, 10, 20)


def test_corrigir_e_estornar_pela_tela(admin_client_logado, mensalidade):
    servico.register_payment(mensalidade, paid_at=ONTEM, payment_method="PIX")
    admin_client_logado.post(
        url(mensalidade, "pagamento/corrigir/"),
        {"paid_at": HOJE.isoformat(), "payment_method": "CASH", "payment_reference": "", "reason": "Recibo em dinheiro"},
    )
    mensalidade.refresh_from_db()
    assert mensalidade.payment_method == "CASH"

    admin_client_logado.post(url(mensalidade, "pagamento/estornar/"), {"reason": "Pagamento não compensou"})
    mensalidade.refresh_from_db()
    assert mensalidade.status == MonthlyFeeStatus.PENDING

    eventos = set(AuditLog.objects.values_list("event", flat=True))
    assert AuditEvent.MINISTER_FEE_PAYMENT_UPDATED in eventos
    assert AuditEvent.MINISTER_FEE_PAYMENT_REVERSED in eventos


@pytest.mark.parametrize("sufixo, situacao", [("cancelar/", "CANCELLED"), ("isentar/", "EXEMPT")])
def test_cancelar_e_isentar_pela_tela(admin_client_logado, mensalidade, sufixo, situacao):
    admin_client_logado.post(url(mensalidade, sufixo), {"reason": "Decisão da diretoria"})
    mensalidade.refresh_from_db()
    assert mensalidade.status == situacao

    admin_client_logado.post(url(mensalidade, "reativar/"), {"reason": "Revisto"})
    mensalidade.refresh_from_db()
    assert mensalidade.status == MonthlyFeeStatus.PENDING


def test_acao_sem_motivo_nao_altera(admin_client_logado, mensalidade):
    resposta = admin_client_logado.post(url(mensalidade, "cancelar/"), {"reason": ""})
    assert resposta.status_code == 200
    mensalidade.refresh_from_db()
    assert mensalidade.status == MonthlyFeeStatus.PENDING


def test_acao_fora_de_ordem_e_recusada(admin_client_logado, mensalidade):
    resposta = admin_client_logado.post(url(mensalidade, "pagamento/estornar/"), {"reason": "x"})
    assert resposta.status_code == 200
    assert "Não é possível" in resposta.content.decode()


def test_ficha_mostra_historico_com_motivo(admin_client_logado, mensalidade):
    servico.cancel_fee(mensalidade, reason="Motivo <b>visível</b>")
    html = admin_client_logado.get(url(mensalidade)).content.decode()
    assert "Mensalidade cancelada" in html
    assert "Motivo &lt;b&gt;visível&lt;/b&gt;" in html
    assert "Reativar" in html


def test_ficha_inexistente(admin_client_logado):
    assert admin_client_logado.get("/admin-panel/ministros/mensalidades/999999/").status_code == 404


def test_ficha_do_ministro_leva_as_mensalidades(admin_client_logado, ministro):
    html = admin_client_logado.get("/admin-panel/ministros/{}/".format(ministro.pk)).content.decode()
    assert "/admin-panel/ministros/mensalidades/lista/?ministro={}".format(ministro.pk) in html


def test_historico_mostra_rotulos_e_valores_legiveis(admin_client_logado, mensalidade):
    servico.update_fee(mensalidade, due_date=mensalidade.due_date, amount="1234.50", notes="")
    html = admin_client_logado.get(url(mensalidade)).content.decode()
    assert "Antes:" in html and "Valor R$ 35,00" in html
    assert "Depois:" in html and "Valor R$ 1.234,50" in html
    assert "amount" not in html
