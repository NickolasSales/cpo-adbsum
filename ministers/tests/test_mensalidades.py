"""Mensalidades: modelo, restricoes, situacao, transicoes e auditoria."""

import datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEvent, AuditLog
from common.exceptions import DomainError
from ministers import mensalidades as servico
from ministers import services
from ministers.models import FeeSituation, MinisterMonthlyFee, MonthlyFeeStatus, PaymentMethod
from ministers.templatetags.financeiro import brl

pytestmark = pytest.mark.django_db

HOJE = timezone.localdate()
ONTEM = HOJE - datetime.timedelta(days=1)
AMANHA = HOJE + datetime.timedelta(days=1)
OUTUBRO = datetime.date(2026, 10, 1)


def nova(ministro, competencia=OUTUBRO, vencimento=None, valor="35.00", **extra):
    return servico.create_fee(
        minister=ministro,
        reference_month=competencia,
        due_date=vencimento or AMANHA,
        amount=valor,
        **extra,
    )


def pagar(mensalidade, **extra):
    dados = {"paid_at": HOJE, "payment_method": PaymentMethod.PIX, "payment_reference": "E123"}
    dados.update(extra)
    return servico.register_payment(mensalidade, **dados)


# ---------------------------------------------------------------------------
# Criacao e competencia
# ---------------------------------------------------------------------------


def test_criacao(ministro, admin_user):
    m = nova(ministro, actor=admin_user)
    m.refresh_from_db()

    assert m.status == MonthlyFeeStatus.PENDING
    assert m.amount == Decimal("35.00")
    assert isinstance(m.amount, Decimal)
    assert m.reference_month == OUTUBRO
    assert m.competencia_rotulo == "10/2026"
    assert m.competencia_chave == "2026-10"

    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_FEE_CREATED)
    assert evento.actor == admin_user
    assert evento.entity_type == "MinisterMonthlyFee"
    assert evento.entity_id == str(m.pk)
    assert evento.metadata["minister_id"] == ministro.pk
    assert evento.metadata["competencia"] == "2026-10"
    assert evento.metadata["depois"]["amount"] == "35.00"


def test_competencia_por_texto_vira_dia_1(ministro):
    m = nova(ministro, competencia="2026-11")
    assert m.reference_month == datetime.date(2026, 11, 1)


def test_competencia_com_dia_qualquer_e_normalizada(ministro):
    m = nova(ministro, competencia=datetime.date(2026, 12, 17))
    assert m.reference_month == datetime.date(2026, 12, 1)


@pytest.mark.parametrize("texto", ["Outubro", "Out/26", "10-2026", "2026/10", "2026-13", "1999-01", ""])
def test_competencia_em_formato_livre_e_recusada(texto):
    with pytest.raises(DomainError):
        servico.competencia_de_texto(texto)


def test_banco_recusa_competencia_fora_do_dia_1(ministro):
    with pytest.raises(IntegrityError), transaction.atomic():
        MinisterMonthlyFee.objects.filter(pk=nova(ministro).pk).update(
            reference_month=datetime.date(2026, 10, 15)
        )


@pytest.mark.parametrize("valor", ["0", "-1", "abc", "100000000"])
def test_valor_invalido(ministro, valor):
    with pytest.raises(DomainError):
        nova(ministro, valor=valor)
    assert not MinisterMonthlyFee.objects.exists()


def test_valor_e_arredondado_em_decimal(ministro):
    assert nova(ministro, valor=Decimal("35.004")).amount == Decimal("35.00")


def test_banco_recusa_valor_zero(ministro):
    with pytest.raises(IntegrityError), transaction.atomic():
        MinisterMonthlyFee.objects.create(
            minister=ministro, reference_month=OUTUBRO, due_date=HOJE, amount=Decimal("0")
        )


def test_valores_diferentes_por_ministro_e_competencia(ministro, outro_ministro):
    a = nova(ministro, valor="35.00")
    b = nova(outro_ministro, valor="50.00")
    c = nova(ministro, competencia="2026-11", valor="40.00")
    assert {a.amount, b.amount, c.amount} == {Decimal("35.00"), Decimal("50.00"), Decimal("40.00")}


# ---------------------------------------------------------------------------
# Unicidade
# ---------------------------------------------------------------------------


