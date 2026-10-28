"""
Regras das mensalidades de ministros.

Toda escrita financeira passa por aqui. As views validam o formulario e
chamam uma funcao deste modulo; nenhuma toca no modelo diretamente.

Transicoes permitidas
---------------------
    criar                    -> PENDING
    editar (valor, venc.)    PENDING -> PENDING
    registrar pagamento      PENDING -> PAID       (inclusive vencida)
    corrigir pagamento       PAID -> PAID          motivo obrigatorio
    estornar pagamento       PAID -> PENDING       motivo obrigatorio
    cancelar                 PENDING -> CANCELLED  motivo obrigatorio
    isentar                  PENDING -> EXEMPT     motivo obrigatorio
    reativar                 CANCELLED|EXEMPT -> PENDING  motivo obrigatorio

Uma mensalidade paga nao e editada nem cancelada direto: primeiro o estorno,
com motivo, e so entao o resto. Assim nenhum dinheiro recebido some da tela
sem deixar rastro de quem desfez o registro e por que.

Cada transicao trava a linha (select_for_update) e confere a situacao DENTRO
da transacao: dois administradores registrando o mesmo pagamento ao mesmo
tempo produzem um registro e uma recusa, e nunca dois eventos.
"""

import datetime
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record
from common.exceptions import DomainError
from common.texto import validar_motivo
from ministers import cobranca
from ministers.models import (
    FeeSituation,
    Minister,
    MinisterMonthlyFee,
    MonthlyFeeStatus,
    PaymentMethod,
)

ANO_MINIMO = 2000
ANO_MAXIMO = 2100
VALOR_MAXIMO = Decimal("99999999.99")
TAMANHO_REFERENCIA = 120

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Competencia
# ---------------------------------------------------------------------------


def competencia_de_texto(texto):
    """
    "2026-10" -> date(2026, 10, 1). A UNICA forma de competencia aceita
    vinda de fora (formulario, filtro, URL). Qualquer outra coisa e recusada.
    """
    texto = (texto or "").strip()
    try:
        ano, mes = texto.split("-")
        if len(ano) != 4 or len(mes) != 2:
            raise ValueError
        data = datetime.date(int(ano), int(mes), 1)
    except (ValueError, TypeError):
        raise DomainError("Competência inválida. Use o formato AAAA-MM.")
    if not (ANO_MINIMO <= data.year <= ANO_MAXIMO):
        raise DomainError(
            "Competência fora do intervalo aceito ({}-{}).".format(ANO_MINIMO, ANO_MAXIMO)
        )
    return data


def competencia_ou_none(texto):
    try:
        return competencia_de_texto(texto) if texto else None
    except DomainError:
        return None


def competencias_existentes():
    return list(
        MinisterMonthlyFee.objects.values_list("reference_month", flat=True)
        .distinct()
        .order_by("-reference_month")
    )


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------


def filtro_de_situacao(situacao, hoje=None):
    """Q equivalente a uma situacao exibida."""
    hoje = hoje or timezone.localdate()
    if situacao == FeeSituation.OVERDUE:
        return Q(status=MonthlyFeeStatus.PENDING, due_date__lt=hoje)
    if situacao == FeeSituation.PENDING:
        return Q(status=MonthlyFeeStatus.PENDING, due_date__gte=hoje)
    if situacao in MonthlyFeeStatus.values:
        return Q(status=situacao)
    return Q()


def mensalidades_queryset():
    return MinisterMonthlyFee.objects.select_related("minister")


# O que o Portal do Ministro pode ler de uma mensalidade. Observacoes,
# motivos e a referencia do pagamento sao anotacoes da secretaria: nem saem
# do banco quando a consulta e para o portal.
COLUNAS_DO_PORTAL = (
    "reference_month",
    "due_date",
    "amount",
    "status",
    "paid_at",
    "payment_method",
)


def mensalidades_do_ministro(ministro):
    """
    As mensalidades de UM ministro, da competencia mais recente para a mais
    antiga. Para o portal: `ministro` e sempre o da sessao autenticada, nunca
    um id vindo da requisicao.
    """
    return (
        MinisterMonthlyFee.objects.filter(minister=ministro)
        .only(*COLUNAS_DO_PORTAL)
        .order_by("-reference_month")
    )


