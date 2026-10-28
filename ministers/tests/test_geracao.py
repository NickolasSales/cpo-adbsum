"""Geracao de mensalidades em massa (Fase 2B)."""

import datetime
import threading
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from audit.models import AuditEvent, AuditLog
from common.exceptions import DomainError
from ministers import geracao
from ministers import mensalidades as servico
from ministers import services
from ministers.models import MinisterFeeBatch, MinisterMonthlyFee, MonthlyFeeStatus
from ministers.tests.conftest import dados_de_ministro

pytestmark = pytest.mark.django_db

HOJE = timezone.localdate()
COMPETENCIA = datetime.date(2026, 11, 1)
VENCIMENTO = datetime.date(2026, 11, 10)

URL_GERAR = "/admin-panel/ministros/mensalidades/gerar/"
URL_PREVIEW = "/admin-panel/ministros/mensalidades/gerar/preview/"
URL_CONFIRMAR = "/admin-panel/ministros/mensalidades/gerar/confirmar/"
URL_CANCELAR = "/admin-panel/ministros/mensalidades/gerar/cancelar/"


def criar_ministros(quantidade, inicio=1000, ativo=True):
    criados = []
    for numero in range(inicio, inicio + quantidade):
        ministro = services.create_minister(
            dados=dados_de_ministro(
                full_name="Ministro {:04d}".format(numero),
                conamad_registration=str(numero),
                field_registration="",
            )
        )
        if not ativo:
            services.deactivate_minister(ministro)
        criados.append(ministro)
    return criados


def parametros(valor="50.00", competencia=COMPETENCIA, vencimento=VENCIMENTO):
    return geracao.validar_parametros(competencia, vencimento, valor)


def gerar(valor="50.00", token=None, actor=None, **kwargs):
    return geracao.gerar(parametros(valor, **kwargs), token=token or geracao.novo_token(), actor=actor)


def form(**extra):
    dados = {"reference_month": "2026-11", "due_date": "2026-11-10", "amount": "50,00"}
    dados.update(extra)
    return dados


def preparar_preview(client, **extra):
    resposta = client.post(URL_GERAR, form(**extra))
    assert resposta.status_code == 302 and resposta.url == URL_PREVIEW
    return client.get(URL_PREVIEW)


def contagens():
    return (
        MinisterMonthlyFee.objects.count(),
        MinisterFeeBatch.objects.count(),
        AuditLog.objects.filter(event__startswith="MINISTER_FEE").count(),
    )


# ---------------------------------------------------------------------------
# Parametros
# ---------------------------------------------------------------------------


def test_parametros_validos():
    p = parametros()
    assert p.competencia == COMPETENCIA
    assert p.valor == Decimal("50.00") and isinstance(p.valor, Decimal)


@pytest.mark.parametrize(
    "competencia, vencimento, valor",
    [
        ("Novembro", VENCIMENTO, "50"),
        ("2026-13", VENCIMENTO, "50"),
        ("2026-11", None, "50"),
        ("2026-11", VENCIMENTO, ""),
        ("2026-11", VENCIMENTO, "0"),
        ("2026-11", VENCIMENTO, "-10"),
    ],
)
def test_parametros_invalidos(competencia, vencimento, valor):
    with pytest.raises(DomainError):
        geracao.validar_parametros(competencia, vencimento, valor)


@pytest.mark.parametrize(
    "campo, valor",
    [
        ("reference_month", "Nov/26"),
        ("reference_month", ""),
        ("due_date", ""),
        ("amount", ""),
        ("amount", "0"),
        ("amount", "-5"),
        ("amount", "abc"),
    ],
)
def test_formulario_recusa_e_nao_abre_preview(admin_client_logado, campo, valor):
    resposta = admin_client_logado.post(URL_GERAR, form(**{campo: valor}))
    assert resposta.status_code == 200
    assert "geracao_mensalidades" not in admin_client_logado.session


def test_formulario_sugere_mes_corrente_e_nao_sugere_vencimento(admin_client_logado):
    form_ = admin_client_logado.get(URL_GERAR).context["form"]
    assert form_.initial["reference_month"] == HOJE.strftime("%Y-%m")
    assert "due_date" not in form_.initial
    assert "amount" not in form_.initial


