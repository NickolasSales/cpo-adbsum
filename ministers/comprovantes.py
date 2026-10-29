"""
Comprovantes de pagamento das mensalidades (Fase 2E).

    ministro envia       PENDING (vencida ou nao), com cobranca  -> comprovante SUBMITTED
    admin aprova         SUBMITTED -> APPROVED  e  mensalidade PENDING -> PAID
    admin recusa         SUBMITTED -> REJECTED  (motivo); mensalidade continua PENDING
    ministro reenvia     novo comprovante SUBMITTED; o recusado fica no historico

A conferencia e humana. Nada aqui le o conteudo do arquivo, consulta banco ou
aprova sozinho.

O comprovante nao e um status da mensalidade
--------------------------------------------
Com um comprovante SUBMITTED a mensalidade continua PENDING: em aberto nos
totais, "Vencido" calculado como sempre. "Aguardando analise" e derivado da
existencia do SUBMITTED. So a aprovacao muda a mensalidade — e muda pelo
MESMO register_payment da baixa manual (2A), com forma Pix: data, forma,
referencia, situacao e o evento MINISTER_FEE_PAYMENT_REGISTERED saem de la,
nao de uma copia daquela logica.

Travas
------
Todas as operacoes travam primeiro a MENSALIDADE e depois o comprovante —
sempre nessa ordem, a mesma da baixa manual, entao nao ha deadlock entre
elas. Com a linha travada:

    envio x envio        o segundo ve o SUBMITTED do primeiro e e recusado;
                         a constraint parcial do banco e a segunda barreira.
    envio x baixa manual quem chega depois ve o resultado do outro: depois de
                         PAID nao nasce comprovante, e com comprovante em
                         analise a baixa manual pede para analisa-lo antes
                         (ministers.mensalidades).
    aprovar x aprovar    o segundo encontra APPROVED e recebe
    aprovar x recusar    DecisaoJaTomada; nunca dois eventos financeiros.

O arquivo e gravado no disco DENTRO da transacao, depois de todas as
conferencias. Se a transacao desfaz, o arquivo e apagado: nao sobra arquivo
sem registro.
"""

import logging

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from django.http import FileResponse, Http404
from django.utils import timezone
from django.utils.cache import add_never_cache_headers, patch_cache_control

from audit.models import AuditEvent
from audit.services import record
from common.exceptions import DomainError
from common.texto import validar_motivo
from ministers import cobranca
from ministers import mensalidades as financeiro
from ministers.arquivo_comprovante import conferir_comprovante
from ministers.models import (
    EXTENSAO_DO_COMPROVANTE,
    MinisterMonthlyFee,
    MinisterPaymentProof,
    MonthlyFeeStatus,
    PaymentMethod,
    ProofStatus,
)
from ministers.pix import texto_do_br_code

logger = logging.getLogger(__name__)

JA_EM_ANALISE = (
    "Já existe um comprovante aguardando análise para esta mensalidade. "
    "Aguarde a conferência da secretaria."
)


class EnvioRecusado(DomainError):
    """
    A mensalidade nao aceita comprovante agora (paga, isenta, cancelada, sem
    cobranca ou com outro em analise). Diferente de um arquivo invalido: nao
    ha o que corrigir no arquivo.
    """


class DecisaoJaTomada(DomainError):
    """Outro administrador analisou o comprovante primeiro."""


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------


def em_analise():
    return MinisterPaymentProof.objects.filter(status=ProofStatus.SUBMITTED)


def anotar_analise(consulta):
    """Acrescenta `em_analise` (Exists) a uma consulta de mensalidades."""
    return consulta.annotate(em_analise=Exists(em_analise().filter(fee=OuterRef("pk"))))


def tem_comprovante_em_analise(mensalidade):
    return em_analise().filter(fee=mensalidade).exists()


def quantos_em_analise():
    return em_analise().count()


def do_portal(mensalidade):
    """
    O historico que o MINISTRO ve: enviado em, situacao, motivo da recusa.
    Sem quem analisou, sem id, hash ou caminho — nem saem do banco.
    """
    return list(
        MinisterPaymentProof.objects.filter(fee=mensalidade)
        .only("fee_id", "submitted_at", "status", "rejection_reason", "mime_type")
        .order_by("submitted_at", "id")
    )