def totais(consulta, hoje=None):
    """
    Numeros do dashboard, numa consulta so.

    Previsto conta o que se espera receber: pendentes, vencidas e pagas.
    Isentas e canceladas nao entram — nao ha o que receber delas.
    """
    hoje = hoje or timezone.localdate()
    decimal = DecimalField(max_digits=14, decimal_places=2)
    pago = Q(status=MonthlyFeeStatus.PAID)
    pendente = Q(status=MonthlyFeeStatus.PENDING, due_date__gte=hoje)
    vencido = Q(status=MonthlyFeeStatus.PENDING, due_date__lt=hoje)
    cobravel = Q(status__in=[MonthlyFeeStatus.PENDING, MonthlyFeeStatus.PAID])

    def soma(condicao):
        return Coalesce(Sum("amount", filter=condicao), Value(ZERO), output_field=decimal)

    return consulta.aggregate(
        previsto=soma(cobravel),
        recebido=soma(pago),
        pendente=soma(pendente),
        vencido=soma(vencido),
        qtd_ministros=Count("minister", filter=~Q(status=MonthlyFeeStatus.CANCELLED), distinct=True),
        qtd_total=Count("id"),
        qtd_pagas=Count("id", filter=pago),
        qtd_pendentes=Count("id", filter=pendente),
        qtd_vencidas=Count("id", filter=vencido),
        qtd_isentas=Count("id", filter=Q(status=MonthlyFeeStatus.EXEMPT)),
        qtd_canceladas=Count("id", filter=Q(status=MonthlyFeeStatus.CANCELLED)),
    )


def ultimo_valor_do_ministro(ministro):
    """Valor da mensalidade mais recente do ministro, para sugerir no formulario."""
    return (
        MinisterMonthlyFee.objects.filter(minister=ministro)
        .exclude(status=MonthlyFeeStatus.CANCELLED)
        .order_by("-reference_month")
        .values_list("amount", flat=True)
        .first()
    )


# ---------------------------------------------------------------------------
# Validacao
# ---------------------------------------------------------------------------


def _valor(valor):
    try:
        valor = Decimal(str(valor)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise DomainError("Valor inválido.")
    if valor <= 0:
        raise DomainError("O valor precisa ser maior que zero.")
    if valor > VALOR_MAXIMO:
        raise DomainError("Valor acima do limite aceito.")
    return valor


def _vencimento(data):
    if not isinstance(data, datetime.date):
        raise DomainError("Informe o vencimento.")
    if not (ANO_MINIMO <= data.year <= ANO_MAXIMO):
        raise DomainError("Vencimento fora do intervalo aceito.")
    return data


def _dados_de_pagamento(paid_at, payment_method, payment_reference):
    if not isinstance(paid_at, datetime.date):
        raise DomainError("Informe a data do pagamento.")
    if paid_at > timezone.localdate():
        raise DomainError("A data do pagamento não pode estar no futuro.")
    if paid_at.year < ANO_MINIMO:
        raise DomainError("Data do pagamento fora do intervalo aceito.")
    if payment_method not in PaymentMethod.values:
        raise DomainError("Informe a forma de pagamento.")
    referencia = " ".join((payment_reference or "").split())
    if len(referencia) > TAMANHO_REFERENCIA:
        raise DomainError(
            "A referência pode ter no máximo {} caracteres.".format(TAMANHO_REFERENCIA)
        )
    return paid_at, payment_method, referencia


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------


def _texto_auditavel(valor):
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, datetime.date):
        return valor.isoformat()
    return valor


def _auditar(evento, mensalidade, *, actor, request, **extra):
    metadata = {
        "minister_id": mensalidade.minister_id,
        "competencia": mensalidade.competencia_chave,
    }
    for chave, valor in extra.items():
        if isinstance(valor, dict):
            valor = {k: _texto_auditavel(v) for k, v in valor.items()}
        metadata[chave] = _texto_auditavel(valor)
    record(
        evento,
        request=request,
        actor=actor,
        entity_type="MinisterMonthlyFee",
        entity_id=mensalidade.pk,
        metadata=metadata,
    )


def _retrato(mensalidade, campos):
    return {campo: getattr(mensalidade, campo) for campo in campos}


CAMPOS_DE_PAGAMENTO = ("paid_at", "payment_method", "payment_reference")


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------


