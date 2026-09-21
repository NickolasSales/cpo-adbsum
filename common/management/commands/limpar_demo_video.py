"""
Encerra o cenario de demonstracao depois das gravacoes.

Dois encerramentos diferentes
-----------------------------
    --desativar   guarda o cenario. Usuarios DEMO com is_active=False e
                  modulo com is_active=False. Nada e apagado, e uma nova
                  gravacao volta com `preparar_demo_video`.

    --remover     apaga o cenario inteiro. Exige a frase de confirmacao.

O padrao e dry run: sem nenhuma das duas opcoes o comando apenas conta o que
existe hoje e nao altera nada.

Por que desativar e o caminho normal
------------------------------------
Sao contas de aluno com senha conhecida por quem gravou, num sistema que a
essa altura tem turma real matriculada. Deixa-las ativas indefinidamente e
manter uma porta aberta sem motivo. Desativar fecha a porta e preserva o
cenario para o proximo video.

O que nunca e apagado
---------------------
    AuditLog      A trilha e append-only. Os eventos gerados durante a
                  demonstracao ficam: eles registram que a demonstracao
                  aconteceu, e nao sao dado academico de ninguem.

    dado real     Todo filtro parte dos identificadores reservados de
                  common.demo_video. Nao existe opcao neste comando que
                  alcance aluno, modulo ou prova de verdade.

Exemplos
--------
    python manage.py limpar_demo_video
    python manage.py limpar_demo_video --desativar
    python manage.py limpar_demo_video --remover --confirm REMOVER-CENARIO-DEMO
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from certificates.models import Certificate
from common import demo_video as cenario
from courses.models import Enrollment, Module
from exams.models import Exam, ExamAttempt, Question

FRASE_DE_CONFIRMACAO = "REMOVER-CENARIO-DEMO"


class Command(BaseCommand):
    help = (
        "Desativa ou remove o cenario de demonstracao dos videos de "
        "treinamento. Dry run por padrao."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--desativar",
            action="store_true",
            help=(
                "Desativa os usuarios DEMO e o modulo DEMO, preservando o "
                "cenario para gravacoes futuras."
            ),
        )
        parser.add_argument(
            "--remover",
            action="store_true",
            help="Apaga o cenario inteiro. Exige --confirm.",
        )
        parser.add_argument(
            "--confirm",
            default="",
            help="Frase de confirmacao, obrigatoria com --remover.",
        )

    # -- leitura -----------------------------------------------------------

    def _levantar(self):
        """O cenario como ele esta agora. Nao altera nada."""
        usuarios = list(User.objects.filter(email__in=cenario.EMAILS))
        ids = [usuario.pk for usuario in usuarios]

        modulo = Module.objects.filter(code=cenario.CODIGO_DO_MODULO).first()
        provas = (
            Exam.objects.filter(module=modulo) if modulo else Exam.objects.none()
        )

        return {
            "usuarios": usuarios,
            "modulo": modulo,
            "provas": provas,
            "matriculas": Enrollment.objects.filter(student_id__in=ids),
            "tentativas": ExamAttempt.objects.filter(student_id__in=ids),
            "certificados": Certificate.objects.filter(
                attempt__student_id__in=ids
            ),
            "questoes": Question.objects.filter(exam__in=provas),
        }

    def _relatar(self, estado, titulo):
        escrever = self.stdout.write
        modulo = estado["modulo"]

        escrever("")
        escrever(self.style.MIGRATE_HEADING(titulo))
        escrever("")
        escrever("Usuarios DEMO ................. {}".format(
            len(estado["usuarios"])
        ))
        for usuario in estado["usuarios"]:
            escrever("  {:<32} ativo={}".format(usuario.email, usuario.is_active))
        escrever("Modulo DEMO ................... {}".format(
            "1 (ativo={})".format(modulo.is_active) if modulo else "0"
        ))
        escrever("Prova DEMO .................... {}".format(
            estado["provas"].count()
        ))
        escrever("Questoes DEMO ................. {}".format(
            estado["questoes"].count()
        ))
        escrever("Matriculas DEMO ............... {}".format(
            estado["matriculas"].count()
        ))
        escrever("Tentativas DEMO ............... {}".format(
            estado["tentativas"].count()
        ))
        escrever("Certificados DEMO ............. {}".format(
            estado["certificados"].count()
        ))
        escrever("")
        escrever("Preservado .................... AuditLog e todo dado real")
        escrever("")

    # -- escrita -----------------------------------------------------------

    def _desativar(self, estado):
        for usuario in estado["usuarios"]:
            usuario.is_active = False
            usuario.save(update_fields=["is_active"])

        modulo = estado["modulo"]
        if modulo is not None:
            modulo.is_active = False
            modulo.save(update_fields=["is_active", "updated_at"])

    def _remover(self, estado):
        """
        Apaga na ordem que as chaves estrangeiras exigem.

        Certificate antes de ExamAttempt (PROTECT). ExamAttempt antes de
        Exam, porque a tentativa aponta para a prova com PROTECT. O resto
        cai por CASCADE.
        """
        estado["certificados"].delete()
        estado["tentativas"].delete()
        estado["matriculas"].delete()
        estado["provas"].delete()
        if estado["modulo"] is not None:
            estado["modulo"].delete()
        for usuario in estado["usuarios"]:
            usuario.delete()

    # -- execucao ----------------------------------------------------------

    @transaction.atomic
    def handle(self, *args, **opcoes):
        if opcoes["desativar"] and opcoes["remover"]:
            raise CommandError("Escolha --desativar ou --remover, nao os dois.")

        if opcoes["remover"] and opcoes["confirm"] != FRASE_DE_CONFIRMACAO:
            raise CommandError(
                "Para remover, repita exatamente: --confirm {}".format(
                    FRASE_DE_CONFIRMACAO
                )
            )

        estado = self._levantar()

        if not opcoes["desativar"] and not opcoes["remover"]:
            self._relatar(estado, "DRY RUN (nada foi alterado)")
            self.stdout.write(
                "Para guardar o cenario: --desativar. "
                "Para apaga-lo: --remover --confirm {}.".format(
                    FRASE_DE_CONFIRMACAO
                )
            )
            self.stdout.write("")
            return

        if opcoes["desativar"]:
            self._desativar(estado)
            self._relatar(self._levantar(), "CENARIO DESATIVADO")
            self.stdout.write(
                "O cenario continua no banco. Para gravar de novo: "
                "manage.py preparar_demo_video --reset"
            )
            self.stdout.write("")
            return

        self._remover(estado)
        self._relatar(self._levantar(), "CENARIO REMOVIDO")
        self.stdout.write(self.style.SUCCESS("Remocao concluida."))
        self.stdout.write("")