def test_valor_sugerido_e_o_mais_usado_na_competencia_mais_recente(admin_client_logado):
    a, b, c = criar_ministros(3)
    for ministro, valor in ((a, "35.00"), (b, "35.00"), (c, "60.00")):
        servico.create_fee(minister=ministro, reference_month="2026-10", due_date=VENCIMENTO, amount=valor)
    servico.create_fee(minister=a, reference_month="2026-09", due_date=VENCIMENTO, amount="99.00")
    form_ = admin_client_logado.get(URL_GERAR).context["form"]
    assert form_.initial["amount"] == Decimal("35.00")


# ---------------------------------------------------------------------------
# Analise / preview
# ---------------------------------------------------------------------------


def test_analise_classifica_cada_ministro():
    ativo_sem, ativo_pago, ativo_cancelado, ativo_isento = criar_ministros(4)
    (inativo,) = criar_ministros(1, inicio=2000, ativo=False)
    pago = servico.create_fee(minister=ativo_pago, reference_month=COMPETENCIA, due_date=VENCIMENTO, amount="10")
    servico.register_payment(pago, paid_at=HOJE, payment_method="PIX")
    servico.cancel_fee(
        servico.create_fee(minister=ativo_cancelado, reference_month=COMPETENCIA, due_date=VENCIMENTO, amount="10"),
        reason="x",
    )
    servico.exempt_fee(
        servico.create_fee(minister=ativo_isento, reference_month=COMPETENCIA, due_date=VENCIMENTO, amount="10"),
        reason="x",
    )

    analise = geracao.analisar(parametros())
    situacao = {linha["id"]: linha["situacao"] for linha in analise.linhas}

    assert situacao[ativo_sem.pk] == geracao.Situacao.CRIAR
    assert situacao[ativo_pago.pk] == geracao.Situacao.EXISTE
    assert situacao[ativo_cancelado.pk] == geracao.Situacao.EXISTE
    assert situacao[ativo_isento.pk] == geracao.Situacao.EXISTE
    assert situacao[inativo.pk] == geracao.Situacao.INATIVO
    assert (analise.ativos, analise.novas, analise.existentes, analise.inativos) == (4, 1, 3, 1)
    assert analise.total == Decimal("50.00")


def test_preview_mostra_numeros_reais_e_nao_grava(admin_client_logado):
    criar_ministros(3)
    criar_ministros(2, inicio=2000, ativo=False)
    antes = contagens()

    resposta = preparar_preview(admin_client_logado)
    html = resposta.content.decode()
    analise = resposta.context["analise"]

    assert (analise.ativos, analise.novas, analise.existentes, analise.inativos) == (3, 3, 0, 2)
    assert "R$ 150,00" in html
    assert "Novembro/2026" in html
    assert "Você está prestes a gerar <strong>3 mensalidades</strong>" in html
    assert ">Confirmar geração</button>" in html
    assert "Será criada" in html and "Ministro inativo" in html
    assert contagens() == antes


def test_preview_sem_nada_a_gerar_nao_oferece_confirmacao(admin_client_logado):
    (ministro,) = criar_ministros(1)
    servico.create_fee(minister=ministro, reference_month=COMPETENCIA, due_date=VENCIMENTO, amount="10")
    html = preparar_preview(admin_client_logado).content.decode()
    assert "Não há mensalidade nova a gerar" in html
    assert ">Confirmar geração</button>" not in html
    assert 'name="confirmar"' not in html
    assert "Já existe" in html


def test_get_no_preview_e_no_formulario_nunca_grava(admin_client_logado):
    criar_ministros(2)
    preparar_preview(admin_client_logado)
    for _ in range(3):
        admin_client_logado.get(URL_PREVIEW)
        admin_client_logado.get(URL_GERAR)
    assert MinisterMonthlyFee.objects.count() == 0


def test_preview_sem_sessao_volta_ao_formulario(admin_client_logado):
    resposta = admin_client_logado.get(URL_PREVIEW)
    assert resposta.status_code == 302 and resposta.url == URL_GERAR


# ---------------------------------------------------------------------------
# Confirmacao
# ---------------------------------------------------------------------------


