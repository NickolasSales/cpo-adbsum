"""
Geracao de mensalidades em massa (Fase 2B).

    analisar()   le ministros e mensalidades da competencia e classifica
                 cada ministro. NAO escreve nada.
    gerar()      refaz a analise dentro de uma transacao travada e cria so
                 as mensalidades novas.

Regras
------
    ministro inativo            sempre ignorado; nao ha opcao para incluir
    ja tem mensalidade na       ignorado, seja qual for a situacao dela
    competencia                 (pendente, paga, cancelada, isenta): a
                                existente nao e editada nem reativada
    ativo sem mensalidade       recebe uma, pendente, com os parametros

Idempotencia
------------
O token do lote nasce no preview e e unico em MinisterFeeBatch. Reenviar a
confirmacao com o mesmo token devolve o lote ja gravado — nada roda de novo.
Uma geracao NOVA da mesma competencia (outro preview, outro token) so cria
para quem ainda nao tem: depois de entrar um ministro novo, ela cria uma.

Concorrencia
------------
Tres camadas, de fora para dentro:

    advisory lock por competencia   duas geracoes da mesma competencia
                                    viram fila; a segunda recalcula depois
                                    que a primeira confirmou
    select_for_update nos ativos    trava contra o cadastro manual
                                    (create_fee trava a mesma linha) e
                                    contra inativacao no meio da geracao
    UniqueConstraint                ultima barreira: se mesmo assim algo
                                    escapar, a transacao inteira volta

Por que bulk_create
-------------------
Um INSERT para o lote inteiro, e no PostgreSQL ele devolve os ids — entao
cada mensalidade ainda ganha o seu MINISTER_FEE_CREATED. O modelo nao tem
signal nem save() com regra que o bulk_create pularia: o save() so normaliza
a competencia para o dia 1, e aqui ela ja chega no dia 1. ignore_conflicts
NAO e usado de proposito: ele esconderia um conflito em vez de reverter, e
sem ids nao haveria como auditar cada mensalidade.
"""

import datetime
import secrets
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.db.models import Count

from audit.models import AuditEvent
from audit.services import record
from common.exceptions import DomainError
from ministers import cobranca
from ministers.mensalidades import _valor, _vencimento, competencia_de_texto
from ministers.models import (
    Minister,
    MinisterFeeBatch,
    MinisterMonthlyFee,
    MonthlyFeeStatus,
)

# Namespace do advisory lock: separa estes locks de qualquer outro que o
# banco venha a ter. O segundo inteiro e a competencia.
NAMESPACE_DO_LOCK = 7301


class Situacao:
    CRIAR = "CRIAR"
    EXISTE = "EXISTE"
    INATIVO = "INATIVO"


@dataclass(frozen=True)
class Parametros:
    competencia: datetime.date
    vencimento: datetime.date
    valor: Decimal

    def para_sessao(self):
        return {
            "competencia": self.competencia.strftime("%Y-%m"),
            "vencimento": self.vencimento.isoformat(),
            "valor": str(self.valor),
        }


def validar_parametros(competencia, vencimento, valor):
    """Mesmas regras do cadastro manual. Aceita texto ou tipos prontos."""
    if not isinstance(competencia, datetime.date):
        competencia = competencia_de_texto(competencia)
    competencia = competencia.replace(day=1)
    if isinstance(vencimento, str):
        try:
            vencimento = datetime.date.fromisoformat(vencimento)
        except ValueError:
            raise DomainError("Vencimento inválido.")
    return Parametros(
        competencia=competencia,
        vencimento=_vencimento(vencimento),
        valor=_valor(valor),
    )


def parametros_da_sessao(dados):
    return validar_parametros(dados["competencia"], dados["vencimento"], dados["valor"])


def novo_token():
    return secrets.token_urlsafe(24)


@dataclass
class Analise:
    parametros: Parametros
    linhas: list = field(default_factory=list)

    def _contar(self, situacao):
        return sum(1 for linha in self.linhas if linha["situacao"] == situacao)

    @property
    def novas(self):
        return self._contar(Situacao.CRIAR)

    @property
    def existentes(self):
        return self._contar(Situacao.EXISTE)

    @property
    def inativos(self):
        return self._contar(Situacao.INATIVO)

    @property
    def ativos(self):
        return self.novas + self.existentes

    @property
    def total(self):
        return self.parametros.valor * self.novas

    @property
    def ids_a_criar(self):
        return [linha["id"] for linha in self.linhas if linha["situacao"] == Situacao.CRIAR]


def analisar(parametros, *, ministros=None):
    """
    Classifica todos os ministros para a competencia. Somente leitura.

    Duas consultas, qualquer que seja o numero de ministros: uma para os
    ministros, outra para quem ja tem mensalidade na competencia.
    """
    if ministros is None:
        ministros = Minister.objects.all()
    colunas = (
        "id",
        "full_name",
        "conamad_registration",
        "field_registration",
        "congregation",
        "is_active",
    )
    ocupados = set(
        MinisterMonthlyFee.objects.filter(
            reference_month=parametros.competencia
        ).values_list("minister_id", flat=True)
    )
    linhas = []
    for ministro in ministros.order_by("full_name", "id").values(*colunas):
        # Inativo vem primeiro: ele e ignorado de qualquer forma, e assim
        # ativos = novas + existentes fecha a conta no preview.
        if not ministro["is_active"]:
            situacao = Situacao.INATIVO
        elif ministro["id"] in ocupados:
            situacao = Situacao.EXISTE
        else:
            situacao = Situacao.CRIAR
        linhas.append(dict(ministro, situacao=situacao))
    return Analise(parametros=parametros, linhas=linhas)


