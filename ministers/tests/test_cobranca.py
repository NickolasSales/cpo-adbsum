"""Cobranca Pix das mensalidades (Fase 2D): configuracao, snapshot e geracao."""

import datetime
import re
import threading
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from audit.models import AuditEvent, AuditLog
from common.exceptions import DomainError
from ministers import cobranca, geracao, pix, services
from ministers import mensalidades as servico
from ministers.models import (
    MinisterBillingSettings,
    MinisterFeeBatch,
    MinisterFeeCharge,
    MinisterialTitle,
    MinisterMonthlyFee,
    MonthlyFeeStatus,
)
from ministers.tests.conftest import PIX_DE_TESTE, dados_de_ministro

pytestmark = pytest.mark.django_db

HOJE = timezone.localdate()
DAQUI_A_10 = HOJE + datetime.timedelta(days=10)
ONTEM = HOJE - datetime.timedelta(days=1)


def nova(ministro, competencia="2026-10", valor="35.00", vencimento=DAQUI_A_10):
    return servico.create_fee(
        minister=ministro, reference_month=competencia, due_date=vencimento, amount=valor
    )


def configurar(**extra):
    dados = dict(PIX_DE_TESTE)
    dados.update(extra)
    return cobranca.salvar_configuracao(dados)[0]


# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------


def test_configuracao_normaliza_para_o_br_code():
    config = configurar()
    assert config.pk == 1
    assert config.pix_key == "pix.teste@example.com"
    assert config.pix_receiver_name == "AD BRAS SUMARE TESTE"
    assert config.pix_receiver_city == "SUMARE"
    assert config.is_active


@pytest.mark.parametrize(
    "extra, trecho",
    [
        ({"pix_key": ""}, "Informe a chave Pix."),
        ({"pix_key_type": ""}, "Escolha o tipo da chave Pix."),
        ({"pix_key": "nao-e-email"}, "E-mail inválido"),
        ({"pix_key_type": "CPF", "pix_key": "111.111.111-11"}, "CPF inválido."),
        ({"pix_receiver_name": ""}, "Informe o favorecido."),
        ({"pix_receiver_name": "Igreja Evangélica Assembleia de Deus"}, "no máximo 25"),
        ({"pix_receiver_city": ""}, "Informe a cidade."),
        ({"pix_receiver_city": "Santa Bárbara d'Oeste"}, "no máximo 15"),
        ({"payment_instructions": "x" * 301}, "no máximo 300"),
    ],
)
def test_configuracao_invalida_nao_e_gravada(extra, trecho):
    with pytest.raises(DomainError) as erro:
        configurar(**extra)
    assert any(trecho in mensagem for mensagem in erro.value.mensagens)
    assert not MinisterBillingSettings.objects.exists()


def test_configuracao_mostra_todos_os_erros_de_uma_vez():
    with pytest.raises(DomainError) as erro:
        configurar(pix_key="", pix_receiver_name="", pix_receiver_city="")
    assert len(erro.value.mensagens) == 3


def test_configuracao_e_linha_unica():
    configurar()
    configurar(pix_receiver_city="Campinas")
    assert MinisterBillingSettings.objects.count() == 1
    with pytest.raises(IntegrityError), transaction.atomic():
        MinisterBillingSettings.objects.create(
            pk=2, pix_key_type="EMAIL", pix_key="a@example.com", pix_receiver_name="X", pix_receiver_city="Y"
        )


def test_configuracao_auditada_com_antes_e_depois(admin_user):
    cobranca.salvar_configuracao(dict(PIX_DE_TESTE), actor=admin_user)
    cobranca.salvar_configuracao(dict(PIX_DE_TESTE, pix_receiver_city="Campinas"), actor=admin_user)
    eventos = AuditLog.objects.filter(event=AuditEvent.MINISTER_BILLING_SETTINGS_UPDATED).order_by("id")
    assert eventos.count() == 2
    assert eventos[0].actor == admin_user
    assert eventos[1].metadata["changed_fields"] == ["pix_receiver_city"]
    assert eventos[1].metadata["antes"] == {"pix_receiver_city": "SUMARE"}
    assert eventos[1].metadata["depois"] == {"pix_receiver_city": "CAMPINAS"}
    assert MinisterBillingSettings.objects.get().updated_by == admin_user