def _confirmar(client, token=None, confirmar="1"):
    if token is None:
        token = client.session["geracao_mensalidades"]["token"]
    return client.post(URL_CONFIRMAR, {"token": token, "confirmar": confirmar})


def test_geracao_normal_pela_tela(admin_client_logado, admin_user):
    ministros = criar_ministros(3)
    criar_ministros(1, inicio=2000, ativo=False)
    preparar_preview(admin_client_logado)

    resposta = _confirmar(admin_client_logado)
    lote = MinisterFeeBatch.objects.get()
    assert resposta.status_code == 302
    assert resposta.url == "/admin-panel/ministros/mensalidades/gerar/resultado/{}/".format(lote.pk)

    mensalidades = MinisterMonthlyFee.objects.all()
    assert {m.minister_id for m in mensalidades} == {m.pk for m in ministros}
    for m in mensalidades:
        assert (m.reference_month, m.due_date, m.amount, m.status) == (
            COMPETENCIA,
            VENCIMENTO,
            Decimal("50.00"),
            MonthlyFeeStatus.PENDING,
        )
    assert (lote.created_count, lote.existing_count, lote.inactive_count) == (3, 0, 1)
    assert lote.total_amount == Decimal("150.00")
    assert lote.created_by == admin_user
    assert "geracao_mensalidades" not in admin_client_logado.session


def test_pagina_de_resultado(admin_client_logado, admin_user):
    criar_ministros(2)
    preparar_preview(admin_client_logado)
    resultado = admin_client_logado.get(_confirmar(admin_client_logado).url)
    html = resultado.content.decode()
    assert resultado.status_code == 200
    assert "2 mensalidades criadas" in html
    assert "R$ 100,00" in html
    assert admin_user.full_name in html
    assert "Ver mensalidades da competência" in html
    assert "?competencia=2026-11" in html
    assert "Gerar outra competência" in html
    # Recarregar o resultado nao gera nada.
    admin_client_logado.get(resultado.wsgi_request.path)
    assert MinisterMonthlyFee.objects.count() == 2


def test_varios_ministros():
    criar_ministros(40)
    lote = gerar()
    assert lote.created_count == 40
    assert MinisterMonthlyFee.objects.count() == 40
    assert lote.total_amount == Decimal("2000.00")


def test_nenhum_ministro_ativo():
    criar_ministros(2, ativo=False)
    lote = gerar()
    assert (lote.created_count, lote.inactive_count) == (0, 2)
    assert not MinisterMonthlyFee.objects.exists()


def test_todos_ja_possuem():
    for ministro in criar_ministros(3):
        servico.create_fee(minister=ministro, reference_month=COMPETENCIA, due_date=VENCIMENTO, amount="10")
    lote = gerar()
    assert (lote.created_count, lote.existing_count) == (0, 3)
    assert lote.total_amount == Decimal("0.00")


def test_alguns_ja_possuem_e_as_existentes_nao_mudam():
    a, b, c = criar_ministros(3)
    paga = servico.create_fee(minister=a, reference_month=COMPETENCIA, due_date=HOJE, amount="10.00")
    servico.register_payment(paga, paid_at=HOJE, payment_method="PIX")
    cancelada = servico.create_fee(minister=b, reference_month=COMPETENCIA, due_date=HOJE, amount="20.00")
    servico.cancel_fee(cancelada, reason="x")

    lote = gerar()
    assert (lote.created_count, lote.existing_count) == (1, 2)
    paga.refresh_from_db()
    cancelada.refresh_from_db()
    assert (paga.amount, paga.due_date, paga.status) == (Decimal("10.00"), HOJE, MonthlyFeeStatus.PAID)
    assert (cancelada.amount, cancelada.status) == (Decimal("20.00"), MonthlyFeeStatus.CANCELLED)
    assert MinisterMonthlyFee.objects.get(minister=c).amount == Decimal("50.00")


def test_inativos_nunca_recebem():
    ativos = criar_ministros(2)
    inativos = criar_ministros(2, inicio=2000, ativo=False)
    gerar()
    assert set(MinisterMonthlyFee.objects.values_list("minister_id", flat=True)) == {m.pk for m in ativos}
    assert not MinisterMonthlyFee.objects.filter(minister__in=inativos).exists()