def create_fee(
    *,
    minister,
    reference_month,
    due_date,
    amount,
    notes="",
    permitir_inativo=False,
    actor=None,
    request=None,
):
    """
    Cria uma mensalidade pendente.

    Ministro inativo so recebe mensalidade com permissao explicita
    (`permitir_inativo`): a geracao em massa nunca a da, e o cadastro manual
    exige que o administrador marque a confirmacao.
    """
    if not isinstance(reference_month, datetime.date):
        reference_month = competencia_de_texto(reference_month)
    reference_month = reference_month.replace(day=1)
    if not (ANO_MINIMO <= reference_month.year <= ANO_MAXIMO):
        raise DomainError("Competência fora do intervalo aceito.")
    valor = _valor(amount)
    vencimento = _vencimento(due_date)

    try:
        with transaction.atomic():
            ministro = Minister.objects.select_for_update().get(pk=minister.pk)
            if not ministro.is_active and not permitir_inativo:
                raise DomainError(
                    "Este ministro está inativo. Confirme explicitamente para "
                    "criar a mensalidade."
                )
            if MinisterMonthlyFee.objects.filter(
                minister=ministro, reference_month=reference_month
            ).exists():
                raise DomainError(
                    "Este ministro já tem mensalidade na competência {}.".format(
                        reference_month.strftime("%m/%Y")
                    )
                )
            mensalidade = MinisterMonthlyFee.objects.create(
                minister=ministro,
                reference_month=reference_month,
                due_date=vencimento,
                amount=valor,
                notes=(notes or "").strip(),
            )
            _auditar(
                AuditEvent.MINISTER_FEE_CREATED,
                mensalidade,
                actor=actor,
                request=request,
                depois={"amount": valor, "due_date": vencimento},
                ministro_inativo=not ministro.is_active,
            )
    except IntegrityError:
        # A constraint de unicidade pegou uma criacao concorrente.
        raise DomainError("Este ministro já tem mensalidade nesta competência.")
    return mensalidade


def _travar(mensalidade, *permitidos, acao):
    atual = MinisterMonthlyFee.objects.select_for_update().get(pk=mensalidade.pk)
    if atual.status not in permitidos:
        raise DomainError(
            "Não é possível {} uma mensalidade com situação {}.".format(
                acao, atual.get_status_display().lower()
            )
        )
    return atual


def update_fee(mensalidade, *, due_date, amount, notes, actor=None, request=None):
    """Altera valor, vencimento e observacoes. So de mensalidade pendente."""
    valor = _valor(amount)
    vencimento = _vencimento(due_date)
    novas = {"amount": valor, "due_date": vencimento, "notes": (notes or "").strip()}

    with transaction.atomic():
        atual = _travar(mensalidade, MonthlyFeeStatus.PENDING, acao="alterar")
        alterados = [campo for campo, valor_novo in novas.items() if getattr(atual, campo) != valor_novo]
        if not alterados:
            return atual
        antes = _retrato(atual, alterados)
        for campo in alterados:
            setattr(atual, campo, novas[campo])
        atual.save(update_fields=alterados + ["updated_at"])

        # Valor e vencimento estao impressos na cobranca. Se ela ja existe,
        # vira historico e da lugar a uma nova (Fase 2D) — na mesma
        # transacao, para nunca existir cobranca com o valor antigo ao lado
        # da mensalidade com o valor novo.
        extra = {}
        if {"amount", "due_date"} & set(alterados):
            substituida, nova = cobranca.substituir_por_alteracao(atual, actor=actor)
            if substituida is not None:
                extra["cobranca_substituida"] = substituida.txid
                extra["cobranca_nova"] = nova.txid if nova is not None else ""

        _auditar(
            AuditEvent.MINISTER_FEE_UPDATED,
            atual,
            actor=actor,
            request=request,
            changed_fields=alterados,
            antes=antes,
            depois=_retrato(atual, alterados),
            **extra,
        )
    return atual


def register_payment(
    mensalidade, *, paid_at, payment_method, payment_reference="", notes=None, actor=None, request=None
):
    """Baixa manual: PENDING (vencida ou nao) -> PAID."""
    paid_at, payment_method, referencia = _dados_de_pagamento(
        paid_at, payment_method, payment_reference
    )
    with transaction.atomic():
        atual = _travar(mensalidade, MonthlyFeeStatus.PENDING, acao="registrar pagamento de")
        vencida = atual.due_date < timezone.localdate()
        atual.status = MonthlyFeeStatus.PAID
        atual.paid_at = paid_at
        atual.payment_method = payment_method
        atual.payment_reference = referencia
        campos = ["status", "paid_at", "payment_method", "payment_reference", "updated_at"]
        if notes is not None and notes.strip():
            atual.notes = notes.strip()
            campos.append("notes")
        atual.save(update_fields=campos)
        _auditar(
            AuditEvent.MINISTER_FEE_PAYMENT_REGISTERED,
            atual,
            actor=actor,
            request=request,
            depois=_retrato(atual, CAMPOS_DE_PAGAMENTO + ("amount",)),
            estava_vencida=vencida,
        )
    return atual


