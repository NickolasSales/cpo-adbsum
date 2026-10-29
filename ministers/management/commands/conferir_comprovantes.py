"""
Confere os comprovantes do banco contra os arquivos no disco. So le.

Para que serve
--------------
O comprovante tem duas metades: a linha no PostgreSQL (com o SHA-256) e o
arquivo em MEDIA_ROOT. O backup do banco e o backup de media sao arquivos
separados (cpo-db-backup e cpo-media-backup), e um restore so esta completo
quando as duas metades voltaram e batem. Este comando responde isso:

    para cada comprovante do banco   o arquivo existe? o SHA-256 confere?
    para cada arquivo da pasta       algum comprovante aponta para ele?

Arquivo sem registro (orfao) e normal quando o tar de media e mais novo que
o dump do banco: foi enviado entre os dois backups. O contrario — registro
sem arquivo, ou SHA diferente — e o que impede a secretaria de abrir o
comprovante, e faz o comando terminar com erro.

Nao altera nada: nao apaga orfao, nao corrige registro, nao grava auditoria.
Le os arquivos em blocos; nenhum vai inteiro para a memoria.
"""

import hashlib
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ministers.models import PASTA_DOS_COMPROVANTES, MinisterPaymentProof


def _sha256(caminho):
    digest = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


class Command(BaseCommand):
    help = "Confere os comprovantes de pagamento do banco contra os arquivos em MEDIA_ROOT (somente leitura)."

    def handle(self, *args, **opcoes):
        pasta = os.path.join(settings.MEDIA_ROOT, PASTA_DOS_COMPROVANTES)
        no_banco = set()
        faltando, divergentes = [], []
        total = 0

        consulta = MinisterPaymentProof.objects.only("id", "file", "sha256").order_by("id")
        for comprovante in consulta.iterator(chunk_size=200):
            total += 1
            no_banco.add(comprovante.file.name)
            caminho = comprovante.file.path
            if not os.path.isfile(caminho):
                faltando.append(comprovante.pk)
            elif _sha256(caminho) != comprovante.sha256:
                divergentes.append(comprovante.pk)

        no_disco = set()
        if os.path.isdir(pasta):
            for nome in os.listdir(pasta):
                if os.path.isfile(os.path.join(pasta, nome)):
                    no_disco.add("{}/{}".format(PASTA_DOS_COMPROVANTES, nome))
        orfaos = no_disco - no_banco

        self.stdout.write("Comprovantes no banco: {}".format(total))
        self.stdout.write("Arquivos na pasta: {}".format(len(no_disco)))
        self.stdout.write("Conferidos (arquivo presente e SHA-256 igual): {}".format(
            total - len(faltando) - len(divergentes)
        ))
        self.stdout.write("Sem arquivo no disco: {}{}".format(
            len(faltando), " (ids {})".format(", ".join(map(str, faltando))) if faltando else ""
        ))
        self.stdout.write("SHA-256 diferente: {}{}".format(
            len(divergentes), " (ids {})".format(", ".join(map(str, divergentes))) if divergentes else ""
        ))
        self.stdout.write("Arquivos sem registro (orfaos): {}".format(len(orfaos)))

        if faltando or divergentes:
            raise CommandError("Ha comprovantes sem arquivo ou com arquivo diferente do registrado.")
        self.stdout.write(self.style.SUCCESS("OK: todos os comprovantes do banco estao no disco e conferem."))