def test_uma_mensalidade_por_ministro_e_competencia(ministro):
    nova(ministro)
    with pytest.raises(DomainError):
        nova(ministro, valor="50.00")
    assert MinisterMonthlyFee.objects.count() == 1


def test_unicidade_no_banco(ministro):
    nova(ministro)
    with pytest.raises(IntegrityError), transaction.atomic():
        MinisterMonthlyFee.objects.create(
            minister=ministro, reference_month=OUTUBRO, due_date=HOJE, amount=Decimal("1")
        )


def test_cancelada_continua_ocupando_a_competencia(ministro):
    m = nova(ministro)
    servico.cancel_fee(m, reason="Lançada por engano")
    with pytest.raises(DomainError):
        nova(ministro)


def test_mesma_competencia_para_ministros_diferentes(ministro, outro_ministro):
    nova(ministro)
    nova(outro_ministro)
    assert MinisterMonthlyFee.objects.filter(reference_month=OUTUBRO).count() == 2


# ---------------------------------------------------------------------------
# Ministro inativo
# ---------------------------------------------------------------------------


def test_ministro_inativo_nao_recebe_sem_confirmacao(ministro):
    services.deactivate_minister(ministro)
    with pytest.raises(DomainError) as erro:
        nova(ministro)
    assert "inativo" in str(erro.value)
    assert not MinisterMonthlyFee.objects.exists()


def test_ministro_inativo_com_confirmacao_explicita(ministro):
    services.deactivate_minister(ministro)
    m = nova(ministro, permitir_inativo=True)
    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_FEE_CREATED)
    assert m.pk and evento.metadata["ministro_inativo"] is True


def test_inativar_ministro_preserva_o_historico(ministro):
    m = pagar(nova(ministro))
    services.deactivate_minister(ministro)
    m.refresh_from_db()
    assert m.status == MonthlyFeeStatus.PAID


def test_mensalidade_e_ministro_nao_sao_excluidos(ministro):
    m = nova(ministro)
    with pytest.raises(ValueError):
        m.delete()
    with pytest.raises(Exception):
        with transaction.atomic():
            type(ministro).objects.filter(pk=ministro.pk).delete()
    assert MinisterMonthlyFee.objects.filter(pk=m.pk).exists()


# ---------------------------------------------------------------------------
# Situacao e vencimento
# ---------------------------------------------------------------------------


def test_pendente_a_vencer(ministro):
    assert nova(ministro, vencimento=HOJE).situacao == FeeSituation.PENDING


def test_pendente_vencida(ministro):
    assert nova(ministro, vencimento=ONTEM).situacao == FeeSituation.OVERDUE


def test_paga_nunca_aparece_vencida(ministro):
    m = pagar(nova(ministro, vencimento=ONTEM - datetime.timedelta(days=60)))
    assert m.situacao == FeeSituation.PAID


def test_isenta_e_cancelada_nao_vencem(ministro, outro_ministro):
    a = servico.exempt_fee(nova(ministro, vencimento=ONTEM), reason="Isenção do campo")
    b = servico.cancel_fee(nova(outro_ministro, vencimento=ONTEM), reason="Duplicada")
    assert a.situacao == FeeSituation.EXEMPT
    assert b.situacao == FeeSituation.CANCELLED


def test_filtro_de_situacao(ministro, outro_ministro):
    vencida = nova(ministro, vencimento=ONTEM)
    a_vencer = nova(outro_ministro, vencimento=AMANHA)
    consulta = MinisterMonthlyFee.objects.all()
    assert list(consulta.filter(servico.filtro_de_situacao(FeeSituation.OVERDUE))) == [vencida]
    assert list(consulta.filter(servico.filtro_de_situacao(FeeSituation.PENDING))) == [a_vencer]


# ---------------------------------------------------------------------------
# Pagamento
# ---------------------------------------------------------------------------


def test_registrar_pagamento(ministro, admin_user):
    m = pagar(nova(ministro, vencimento=ONTEM), actor=admin_user)
    m.refresh_from_db()
    assert m.status == MonthlyFeeStatus.PAID
    assert m.paid_at == HOJE
    assert m.payment_method == PaymentMethod.PIX
    assert m.payment_reference == "E123"

    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_FEE_PAYMENT_REGISTERED)
    assert evento.actor == admin_user
    assert evento.metadata["estava_vencida"] is True
    assert evento.metadata["depois"]["paid_at"] == HOJE.isoformat()
    assert evento.metadata["depois"]["payment_method"] == "PIX"