def test_calculo_financeiro_reflete_no_dashboard(admin_client_logado):
    criar_ministros(4)
    gerar(valor="37.50")
    t = admin_client_logado.get(
        "/admin-panel/ministros/mensalidades/?competencia=2026-11"
    ).context["totais"]
    assert t["previsto"] == Decimal("150.00")
    assert t["qtd_total"] == 4
    assert t["recebido"] == Decimal("0.00")


def test_confirmacao_exige_confirmacao_explicita(admin_client_logado):
    criar_ministros(2)
    preparar_preview(admin_client_logado)
    _confirmar(admin_client_logado, confirmar="")
    assert MinisterMonthlyFee.objects.count() == 0


def test_token_errado_nao_gera(admin_client_logado):
    criar_ministros(2)
    preparar_preview(admin_client_logado)
    _confirmar(admin_client_logado, token="forjado")
    assert MinisterMonthlyFee.objects.count() == 0


def test_confirmacao_ignora_parametros_do_navegador(admin_client_logado):
    (ministro,) = criar_ministros(1)
    (outro,) = criar_ministros(1, inicio=3000)
    preparar_preview(admin_client_logado)
    token = admin_client_logado.session["geracao_mensalidades"]["token"]
    admin_client_logado.post(
        URL_CONFIRMAR,
        {
            "token": token,
            "confirmar": "1",
            "amount": "1.00",
            "reference_month": "2030-01",
            "ministros": str(ministro.pk),
        },
    )
    assert {(m.amount, m.reference_month) for m in MinisterMonthlyFee.objects.all()} == {
        (Decimal("50.00"), COMPETENCIA)
    }
    assert MinisterMonthlyFee.objects.count() == 2


def test_confirmacao_recalcula_no_servidor(admin_client_logado):
    """Mudancas entre o preview e a confirmacao valem; o preview nao decide."""
    a, b = criar_ministros(2)
    preparar_preview(admin_client_logado)
    servico.create_fee(minister=a, reference_month=COMPETENCIA, due_date=HOJE, amount="10")
    services.deactivate_minister(b)
    (novo,) = criar_ministros(1, inicio=3000)

    _confirmar(admin_client_logado)
    lote = MinisterFeeBatch.objects.get()
    assert (lote.created_count, lote.existing_count, lote.inactive_count) == (1, 1, 1)
    assert MinisterMonthlyFee.objects.filter(minister=novo).exists()
    assert not MinisterMonthlyFee.objects.filter(minister=b).exists()


def test_confirmacao_exige_post(admin_client_logado):
    assert admin_client_logado.get(URL_CONFIRMAR).status_code == 405
    assert admin_client_logado.get(URL_CANCELAR).status_code == 405


def test_cancelar_descarta_sem_gravar(admin_client_logado):
    criar_ministros(2)
    preparar_preview(admin_client_logado)
    admin_client_logado.post(URL_CANCELAR)
    assert "geracao_mensalidades" not in admin_client_logado.session
    assert MinisterMonthlyFee.objects.count() == 0


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------


def test_reenvio_do_mesmo_token_devolve_o_mesmo_lote(admin_client_logado):
    criar_ministros(3)
    preparar_preview(admin_client_logado)
    token = admin_client_logado.session["geracao_mensalidades"]["token"]

    primeira = _confirmar(admin_client_logado, token=token)
    segunda = _confirmar(admin_client_logado, token=token)  # F5 / clique duplo
    terceira = _confirmar(admin_client_logado, token=token)

    assert primeira.url == segunda.url == terceira.url
    assert MinisterFeeBatch.objects.count() == 1
    assert MinisterMonthlyFee.objects.count() == 3
    assert AuditLog.objects.filter(event=AuditEvent.MINISTER_FEE_BATCH_CREATED).count() == 1


def test_mesmo_token_no_servico():
    criar_ministros(2)
    token = geracao.novo_token()
    a = gerar(token=token)
    b = gerar(token=token)
    assert a.pk == b.pk
    assert MinisterMonthlyFee.objects.count() == 2