def test_salvar_sem_mudanca_nao_gera_evento():
    configurar()
    _, alterou = cobranca.salvar_configuracao(dict(PIX_DE_TESTE))
    assert not alterou
    assert AuditLog.objects.filter(event=AuditEvent.MINISTER_BILLING_SETTINGS_UPDATED).count() == 1


def test_configuracao_desativada_nao_esta_pronta():
    configurar(is_active=False)
    assert cobranca.configuracao_pronta() is None
    assert cobranca.motivo_sem_configuracao() == cobranca.COBRANCA_DESATIVADA


def test_sem_configuracao_nao_esta_pronta():
    assert cobranca.configuracao_pronta() is None
    assert cobranca.motivo_sem_configuracao() == "Configure os dados de pagamento antes de gerar as cobranças."


def test_linha_gravada_por_fora_com_dado_invalido_nao_gera_cobranca(ministro):
    MinisterBillingSettings.objects.create(
        pk=1, pix_key_type="EMAIL", pix_key="invalido", pix_receiver_name="X", pix_receiver_city="Y"
    )
    assert cobranca.configuracao_pronta() is None
    with pytest.raises(DomainError):
        cobranca.gerar_cobranca(nova(ministro))
    assert not MinisterFeeCharge.objects.exists()


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def test_snapshot_copia_tudo_e_monta_o_pix(ministro, configuracao_pix, admin_user):
    mensalidade = nova(ministro, valor="35.00")
    charge, criada = cobranca.gerar_cobranca(mensalidade, actor=admin_user)
    assert criada
    assert charge.fee == mensalidade
    assert charge.amount_snapshot == Decimal("35.00")
    assert charge.due_date_snapshot == DAQUI_A_10
    assert charge.reference_month_snapshot == datetime.date(2026, 10, 1)
    assert charge.minister_name_snapshot == "Rodrigo Exemplo Xavier"
    assert charge.conamad_registration_snapshot == "17899"
    assert charge.field_registration_snapshot == "7764"
    assert charge.congregation_snapshot == "Sede"
    assert charge.position_snapshot == "Pastor Presidente"
    assert charge.pix_key_snapshot == "pix.teste@example.com"
    assert charge.pix_receiver_name_snapshot == "AD BRAS SUMARE TESTE"
    assert charge.pix_receiver_city_snapshot == "SUMARE"
    assert charge.payment_instructions_snapshot == "Envie o comprovante para a secretaria."
    assert charge.generated_by == admin_user

    campos = pix.ler(charge.pix_payload)
    assert campos["26"]["01"] == "pix.teste@example.com"
    assert campos["54"] == "35.00"
    assert campos["59"] == "AD BRAS SUMARE TESTE"
    assert campos["60"] == "SUMARE"
    assert campos["62"]["05"] == charge.txid


def test_valor_do_pix_vem_da_mensalidade(ministro, configuracao_pix):
    charge, _ = cobranca.gerar_cobranca(nova(ministro, valor="47.30"))
    assert pix.ler(charge.pix_payload)["54"] == "47.30"
    assert isinstance(charge.amount_snapshot, Decimal)


def test_cargo_vazio_usa_o_titulo(configuracao_pix):
    ministro = services.create_minister(
        dados=dados_de_ministro(position="", ministerial_title=MinisterialTitle.EVANGELISTA)
    )
    charge, _ = cobranca.gerar_cobranca(nova(ministro))
    assert charge.position_snapshot == "Evangelista"