def test_pagamento_no_futuro_e_recusado(ministro):
    with pytest.raises(DomainError):
        pagar(nova(ministro), paid_at=AMANHA)


def test_pagamento_sem_forma_e_recusado(ministro):
    with pytest.raises(DomainError):
        pagar(nova(ministro), payment_method="")


def test_pagamento_duplo_e_recusado(ministro):
    m = pagar(nova(ministro))
    with pytest.raises(DomainError):
        pagar(m)
    assert AuditLog.objects.filter(event=AuditEvent.MINISTER_FEE_PAYMENT_REGISTERED).count() == 1


def test_banco_recusa_pago_sem_data(ministro):
    m = nova(ministro)
    with pytest.raises(IntegrityError), transaction.atomic():
        MinisterMonthlyFee.objects.filter(pk=m.pk).update(status=MonthlyFeeStatus.PAID)


def test_banco_recusa_pendente_com_dados_de_pagamento(ministro):
    m = nova(ministro)
    with pytest.raises(IntegrityError), transaction.atomic():
        MinisterMonthlyFee.objects.filter(pk=m.pk).update(paid_at=HOJE, payment_method="PIX")


def test_corrigir_pagamento_audita_antes_e_depois(ministro, admin_user):
    m = pagar(nova(ministro), paid_at=ONTEM)
    servico.correct_payment(
        m,
        paid_at=HOJE,
        payment_method=PaymentMethod.CASH,
        payment_reference="E123",
        reason="Data digitada errada",
        actor=admin_user,
    )
    m.refresh_from_db()
    assert (m.paid_at, m.payment_method) == (HOJE, PaymentMethod.CASH)

    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_FEE_PAYMENT_UPDATED)
    assert evento.metadata["antes"] == {"paid_at": ONTEM.isoformat(), "payment_method": "PIX"}
    assert evento.metadata["depois"] == {"paid_at": HOJE.isoformat(), "payment_method": "CASH"}
    assert evento.metadata["motivo"] == "Data digitada errada"


def test_correcao_exige_motivo(ministro):
    m = pagar(nova(ministro))
    with pytest.raises(DomainError):
        servico.correct_payment(m, paid_at=ONTEM, payment_method="PIX", reason="  ")


def test_correcao_sem_mudanca_e_recusada(ministro):
    m = pagar(nova(ministro))
    with pytest.raises(DomainError):
        servico.correct_payment(
            m, paid_at=HOJE, payment_method="PIX", payment_reference="E123", reason="x"
        )


def test_estorno_volta_a_pendente_e_guarda_o_pagamento_na_trilha(ministro):
    m = pagar(nova(ministro))
    servico.reverse_payment(m, reason="Pix devolvido pelo banco")
    m.refresh_from_db()
    assert m.status == MonthlyFeeStatus.PENDING
    assert m.paid_at is None and m.payment_method == "" and m.payment_reference == ""

    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_FEE_PAYMENT_REVERSED)
    assert evento.metadata["antes"] == {
        "paid_at": HOJE.isoformat(),
        "payment_method": "PIX",
        "payment_reference": "E123",
    }
    assert evento.metadata["motivo"] == "Pix devolvido pelo banco"


def test_mensalidade_paga_nao_e_editada_nem_cancelada(ministro):
    m = pagar(nova(ministro))
    with pytest.raises(DomainError):
        servico.update_fee(m, due_date=HOJE, amount="10", notes="")
    with pytest.raises(DomainError):
        servico.cancel_fee(m, reason="x")
    with pytest.raises(DomainError):
        servico.exempt_fee(m, reason="x")


# ---------------------------------------------------------------------------
# Edicao, cancelamento, isencao, reativacao
# ---------------------------------------------------------------------------


def test_edicao_audita_antes_e_depois(ministro, admin_user):
    m = nova(ministro, valor="35.00")
    servico.update_fee(m, due_date=m.due_date, amount="40.00", notes="", actor=admin_user)
    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_FEE_UPDATED)
    assert evento.metadata["changed_fields"] == ["amount"]
    assert evento.metadata["antes"] == {"amount": "35.00"}
    assert evento.metadata["depois"] == {"amount": "40.00"}


