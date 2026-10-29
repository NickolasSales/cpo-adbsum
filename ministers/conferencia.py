"""
Conferencias de consistencia do Controle de Ministros. Somente leitura.

Usadas por `conferir_ministros` (tudo) e `conferir_comprovantes` (so os
arquivos). Nenhuma corrige nada: dizem o que esta errado e onde, para uma
pessoa decidir. Varias das regras ja sao garantidas por constraint no banco;
conferir de novo custa uma consulta e responde "esta tudo certo?" depois de
um restore, de uma migracao ou antes de abrir o portal.
"""

import hashlib
import os
from dataclasses import dataclass, field

from django.conf import settings
from django.db.models import Count, F, Q

from ministers.models import (
    PASTA_DOS_COMPROVANTES,
    MinisterAccount,
    MinisterFeeCharge,
    MinisterMonthlyFee,
    MinisterPaymentProof,
    MonthlyFeeStatus,
    ProofStatus,
)


@dataclass
class Resultado:
    descricao: str
    ids: list = field(default_factory=list)

    @property
    def quantidade(self):
        return len(self.ids)


def _sha256(caminho):
    digest = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


@dataclass
class Arquivos:
    total: int = 0
    na_pasta: int = 0
    faltando: list = field(default_factory=list)
    divergentes: list = field(default_factory=list)
    orfaos: list = field(default_factory=list)

    @property
    def conferidos(self):
        return self.total - len(self.faltando) - len(self.divergentes)


def arquivos_dos_comprovantes():
    """Cada comprovante tem arquivo e o SHA-256 bate? Algum arquivo sem registro?"""
    pasta = os.path.join(settings.MEDIA_ROOT, PASTA_DOS_COMPROVANTES)
    resultado = Arquivos()
    no_banco = set()
    consulta = MinisterPaymentProof.objects.only("id", "file", "sha256").order_by("id")
    for comprovante in consulta.iterator(chunk_size=200):
        resultado.total += 1
        no_banco.add(comprovante.file.name)
        caminho = comprovante.file.path
        if not os.path.isfile(caminho):
            resultado.faltando.append(comprovante.pk)
        elif _sha256(caminho) != comprovante.sha256:
            resultado.divergentes.append(comprovante.pk)

    no_disco = set()
    if os.path.isdir(pasta):
        for nome in os.listdir(pasta):
            if os.path.isfile(os.path.join(pasta, nome)):
                no_disco.add("{}/{}".format(PASTA_DOS_COMPROVANTES, nome))
    resultado.na_pasta = len(no_disco)
    resultado.orfaos = sorted(no_disco - no_banco)
    return resultado


def _ids(consulta, campo="id"):
    return list(consulta.values_list(campo, flat=True)[:50])


def conferir_banco():
    """As regras do banco, conferidas de novo. Cada item com ate 50 ids."""
    duplicadas = (
        MinisterMonthlyFee.objects.values("minister", "reference_month")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
    )
    vigentes_repetidas = (
        MinisterFeeCharge.objects.filter(superseded_at__isnull=True)
        .values("fee")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
    )
    return [
        Resultado("mensalidade sem ministro", _ids(MinisterMonthlyFee.objects.filter(minister__isnull=True))),
        Resultado(
            "mensalidade duplicada na competencia (ministro + competencia)",
            [(d["minister"], str(d["reference_month"])) for d in duplicadas[:50]],
        ),
        Resultado("cobranca sem mensalidade", _ids(MinisterFeeCharge.objects.filter(fee__isnull=True))),
        Resultado("mais de uma cobranca vigente na mesma mensalidade", [d["fee"] for d in vigentes_repetidas[:50]]),
        Resultado(
            "cobranca vigente com valor ou vencimento diferente da mensalidade pendente",
            _ids(
                MinisterFeeCharge.objects.filter(
                    superseded_at__isnull=True, fee__status=MonthlyFeeStatus.PENDING
                ).filter(~Q(amount_snapshot=F("fee__amount")) | ~Q(due_date_snapshot=F("fee__due_date")))
            ),
        ),
        Resultado("comprovante sem mensalidade", _ids(MinisterPaymentProof.objects.filter(fee__isnull=True))),
        Resultado(
            "comprovante aguardando analise de mensalidade que nao esta pendente",
            _ids(
                MinisterPaymentProof.objects.filter(status=ProofStatus.SUBMITTED).exclude(
                    fee__status=MonthlyFeeStatus.PENDING
                )
            ),
        ),
        Resultado(
            "acesso com login diferente da inscricao CONAMAD",
            _ids(MinisterAccount.objects.exclude(login_identifier=F("minister__conamad_registration"))),
        ),
    ]


def conferir_tudo():
    resultados = conferir_banco()
    arquivos = arquivos_dos_comprovantes()
    resultados += [
        Resultado("comprovante sem arquivo no disco", arquivos.faltando),
        Resultado("comprovante com SHA-256 diferente do registrado", arquivos.divergentes),
        Resultado("arquivo na pasta de comprovantes sem registro", arquivos.orfaos),
    ]
    return resultados, arquivos
