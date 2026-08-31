"""
Encerra tentativas cujo prazo venceu e que ninguem mais abriu.

Por que este comando existe
---------------------------
A expiracao ja acontece sozinha no proximo request do aluno: abrir a tela da
prova ou tentar salvar uma resposta depois do prazo encerra a tentativa na
hora. O que sobra sao as tentativas orfas — a aba que o aluno fechou, o
notebook que dormiu, quem simplesmente nunca mais voltou. Sem este comando
elas ficariam IN_PROGRESS para sempre, e a constraint parcial
uniq_tentativa_em_andamento impediria o aluno de comecar outra.

Ele nao tem regra propria. Chama exams.services.attempt.expire_attempt, a
mesma funcao que o acesso web usa. Uma segunda implementacao da expiracao
seria a forma mais rapida de as duas discordarem sobre o que EXPIRED
significa.

Idempotente: rodar duas vezes seguidas encerra 3 e depois 0, porque a
primeira execucao tirou todas de IN_PROGRESS. Pode rodar a cada dois minutos
sem acumular efeito nem duplicar auditoria.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from exams.models import ExamAttempt
from exams.services.attempt import expirar_tentativas_vencidas


class Command(BaseCommand):
    help = "Encerra tentativas em andamento cujo prazo ja passou."

    def add_arguments(self, parser):
        parser.add_argument(
            "--lote",
            type=int,
            default=100,
            help=(
                "Quantas tentativas carregar por vez. Existe para nao trazer "
                "uma turma inteira para a memoria de uma so vez."
            ),
        )
        parser.add_argument(
            "--limite",
            type=int,
            default=None,
            help="Para depois de encerrar esta quantidade. Sem limite por padrao.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Apenas informa quantas seriam encerradas, sem alterar nada.",
        )

    def handle(self, *args, **opcoes):
        lote = opcoes["lote"]
        limite = opcoes["limite"]

        if lote < 1:
            raise CommandError("O lote precisa ser de pelo menos 1.")
        if limite is not None and limite < 1:
            raise CommandError("O limite, quando informado, precisa ser positivo.")

        agora = timezone.now()

        if opcoes["dry_run"]:
            pendentes = ExamAttempt.objects.vencidas(agora).count()
            self.stdout.write(
                "Simulacao: {} tentativa(s) seriam encerradas.".format(pendentes)
            )
            return

        total = expirar_tentativas_vencidas(agora=agora, lote=lote, limite=limite)

        if total:
            self.stdout.write(
                self.style.SUCCESS("{} tentativa(s) encerradas.".format(total))
            )
        else:
            self.stdout.write("Nenhuma tentativa vencida.")