def test_txid_estavel_sem_dado_pessoal(ministro, configuracao_pix):
    charge, _ = cobranca.gerar_cobranca(nova(ministro))
    assert re.fullmatch(r"CPO202610[A-Z0-9]{10}", charge.txid)
    for parte in ("RODRIGO", "EXEMPLO", "XAVIER"):
        assert parte not in charge.txid
    assert MinisterFeeCharge.objects.get().txid == charge.txid


def test_txids_sao_diferentes_entre_cobrancas(configuracao_pix):
    ministros = [
        services.create_minister(dados=dados_de_ministro(conamad_registration=str(9000 + i), field_registration=""))
        for i in range(5)
    ]
    txids = {cobranca.gerar_cobranca(nova(m))[0].txid for m in ministros}
    assert len(txids) == 5


def test_snapshot_e_imutavel(ministro, configuracao_pix):
    charge, _ = cobranca.gerar_cobranca(nova(ministro))
    charge.amount_snapshot = Decimal("1.00")
    with pytest.raises(ValueError):
        charge.save()
    with pytest.raises(ValueError):
        charge.delete()
    assert MinisterFeeCharge.objects.get().amount_snapshot == Decimal("35.00")


# ---------------------------------------------------------------------------
# Idempotencia e concorrencia
# ---------------------------------------------------------------------------


def test_gerar_duas_vezes_devolve_a_mesma(ministro, configuracao_pix):
    mensalidade = nova(ministro)
    primeira, criada1 = cobranca.gerar_cobranca(mensalidade)
    segunda, criada2 = cobranca.gerar_cobranca(mensalidade)
    assert (criada1, criada2) == (True, False)
    assert primeira.pk == segunda.pk and primeira.txid == segunda.txid
    assert primeira.pix_payload == segunda.pix_payload
    assert MinisterFeeCharge.objects.count() == 1
    assert AuditLog.objects.filter(event=AuditEvent.MINISTER_BILLING_GENERATED).count() == 1


def test_banco_recusa_duas_vigentes_para_a_mesma_mensalidade(ministro, configuracao_pix):
    mensalidade = nova(ministro)
    charge, _ = cobranca.gerar_cobranca(mensalidade)
    copia = MinisterFeeCharge.objects.filter(pk=charge.pk).values().get()
    copia.pop("id")
    copia["txid"] = "CPO202610OUTROTXID1"
    with pytest.raises(IntegrityError), transaction.atomic():
        MinisterFeeCharge.objects.create(**copia)


def _em_paralelo(*funcoes):
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
def test_duas_geracoes_simultaneas_da_mesma_mensalidade():
    configurar()
    ministro = services.create_minister(dados=dados_de_ministro())
    mensalidade = nova(ministro)
    resultados, erros = _em_paralelo(
        lambda: cobranca.gerar_cobranca(mensalidade), lambda: cobranca.gerar_cobranca(mensalidade)
    )
    assert erros == []
    assert sorted(criada for _, criada in resultados) == [False, True]
    assert resultados[0][0].txid == resultados[1][0].txid
    assert MinisterFeeCharge.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_dois_lotes_simultaneos_nao_duplicam():
    configurar()
    for i in range(8):
        m = services.create_minister(dados=dados_de_ministro(conamad_registration=str(5000 + i), field_registration=""))
        nova(m)
    resultados, erros = _em_paralelo(cobranca.gerar_cobrancas_pendentes, cobranca.gerar_cobrancas_pendentes)
    assert erros == []
    assert sorted(resultados) == [0, 8]
    assert MinisterFeeCharge.objects.count() == 8
    assert MinisterFeeCharge.objects.values("fee").distinct().count() == 8


# ---------------------------------------------------------------------------
# Quem recebe cobranca
# ---------------------------------------------------------------------------