def correct_payment(
    mensalidade, *, paid_at, payment_method, payment_reference="", reason, actor=None, request=None
):
    """Correcao de um pagamento ja registrado. Exige motivo; audita antes e depois."""
    motivo = validar_motivo(reason, vazio="Informe o motivo da correção.")
    paid_at, payment_method, referencia = _dados_de_pagamento(
        paid_at, payment_method, payment_reference
    )
    with transaction.atomic():
        atual = _travar(mensalidade, MonthlyFeeStatus.PAID, acao="corrigir o pagamento de")
        novos = {"paid_at": paid_at, "payment_method": payment_method, "payment_reference": referencia}
        alterados = [campo for campo, valor in novos.items() if getattr(atual, campo) != valor]
        if not alterados:
            raise DomainError("Nenhum dado do pagamento foi alterado.")
        antes = _retrato(atual, alterados)
        for campo in alterados:
            setattr(atual, campo, novos[campo])
        atual.status_reason = motivo
        atual.save(update_fields=alterados + ["status_reason", "updated_at"])
        _auditar(
            AuditEvent.MINISTER_FEE_PAYMENT_UPDATED,
            atual,
            actor=actor,
            request=request,
            changed_fields=alterados,
            antes=antes,
            depois=_retrato(atual, alterados),
            motivo=motivo,
        )
    return atual


def reverse_payment(mensalidade, *, reason, actor=None, request=None):
    """
    Estorno: PAID -> PENDING. O registro do pagamento sai da mensalidade, mas
    vai inteiro para a trilha — data, forma e referencia — junto do motivo.
    """
    motivo = validar_motivo(reason, vazio="Informe o motivo do estorno.")
    with transaction.atomic():
        atual = _travar(mensalidade, MonthlyFeeStatus.PAID, acao="estornar o pagamento de")
        antes = _retrato(atual, CAMPOS_DE_PAGAMENTO)
        atual.status = MonthlyFeeStatus.PENDING
        atual.paid_at = None
        atual.payment_method = ""
        atual.payment_reference = ""
        atual.status_reason = motivo
        atual.save(
            update_fields=["status", "status_reason", "updated_at", *CAMPOS_DE_PAGAMENTO]
        )
        _auditar(
            AuditEvent.MINISTER_FEE_PAYMENT_REVERSED,
            atual,
            actor=actor,
            request=request,
            antes=antes,
            motivo=motivo,
        )
    return atual


def _mudar_situacao(mensalidade, *, permitidos, nova, evento, acao, vazio, reason, actor, request):
    motivo = validar_motivo(reason, vazio=vazio)
    with transaction.atomic():
        atual = _travar(mensalidade, *permitidos, acao=acao)
        anterior = atual.status
        atual.status = nova
        atual.status_reason = motivo
        atual.save(update_fields=["status", "status_reason", "updated_at"])
        _auditar(
            evento,
            atual,
            actor=actor,
            request=request,
            antes={"status": anterior},
            depois={"status": nova},
            motivo=motivo,
        )
    return atual


def cancel_fee(mensalidade, *, reason, actor=None, request=None):
    return _mudar_situacao(
        mensalidade,
        permitidos=(MonthlyFeeStatus.PENDING,),
        nova=MonthlyFeeStatus.CANCELLED,
        evento=AuditEvent.MINISTER_FEE_CANCELLED,
        acao="cancelar",
        vazio="Informe o motivo do cancelamento.",
        reason=reason,
        actor=actor,
        request=request,
    )


def exempt_fee(mensalidade, *, reason, actor=None, request=None):
    return _mudar_situacao(
        mensalidade,
        permitidos=(MonthlyFeeStatus.PENDING,),
        nova=MonthlyFeeStatus.EXEMPT,
        evento=AuditEvent.MINISTER_FEE_EXEMPTED,
        acao="isentar",
        vazio="Informe o motivo da isenção.",
        reason=reason,
        actor=actor,
        request=request,
    )


def reactivate_fee(mensalidade, *, reason, actor=None, request=None):
    return _mudar_situacao(
        mensalidade,
        permitidos=(MonthlyFeeStatus.CANCELLED, MonthlyFeeStatus.EXEMPT),
        nova=MonthlyFeeStatus.PENDING,
        evento=AuditEvent.MINISTER_FEE_REACTIVATED,
        acao="reativar",
        vazio="Informe o motivo da reativação.",
        reason=reason,
        actor=actor,
        request=request,
    )