def _travar_competencia(competencia):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            [NAMESPACE_DO_LOCK, competencia.toordinal()],
        )


def gerar(parametros, *, token, actor=None, request=None):
    """
    Cria as mensalidades novas da competencia e devolve o MinisterFeeBatch.

    Tudo numa transacao. Com o mesmo token, devolve o lote ja gravado sem
    criar nada.
    """
    if not token:
        raise DomainError("Lote de geração inválido.")

    try:
        with transaction.atomic():
            _travar_competencia(parametros.competencia)

            existente = MinisterFeeBatch.objects.filter(token=token).first()
            if existente is not None:
                return existente

            # Recalcula tudo AGORA, com os ativos travados. O preview que o
            # administrador viu nao decide nada.
            ativos = set(
                Minister.objects.select_for_update()
                .filter(is_active=True)
                .values_list("id", flat=True)
            )
            analise = analisar(parametros)
            ids = [pk for pk in analise.ids_a_criar if pk in ativos]

            lote = MinisterFeeBatch.objects.create(
                token=token,
                reference_month=parametros.competencia,
                due_date=parametros.vencimento,
                amount=parametros.valor,
                created_by=actor if getattr(actor, "pk", None) else None,
            )

            criadas = MinisterMonthlyFee.objects.bulk_create(
                [
                    MinisterMonthlyFee(
                        minister_id=pk,
                        reference_month=parametros.competencia,
                        due_date=parametros.vencimento,
                        amount=parametros.valor,
                        status=MonthlyFeeStatus.PENDING,
                    )
                    for pk in ids
                ]
            )

            competencia = parametros.competencia.strftime("%Y-%m")
            for mensalidade in criadas:
                record(
                    AuditEvent.MINISTER_FEE_CREATED,
                    request=request,
                    actor=actor,
                    entity_type="MinisterMonthlyFee",
                    entity_id=mensalidade.pk,
                    metadata={
                        "minister_id": mensalidade.minister_id,
                        "competencia": competencia,
                        "depois": {
                            "amount": str(parametros.valor),
                            "due_date": parametros.vencimento.isoformat(),
                        },
                        "lote": lote.pk,
                    },
                )

            # Cobrancas na mesma transacao (Fase 2D): se uma falhar, o lote
            # inteiro volta. Sem configuracao pronta, zero — as mensalidades
            # nascem sem cobranca e a acao "Gerar cobrancas pendentes" cuida
            # delas depois.
            cobrancas = cobranca.gerar_para_novas(criadas, actor=actor)

            lote.created_count = len(criadas)
            lote.existing_count = analise.existentes
            lote.inactive_count = analise.inativos
            lote.total_amount = parametros.valor * len(criadas)
            lote.charge_count = cobrancas
            lote.save(
                update_fields=[
                    "created_count",
                    "existing_count",
                    "inactive_count",
                    "total_amount",
                    "charge_count",
                ]
            )

            record(
                AuditEvent.MINISTER_FEE_BATCH_CREATED,
                request=request,
                actor=actor,
                entity_type="MinisterFeeBatch",
                entity_id=lote.pk,
                metadata={
                    "competencia": competencia,
                    "vencimento": parametros.vencimento.isoformat(),
                    "valor": str(parametros.valor),
                    "criadas": lote.created_count,
                    "existentes": lote.existing_count,
                    "inativos_ignorados": lote.inactive_count,
                    "total_criado": str(lote.total_amount),
                    "cobrancas_geradas": lote.charge_count,
                },
            )
    except IntegrityError:
        # So chega aqui se algo escapou das travas — por exemplo, uma
        # mensalidade gravada por fora delas. Nada foi gravado.
        raise DomainError(
            "Outra operação gravou mensalidades desta competência ao mesmo "
            "tempo. Nada foi criado. Gere o preview novamente."
        )
    return lote


def valor_sugerido():
    """
    O valor mais usado na competencia mais recente, para pre-preencher o
    formulario. So sugestao: nenhum valor vive no codigo.
    """
    ultima = (
        MinisterMonthlyFee.objects.exclude(status=MonthlyFeeStatus.CANCELLED)
        .order_by("-reference_month")
        .values_list("reference_month", flat=True)
        .first()
    )
    if ultima is None:
        return None
    return (
        MinisterMonthlyFee.objects.filter(reference_month=ultima)
        .exclude(status=MonthlyFeeStatus.CANCELLED)
        .values("amount")
        .annotate(n=Count("id"))
        .order_by("-n", "-amount")
        .values_list("amount", flat=True)
        .first()
    )