def test_sem_configuracao_nao_gera_e_explica(ministro):
    with pytest.raises(DomainError) as erro:
        cobranca.gerar_cobranca(nova(ministro))
    assert str(erro.value) == "Configure os dados de pagamento antes de gerar as cobranças."
    assert not MinisterFeeCharge.objects.exists()


def test_vencida_recebe_cobranca(ministro, configuracao_pix):
    charge, criada = cobranca.gerar_cobranca(nova(ministro, vencimento=ONTEM))
    assert criada


@pytest.mark.parametrize("acao", ["pagar", "isentar", "cancelar"])
def test_paga_isenta_e_cancelada_nao_recebem(ministro, configuracao_pix, acao):
    mensalidade = nova(ministro)
    if acao == "pagar":
        servico.register_payment(mensalidade, paid_at=HOJE, payment_method="PIX")
    elif acao == "isentar":
        servico.exempt_fee(mensalidade, reason="motivo")
    else:
        servico.cancel_fee(mensalidade, reason="motivo")
    with pytest.raises(DomainError):
        cobranca.gerar_cobranca(mensalidade)
    assert not MinisterFeeCharge.objects.exists()


def test_gerar_nao_muda_a_situacao_da_mensalidade(ministro, configuracao_pix):
    mensalidade = nova(ministro)
    antes = MinisterMonthlyFee.objects.filter(pk=mensalidade.pk).values().get()
    cobranca.gerar_cobranca(mensalidade)
    depois = MinisterMonthlyFee.objects.filter(pk=mensalidade.pk).values().get()
    assert antes == depois
    assert depois["status"] == MonthlyFeeStatus.PENDING


def test_auditoria_da_geracao_individual(ministro, configuracao_pix, admin_user):
    mensalidade = nova(ministro)
    charge, _ = cobranca.gerar_cobranca(mensalidade, actor=admin_user)
    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_BILLING_GENERATED)
    assert evento.actor == admin_user
    assert (evento.entity_type, evento.entity_id) == ("MinisterMonthlyFee", str(mensalidade.pk))
    assert evento.metadata["txid"] == charge.txid
    assert evento.metadata["valor"] == "35.00"
    assert "pix_payload" not in evento.metadata


# ---------------------------------------------------------------------------
# Historico imutavel
# ---------------------------------------------------------------------------


def test_configuracao_nova_nao_altera_cobranca_antiga(ministro, configuracao_pix):
    outubro, _ = cobranca.gerar_cobranca(nova(ministro, "2026-10"))
    payload_de_outubro = outubro.pix_payload

    configurar(
        pix_key_type="TELEFONE",
        pix_key="(19) 99999-8888",
        pix_receiver_name="Outro Favorecido",
        pix_receiver_city="Campinas",
    )
    outubro_de_novo, criada = cobranca.gerar_cobranca(MinisterMonthlyFee.objects.get(reference_month="2026-10-01"))
    assert not criada
    assert outubro_de_novo.pix_payload == payload_de_outubro
    assert outubro_de_novo.pix_key_snapshot == "pix.teste@example.com"
    assert pix.ler(outubro_de_novo.pix_payload)["60"] == "SUMARE"

    novembro, _ = cobranca.gerar_cobranca(nova(ministro, "2026-11"))
    assert novembro.pix_key_snapshot == "+5519999998888"
    assert pix.ler(novembro.pix_payload)["59"] == "OUTRO FAVORECIDO"
    assert pix.ler(novembro.pix_payload)["60"] == "CAMPINAS"


def test_cadastro_alterado_nao_muda_a_cobranca(ministro, configuracao_pix, admin_user):
    charge, _ = cobranca.gerar_cobranca(nova(ministro))
    services.update_minister(
        ministro,
        dados=dados_de_ministro(
            full_name="Rodrigo Nome Novo", position="Diácono", congregation="Jardim Novo", field_registration="9999"
        ),
        actor=admin_user,
    )
    charge = MinisterFeeCharge.objects.get(pk=charge.pk)
    assert charge.minister_name_snapshot == "Rodrigo Exemplo Xavier"
    assert charge.position_snapshot == "Pastor Presidente"
    assert charge.congregation_snapshot == "Sede"
    assert charge.field_registration_snapshot == "7764"


