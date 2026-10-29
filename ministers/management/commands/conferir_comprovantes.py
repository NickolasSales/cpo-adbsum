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
Le os arquivos em blocos; nenhum vai inteiro para a memoria. A conferencia
completa do Controle de Ministros (banco + arquivos) e conferir_ministros.
"""

from django.core.management.base import BaseCommand, CommandError

from ministers.conferencia import arquivos_dos_comprovantes


def _ids(ids):
    return " (ids {})".format(", ".join(map(str, ids))) if ids else ""


class Command(BaseCommand):
    help = "Confere os comprovantes de pagamento do banco contra os arquivos em MEDIA_ROOT (somente leitura)."

    def handle(self, *args, **opcoes):
        r = arquivos_dos_comprovantes()
        self.stdout.write("Comprovantes no banco: {}".format(r.total))
        self.stdout.write("Arquivos na pasta: {}".format(r.na_pasta))
        self.stdout.write("Conferidos (arquivo presente e SHA-256 igual): {}".format(r.conferidos))
        self.stdout.write("Sem arquivo no disco: {}{}".format(len(r.faltando), _ids(r.faltando)))
        self.stdout.write("SHA-256 diferente: {}{}".format(len(r.divergentes), _ids(r.divergentes)))
        self.stdout.write("Arquivos sem registro (orfaos): {}".format(len(r.orfaos)))

        if r.faltando or r.divergentes:
            raise CommandError("Ha comprovantes sem arquivo ou com arquivo diferente do registrado.")
        self.stdout.write(self.style.SUCCESS("OK: todos os comprovantes do banco estao no disco e conferem."))