def motivo_para_nao_enviar(mensalidade, *, tem_cobranca, tem_em_analise):
    """
    Por que esta mensalidade nao aceita comprovante agora, ou None se aceita.
    So leitura: o servico confere tudo de novo com a linha travada.
    """
    if mensalidade.status == MonthlyFeeStatus.PAID:
        return "Esta mensalidade já está paga."
    if mensalidade.status == MonthlyFeeStatus.EXEMPT:
        return "Esta mensalidade é isenta: não há pagamento a comprovar."
    if mensalidade.status == MonthlyFeeStatus.CANCELLED:
        return "Esta mensalidade foi cancelada: não há pagamento a comprovar."
    if not tem_cobranca:
        return "A cobrança desta mensalidade ainda não foi emitida."
    if tem_em_analise:
        return JA_EM_ANALISE
    return None


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------


def _auditar(evento, comprovante, mensalidade, *, actor=None, request=None, **extra):
    metadata = {
        "minister_id": mensalidade.minister_id,
        "competencia": mensalidade.competencia_chave,
        "comprovante": comprovante.pk,
    }
    metadata.update(extra)
    record(
        evento,
        request=request,
        actor=actor,
        entity_type="MinisterMonthlyFee",
        entity_id=mensalidade.pk,
        metadata=metadata,
    )


def _travar(mensalidade_id, comprovante_id=None):
    """Mensalidade primeiro, comprovante depois. Sempre nessa ordem."""
    mensalidade = MinisterMonthlyFee.objects.select_for_update().get(pk=mensalidade_id)
    if comprovante_id is None:
        return mensalidade, None
    comprovante = MinisterPaymentProof.objects.select_for_update().get(pk=comprovante_id)
    return mensalidade, comprovante


# ---------------------------------------------------------------------------
# Envio (portal)
# ---------------------------------------------------------------------------


def enviar_comprovante(mensalidade, arquivo, *, request=None):
    """
    O ministro envia o comprovante de UMA mensalidade dele.

    `mensalidade` ja vem filtrada pelo ministro da sessao (a view nao aceita
    id). O arquivo e conferido antes de travar qualquer coisa — e o passo
    caro, e nao precisa de lock.
    """
    conferido = conferir_comprovante(arquivo)
    gravado = None
    try:
        with transaction.atomic():
            atual, _ = _travar(mensalidade.pk)
            motivo = motivo_para_nao_enviar(
                atual,
                tem_cobranca=cobranca.cobranca_vigente(atual) is not None,
                tem_em_analise=tem_comprovante_em_analise(atual),
            )
            if motivo:
                raise EnvioRecusado(motivo)

            comprovante = MinisterPaymentProof(
                fee=atual,
                original_filename=conferido.nome_original,
                mime_type=conferido.mime_type,
                file_size=conferido.tamanho,
                sha256=conferido.sha256,
            )
            # O nome passado aqui e ignorado por caminho_do_comprovante.
            comprovante.file.save("comprovante", arquivo, save=False)
            gravado = comprovante.file.name
            comprovante.save()

            _auditar(
                AuditEvent.MINISTER_PAYMENT_PROOF_SUBMITTED,
                comprovante,
                atual,
                request=request,
                file_size=conferido.tamanho,
                mime_type=conferido.mime_type,
                estava_vencida=atual.due_date < timezone.localdate(),
            )
    except BaseException as erro:
        if gravado:
            _apagar_arquivo_orfao(gravado)
        if isinstance(erro, IntegrityError):
            # A constraint parcial pegou um envio concorrente.
            raise EnvioRecusado(JA_EM_ANALISE)
        raise
    return comprovante


def _apagar_arquivo_orfao(nome):
    storage = MinisterPaymentProof._meta.get_field("file").storage
    try:
        storage.delete(nome)
    except OSError:
        logger.exception("Nao foi possivel apagar o arquivo de um envio desfeito")


# ---------------------------------------------------------------------------
# Analise (painel)
# ---------------------------------------------------------------------------


def _so_em_analise(comprovante):
    if comprovante.status != ProofStatus.SUBMITTED:
        raise DecisaoJaTomada(
            "Este comprovante já foi analisado: {}. Nada foi alterado.".format(
                comprovante.get_status_display().lower()
            )
        )


