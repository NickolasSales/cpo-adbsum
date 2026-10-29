"""
Confere a consistencia do Controle de Ministros. So le.

Roda antes de abrir o portal, depois de gerar uma competencia, depois de um
restore. Termina com erro se qualquer item tiver ocorrencia — inclusive
arquivo de comprovante sem registro, que depois de um restore e normal (o tar
de media e mais novo que o dump) mas antes de um piloto nao deveria existir.
Os itens estao em ministers.conferencia.
"""

from django.core.management.base import BaseCommand, CommandError

from ministers.conferencia import conferir_tudo
from ministers.models import (
    Minister,
    MinisterAccount,
    MinisterFeeCharge,
    MinisterMonthlyFee,
    MinisterPaymentProof,
)


class Command(BaseCommand):
    help = "Confere mensalidades, cobrancas, comprovantes e acessos (somente leitura)."

    def handle(self, *args, **opcoes):
        self.stdout.write(
            "Ministros {} | acessos {} | mensalidades {} | cobrancas {} | comprovantes {}".format(
                Minister.objects.count(),
                MinisterAccount.objects.count(),
                MinisterMonthlyFee.objects.count(),
                MinisterFeeCharge.objects.count(),
                MinisterPaymentProof.objects.count(),
            )
        )
        resultados, _ = conferir_tudo()
        problemas = 0
        for resultado in resultados:
            marca = "OK " if not resultado.quantidade else "ERRO"
            detalhe = " ({})".format(", ".join(map(str, resultado.ids[:10]))) if resultado.quantidade else ""
            self.stdout.write("[{}] {}: {}{}".format(marca, resultado.descricao, resultado.quantidade, detalhe))
            problemas += resultado.quantidade
        if problemas:
            raise CommandError("{} inconsistencia(s) encontrada(s).".format(problemas))
        self.stdout.write(self.style.SUCCESS("Nenhuma inconsistencia."))