def test_mesma_geracao_duas_vezes():
    criar_ministros(5)
    primeira = gerar()
    segunda = gerar()
    assert (primeira.created_count, segunda.created_count) == (5, 0)
    assert segunda.existing_count == 5
    assert MinisterMonthlyFee.objects.count() == 5


def test_ministro_cadastrado_depois_recebe_so_ele():
    criar_ministros(4)
    assert gerar().created_count == 4
    (novo,) = criar_ministros(1, inicio=5000)

    segunda = gerar()
    assert (segunda.created_count, segunda.existing_count) == (1, 4)
    assert MinisterMonthlyFee.objects.count() == 5
    assert MinisterMonthlyFee.objects.filter(minister=novo).count() == 1


# ---------------------------------------------------------------------------
# Concorrencia
# ---------------------------------------------------------------------------


def test_conflito_que_escapa_das_travas_desfaz_o_lote_inteiro(monkeypatch):
    """
    Se a analise disser que um ministro nao tem mensalidade quando ele ja tem
    (o que so aconteceria com uma gravacao por fora das travas), a
    constraint de unicidade barra e NADA do lote fica gravado.
    """
    a, b, c = criar_ministros(3)
    servico.create_fee(minister=b, reference_month=COMPETENCIA, due_date=HOJE, amount="10")

    original = geracao.analisar

    def analise_cega(params, **kwargs):
        resultado = original(params, **kwargs)
        for linha in resultado.linhas:
            if linha["situacao"] == geracao.Situacao.EXISTE:
                linha["situacao"] = geracao.Situacao.CRIAR
        return resultado

    monkeypatch.setattr(geracao, "analisar", analise_cega)
    with pytest.raises(DomainError):
        gerar()

    assert MinisterMonthlyFee.objects.count() == 1
    assert MinisterFeeBatch.objects.count() == 0
    assert not AuditLog.objects.filter(event=AuditEvent.MINISTER_FEE_BATCH_CREATED).exists()


def _em_paralelo(*funcoes):
    """Roda as funcoes ao mesmo tempo, cada uma na propria conexao."""
    barreira = threading.Barrier(len(funcoes))
    resultados, erros = [None] * len(funcoes), []

    def rodar(indice, funcao):
        try:
            barreira.wait()
            resultados[indice] = funcao()
        except Exception as exc:  # pragma: no cover - aparece no assert
            erros.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=rodar, args=(i, f)) for i, f in enumerate(funcoes)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return resultados, erros


@pytest.mark.django_db(transaction=True)
def test_duas_geracoes_simultaneas_da_mesma_competencia():
    criar_ministros(20)
    resultados, erros = _em_paralelo(lambda: gerar(), lambda: gerar())

    assert erros == []
    assert MinisterMonthlyFee.objects.count() == 20
    # A segunda esperou a primeira e recalculou: zero novas, nenhuma duplicada.
    assert sorted(lote.created_count for lote in resultados) == [0, 20]
    assert MinisterFeeBatch.objects.count() == 2
    assert MinisterMonthlyFee.objects.values("minister_id").distinct().count() == 20
    assert sum(lote.total_amount for lote in resultados) == Decimal("1000.00")


@pytest.mark.django_db(transaction=True)
def test_clique_duplo_simultaneo_com_o_mesmo_token():
    criar_ministros(10)
    token = geracao.novo_token()
    resultados, erros = _em_paralelo(lambda: gerar(token=token), lambda: gerar(token=token))

    assert erros == []
    assert resultados[0].pk == resultados[1].pk
    assert MinisterFeeBatch.objects.count() == 1
    assert MinisterMonthlyFee.objects.count() == 10
    assert AuditLog.objects.filter(event=AuditEvent.MINISTER_FEE_CREATED).count() == 10


# ---------------------------------------------------------------------------
# Permissoes
# ---------------------------------------------------------------------------


def _rotas_get():
    return [URL_GERAR, URL_PREVIEW, "/admin-panel/ministros/mensalidades/gerar/resultado/1/"]


def test_aluno_recebe_403(student_client_logado):
    criar_ministros(2)
    for rota in _rotas_get():
        assert student_client_logado.get(rota).status_code == 403, rota
    assert student_client_logado.post(URL_GERAR, form()).status_code == 403
    assert student_client_logado.post(URL_CONFIRMAR, {"token": "x", "confirmar": "1"}).status_code == 403
    assert MinisterMonthlyFee.objects.count() == 0