def referencia_do_pagamento(comprovante):
    """O que vai em payment_reference: so o numero do comprovante."""
    return "Comprovante do portal nº {}".format(comprovante.pk)


def aprovar_comprovante(comprovante, *, paid_at, actor, request=None):
    """
    SUBMITTED -> APPROVED e a mensalidade -> PAID, na mesma transacao.

    A data do pagamento e informada pelo administrador: o ministro pode ter
    pago num dia e enviado o comprovante dias depois. Ela nao pode ser
    posterior ao envio (nem futura — register_payment ja recusa).
    """
    with transaction.atomic():
        mensalidade, atual = _travar(comprovante.fee_id, comprovante.pk)
        _so_em_analise(atual)
        if paid_at and paid_at > timezone.localdate(atual.submitted_at):
            raise DomainError(
                "A data do pagamento não pode ser posterior ao envio do comprovante ({}).".format(
                    timezone.localdate(atual.submitted_at).strftime("%d/%m/%Y")
                )
            )

        # Primeiro o comprovante deixa de estar em analise; so entao a baixa,
        # que recusa mensalidade com comprovante SUBMITTED.
        atual.status = ProofStatus.APPROVED
        atual.reviewed_by = actor
        atual.reviewed_at = timezone.now()
        atual.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])

        financeiro.register_payment(
            mensalidade,
            paid_at=paid_at,
            payment_method=PaymentMethod.PIX,
            payment_reference=referencia_do_pagamento(atual),
            actor=actor,
            request=request,
        )
        _auditar(
            AuditEvent.MINISTER_PAYMENT_PROOF_APPROVED,
            atual,
            mensalidade,
            actor=actor,
            request=request,
            payment_method=PaymentMethod.PIX,
            paid_at=paid_at.isoformat(),
        )
    return atual


def recusar_comprovante(comprovante, *, motivo, actor, request=None):
    """SUBMITTED -> REJECTED com motivo. A mensalidade nao muda."""
    motivo = validar_motivo(motivo, vazio="Informe o motivo da recusa.")
    with transaction.atomic():
        mensalidade, atual = _travar(comprovante.fee_id, comprovante.pk)
        _so_em_analise(atual)
        atual.status = ProofStatus.REJECTED
        atual.reviewed_by = actor
        atual.reviewed_at = timezone.now()
        atual.rejection_reason = motivo
        atual.save(
            update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at"]
        )
        # O motivo fica no comprovante; a trilha registra so que houve recusa.
        _auditar(
            AuditEvent.MINISTER_PAYMENT_PROOF_REJECTED,
            atual,
            mensalidade,
            actor=actor,
            request=request,
            rejected=True,
        )
    return atual


# ---------------------------------------------------------------------------
# Entrega do arquivo
# ---------------------------------------------------------------------------


def nome_para_download(comprovante, competencia, numero):
    """comprovante-outubro-2026-2.pdf. Sem nome de pessoa, sem acento."""
    mes = texto_do_br_code(cobranca.nome_do_mes(competencia)).lower()
    return "comprovante-{}-{}-{}{}".format(
        mes, competencia.year, numero, EXTENSAO_DO_COMPROVANTE.get(comprovante.mime_type, "")
    )


def resposta_do_arquivo(comprovante, *, nome, inline):
    """
    O arquivo, lido do disco em blocos (FileResponse), nunca inteiro na
    memoria.

    Content-Type e o tipo DETECTADO no envio, com nosniff: o navegador nao
    "adivinha" outro. Sem cache em lugar nenhum. O nome do download e
    montado pelo chamador por lista branca (mes, ano, numero) — o nome
    original nao chega ao cabecalho.
    """
    try:
        arquivo = comprovante.file.open("rb")
    except (FileNotFoundError, OSError, ValueError):
        logger.error("Arquivo do comprovante %s nao encontrado no disco", comprovante.pk)
        raise Http404
    resposta = FileResponse(
        arquivo,
        content_type=comprovante.mime_type,
        as_attachment=not inline,
        filename=nome,
    )
    resposta["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(resposta)
    patch_cache_control(resposta, private=True)
    return resposta