def test_alterar_valor_substitui_a_cobranca(ministro, configuracao_pix, admin_user):
    mensalidade = nova(ministro, valor="35.00")
    antiga, _ = cobranca.gerar_cobranca(mensalidade)
    servico.update_fee(mensalidade, due_date=DAQUI_A_10, amount="40.00", notes="", actor=admin_user)

    antiga = MinisterFeeCharge.objects.get(pk=antiga.pk)
    assert antiga.superseded_at is not None
    assert antiga.amount_snapshot == Decimal("35.00")
    nova_cobranca = cobranca.cobranca_vigente(mensalidade)
    assert nova_cobranca.amount_snapshot == Decimal("40.00")
    assert nova_cobranca.txid != antiga.txid
    assert pix.ler(nova_cobranca.pix_payload)["54"] == "40.00"

    evento = AuditLog.objects.filter(event=AuditEvent.MINISTER_FEE_UPDATED).get()
    assert evento.metadata["cobranca_substituida"] == antiga.txid
    assert evento.metadata["cobranca_nova"] == nova_cobranca.txid


def test_alterar_so_observacao_mantem_a_cobranca(ministro, configuracao_pix):
    mensalidade = nova(ministro)
    charge, _ = cobranca.gerar_cobranca(mensalidade)
    servico.update_fee(mensalidade, due_date=DAQUI_A_10, amount="35.00", notes="nova nota")
    assert cobranca.cobranca_vigente(mensalidade).pk == charge.pk
    assert MinisterFeeCharge.objects.count() == 1


def test_alterar_valor_sem_configuracao_deixa_sem_cobranca(ministro, configuracao_pix):
    mensalidade = nova(ministro)
    cobranca.gerar_cobranca(mensalidade)
    configurar(is_active=False)
    servico.update_fee(mensalidade, due_date=DAQUI_A_10, amount="50.00", notes="")
    assert cobranca.cobranca_vigente(mensalidade) is None
    assert MinisterFeeCharge.objects.count() == 1


def test_estorno_e_reativacao_mantem_a_mesma_cobranca(ministro, configuracao_pix):
    mensalidade = nova(ministro)
    charge, _ = cobranca.gerar_cobranca(mensalidade)
    servico.register_payment(mensalidade, paid_at=HOJE, payment_method="PIX")
    servico.reverse_payment(mensalidade, reason="estorno de teste")
    assert cobranca.cobranca_vigente(mensalidade).pk == charge.pk


# ---------------------------------------------------------------------------
# Geracao em lote e integracao com a 2B
# ---------------------------------------------------------------------------


def _ministros(n, inicio=6000):
    return [
        services.create_minister(dados=dados_de_ministro(conamad_registration=str(inicio + i), field_registration=""))
        for i in range(n)
    ]


def test_lote_gera_so_para_pendentes_sem_cobranca(configuracao_pix, admin_user):
    a, b, c, d, e = _ministros(5)
    com_cobranca = nova(a)
    cobranca.gerar_cobranca(com_cobranca)
    nova(b)
    nova(c, vencimento=ONTEM)
    paga = nova(d)
    servico.register_payment(paga, paid_at=HOJE, payment_method="PIX")
    isenta = nova(e)
    servico.exempt_fee(isenta, reason="motivo")

    resumo = cobranca.resumo()
    assert resumo["total"] == 5
    assert resumo["com_cobranca"] == 1
    assert resumo["elegiveis"] == 2
    assert (resumo["pendentes"], resumo["vencidas"], resumo["pagas"], resumo["isentas"]) == (2, 1, 1, 1)
    assert resumo["pagas_sem_cobranca"] == 1

    assert cobranca.gerar_cobrancas_pendentes(actor=admin_user) == 2
    assert MinisterFeeCharge.objects.count() == 3
    assert cobranca.gerar_cobrancas_pendentes(actor=admin_user) == 0
    assert MinisterFeeCharge.objects.count() == 3

    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_BILLING_BATCH_GENERATED)
    assert evento.metadata == {"criadas": 2, "por_competencia": {"2026-10": 2}, "valor_total": "70.00"}
    assert AuditLog.objects.filter(event=AuditEvent.MINISTER_BILLING_GENERATED).count() == 1