def test_ministro_do_portal_nao_acessa(ministro_client_logado):
    for rota in _rotas_get():
        resposta = ministro_client_logado.get(rota)
        assert resposta.status_code == 302 and resposta.url.startswith("/login/"), rota
    resposta = ministro_client_logado.post(URL_CONFIRMAR, {"token": "x", "confirmar": "1"})
    assert resposta.status_code == 302 and resposta.url.startswith("/login/")
    assert MinisterMonthlyFee.objects.count() == 0


def test_anonimo_nao_executa(client):
    criar_ministros(2)
    for rota in _rotas_get():
        assert client.get(rota).url.startswith("/login/")
    resposta = client.post(URL_CONFIRMAR, {"token": "x", "confirmar": "1"})
    assert resposta.status_code == 302 and resposta.url.startswith("/login/")
    assert MinisterMonthlyFee.objects.count() == 0


def test_resultado_inexistente(admin_client_logado):
    assert admin_client_logado.get("/admin-panel/ministros/mensalidades/gerar/resultado/9999/").status_code == 404


def test_dashboard_e_lista_oferecem_a_geracao(admin_client_logado):
    for rota in ("/admin-panel/ministros/mensalidades/", "/admin-panel/ministros/mensalidades/lista/"):
        assert 'href="/admin-panel/ministros/mensalidades/gerar/"' in admin_client_logado.get(rota).content.decode()


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------


def test_auditoria_do_lote_e_das_mensalidades(admin_user):
    criar_ministros(3)
    criar_ministros(1, inicio=2000, ativo=False)
    lote = gerar(valor="45.00", actor=admin_user)

    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_FEE_BATCH_CREATED)
    assert evento.actor == admin_user
    assert (evento.entity_type, evento.entity_id) == ("MinisterFeeBatch", str(lote.pk))
    assert evento.metadata == {
        "competencia": "2026-11",
        "vencimento": "2026-11-10",
        "valor": "45.00",
        "criadas": 3,
        "existentes": 0,
        "inativos_ignorados": 1,
        "total_criado": "135.00",
    }

    individuais = AuditLog.objects.filter(event=AuditEvent.MINISTER_FEE_CREATED)
    assert individuais.count() == 3
    ids = set(MinisterMonthlyFee.objects.values_list("pk", flat=True))
    for registro in individuais:
        assert registro.actor == admin_user
        assert int(registro.entity_id) in ids
        assert registro.metadata["lote"] == lote.pk
        assert registro.metadata["competencia"] == "2026-11"
        assert registro.metadata["depois"] == {"amount": "45.00", "due_date": "2026-11-10"}


def test_existentes_nao_ganham_evento_novo():
    (ministro,) = criar_ministros(1)
    servico.create_fee(minister=ministro, reference_month=COMPETENCIA, due_date=HOJE, amount="10")
    antes = AuditLog.objects.filter(event=AuditEvent.MINISTER_FEE_CREATED).count()
    gerar()
    assert AuditLog.objects.filter(event=AuditEvent.MINISTER_FEE_CREATED).count() == antes


def test_lote_nao_e_excluido():
    criar_ministros(1)
    lote = gerar()
    with pytest.raises(ValueError):
        lote.delete()


# ---------------------------------------------------------------------------
# Desempenho
# ---------------------------------------------------------------------------


def test_analise_tem_numero_fixo_de_consultas():
    criar_ministros(5)
    with CaptureQueriesContext(connection) as poucos:
        geracao.analisar(parametros())
    criar_ministros(60, inicio=3000)
    with CaptureQueriesContext(connection) as muitos:
        analise = geracao.analisar(parametros())
    assert analise.novas == 65
    assert len(poucos) == len(muitos) <= 3


def test_geracao_tem_custo_fixo_mais_um_evento_por_mensalidade():
    criar_ministros(50)
    with CaptureQueriesContext(connection) as consultas:
        lote = gerar()
    assert lote.created_count == 50
    # Uma insercao de auditoria por mensalidade criada; o resto e fixo.
    assert len(consultas) <= 50 + 15