def test_edicao_sem_mudanca_nao_audita(ministro):
    m = nova(ministro)
    servico.update_fee(m, due_date=m.due_date, amount=m.amount, notes="")
    assert not AuditLog.objects.filter(event=AuditEvent.MINISTER_FEE_UPDATED).exists()


def test_cancelar_isentar_e_reativar(ministro, admin_user):
    m = nova(ministro)
    servico.cancel_fee(m, reason="Lançada em duplicidade", actor=admin_user)
    m.refresh_from_db()
    assert m.status == MonthlyFeeStatus.CANCELLED
    assert m.status_reason == "Lançada em duplicidade"

    servico.reactivate_fee(m, reason="Cancelada por engano", actor=admin_user)
    servico.exempt_fee(m, reason="Isenção aprovada", actor=admin_user)
    servico.reactivate_fee(m, reason="Isenção revogada", actor=admin_user)
    m.refresh_from_db()
    assert m.status == MonthlyFeeStatus.PENDING

    eventos = list(
        AuditLog.objects.filter(entity_type="MinisterMonthlyFee", entity_id=str(m.pk))
        .order_by("id")
        .values_list("event", "metadata__motivo")
    )
    assert eventos[1:] == [
        (AuditEvent.MINISTER_FEE_CANCELLED, "Lançada em duplicidade"),
        (AuditEvent.MINISTER_FEE_REACTIVATED, "Cancelada por engano"),
        (AuditEvent.MINISTER_FEE_EXEMPTED, "Isenção aprovada"),
        (AuditEvent.MINISTER_FEE_REACTIVATED, "Isenção revogada"),
    ]


@pytest.mark.parametrize("acao", ["cancel_fee", "exempt_fee", "reactivate_fee", "reverse_payment"])
def test_motivo_e_obrigatorio(ministro, acao):
    m = nova(ministro)
    with pytest.raises(DomainError):
        getattr(servico, acao)(m, reason="")


def test_pendente_nao_e_reativada(ministro):
    with pytest.raises(DomainError):
        servico.reactivate_fee(nova(ministro), reason="x")


def test_auditoria_nunca_tem_senha(ministro_com_acesso, admin_user):
    from ministers.tests.conftest import SENHA_MINISTRO

    m = pagar(nova(ministro_com_acesso), actor=admin_user)
    servico.reverse_payment(m, reason="teste", actor=admin_user)
    for evento in AuditLog.objects.filter(entity_type="MinisterMonthlyFee"):
        texto = str(evento.metadata)
        assert SENHA_MINISTRO not in texto
        assert "pbkdf2" not in texto


# ---------------------------------------------------------------------------
# Totais
# ---------------------------------------------------------------------------


def test_totais(ministro, outro_ministro):
    from ministers.tests.conftest import dados_de_ministro

    terceiro = services.create_minister(
        dados=dados_de_ministro(full_name="Terceiro", conamad_registration="3", field_registration="")
    )
    quarto = services.create_minister(
        dados=dados_de_ministro(full_name="Quarto", conamad_registration="4", field_registration="")
    )
    pagar(nova(ministro, valor="35.00"))
    nova(outro_ministro, valor="50.00", vencimento=AMANHA)
    nova(terceiro, valor="20.00", vencimento=ONTEM)
    servico.cancel_fee(nova(quarto, valor="99.00"), reason="x")

    t = servico.totais(MinisterMonthlyFee.objects.filter(reference_month=OUTUBRO))
    assert t["previsto"] == Decimal("105.00")
    assert t["recebido"] == Decimal("35.00")
    assert t["pendente"] == Decimal("50.00")
    assert t["vencido"] == Decimal("20.00")
    assert t["qtd_ministros"] == 3
    assert (t["qtd_pagas"], t["qtd_pendentes"], t["qtd_vencidas"], t["qtd_canceladas"]) == (1, 1, 1, 1)


def test_totais_vazios_sao_zero():
    t = servico.totais(MinisterMonthlyFee.objects.none())
    assert t["previsto"] == Decimal("0.00")
    assert t["qtd_total"] == 0


def test_formatacao_em_reais():
    assert brl(Decimal("1234.5")) == "R$ 1.234,50"
    assert brl(Decimal("35")) == "R$ 35,00"
    assert brl(None) == ""