def test_lote_sem_configuracao_nao_grava_nada():
    (ministro,) = _ministros(1)
    nova(ministro)
    with pytest.raises(DomainError):
        cobranca.gerar_cobrancas_pendentes()
    assert not MinisterFeeCharge.objects.exists()


def test_lote_e_tudo_ou_nada(configuracao_pix, monkeypatch):
    for m in _ministros(3):
        nova(m)
    original = cobranca._snapshot
    chamadas = []

    def falha_na_terceira(*args, **kwargs):
        chamadas.append(1)
        if len(chamadas) == 3:
            raise pix.PayloadInvalido("falha simulada")
        return original(*args, **kwargs)

    monkeypatch.setattr(cobranca, "_snapshot", falha_na_terceira)
    with pytest.raises(pix.PayloadInvalido):
        cobranca.gerar_cobrancas_pendentes()
    assert not MinisterFeeCharge.objects.exists()
    assert not AuditLog.objects.filter(event=AuditEvent.MINISTER_BILLING_BATCH_GENERATED).exists()


def test_lote_numero_de_consultas_constante(configuracao_pix):
    for m in _ministros(3):
        nova(m)
    with CaptureQueriesContext(connection) as poucas:
        cobranca.gerar_cobrancas_pendentes()
    for m in _ministros(12, inicio=7000):
        nova(m)
    with CaptureQueriesContext(connection) as muitas:
        cobranca.gerar_cobrancas_pendentes()
    assert len(muitas.captured_queries) == len(poucas.captured_queries)


def _gerar_competencia(actor=None):
    parametros = geracao.validar_parametros("2026-11", DAQUI_A_10, "35.00")
    return geracao.gerar(parametros, token=geracao.novo_token(), actor=actor)


def test_geracao_em_massa_ja_cria_as_cobrancas(configuracao_pix, admin_user):
    _ministros(4)
    lote = _gerar_competencia(admin_user)
    assert lote.created_count == 4 and lote.charge_count == 4
    assert MinisterFeeCharge.objects.count() == 4
    for charge in MinisterFeeCharge.objects.select_related("fee"):
        assert charge.amount_snapshot == charge.fee.amount == Decimal("35.00")
        assert charge.generated_by == admin_user
    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_FEE_BATCH_CREATED)
    assert evento.metadata["cobrancas_geradas"] == 4


def test_geracao_em_massa_sem_configuracao_cria_so_as_mensalidades():
    _ministros(3)
    lote = _gerar_competencia()
    assert (lote.created_count, lote.charge_count) == (3, 0)
    assert not MinisterFeeCharge.objects.exists()


def test_geracao_em_massa_com_cobranca_nao_cresce_em_consultas(configuracao_pix):
    _ministros(2)
    with CaptureQueriesContext(connection) as poucas:
        _gerar_competencia()
    _ministros(10, inicio=8000)
    parametros = geracao.validar_parametros("2026-12", DAQUI_A_10, "35.00")
    with CaptureQueriesContext(connection) as muitas:
        geracao.gerar(parametros, token=geracao.novo_token())
    # +1 por mensalidade criada: o MINISTER_FEE_CREATED da 2B. As cobrancas
    # entram num INSERT so.
    assert len(muitas.captured_queries) - len(poucas.captured_queries) == 12 - 2
    assert MinisterFeeBatch.objects.count() == 2
