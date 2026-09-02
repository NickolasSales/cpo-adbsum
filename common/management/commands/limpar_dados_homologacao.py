"""
Remove os dados academicos criados durante a homologacao.

Excecao declarada, e nao um caminho normal
------------------------------------------
Todo o resto do sistema preserva historico academico. Resetar tentativa nao
apaga: marca RESET. Revogar matricula nao apaga: marca REVOKED. Revogar
certificado nao apaga: marca REVOKED. Excluir prova so e permitido quando
nunca existiu historico.

Este comando faz o contrario: apaga tentativas, respostas e certificados de
verdade. Ele existe porque antes do piloto ha dados que nunca foram reais —
provas de teste feitas pelo proprio administrador, para verificar se o sistema
funcionava. Guardar isso como historico academico seria guardar mentira.

Por isso ele nao esta na interface, nao tem rota, e exige quatro coisas para
apagar qualquer linha: filtro especifico, --execute, a frase de confirmacao
exata e uma transacao que ou vai inteira ou nao vai.

O que ele NAO apaga, em nenhuma circunstancia
---------------------------------------------
    AuditLog     A trilha e append-only. Os eventos de teste ficam como
                 evidencia de que os testes aconteceram — inclusive o evento
                 desta propria limpeza.

    o aluno      O pedido e limpar as tentativas dele, e nao o cadastro.
    o modulo
    a prova      Depois da limpeza a prova pode passar a atender as regras de
                 exclusao, e ai o administrador a remove pela interface — que
                 e o caminho auditado, com confirmacao e verificacao de
                 dependencia.

Ordem de remocao
----------------
    Certificate           PROTECT sobre a tentativa: sai primeiro
    ExamAttempt           CASCADE cuida do resto:
        AttemptQuestion       -> AttemptOption -> AnswerOption
                              -> Answer        -> AnswerOption

Nota e resultado nao sao tabela: moram em ExamAttempt (final_score, result,
obtained_points). Apagar a tentativa e o que faz a linha sumir da tela de
Notas — nao existe registro de nota sobrevivente.

Exemplos
--------
    # Dry run, o padrao. Nao altera nada.
    python manage.py limpar_dados_homologacao \\
        --student-email aluno@exemplo.com \\
        --module-code MOD1-2026

    # Execucao real.
    python manage.py limpar_dados_homologacao \\
        --student-email aluno@exemplo.com \\
        --module-code MOD1-2026 \\
        --exam-title "Avaliacao MOD 1 Ano 2026 (Teste1)" \\
        --execute \\
        --confirm "APAGAR-DADOS-DE-HOMOLOGACAO"
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from audit.models import AuditEvent
from audit.services import record
from courses.models import Enrollment, EnrollmentStatus, Module
from exams.models import (
    Answer,
    AnswerOption,
    AttemptOption,
    AttemptQuestion,
    ExamAttempt,
)

# A frase precisa ser digitada exatamente assim. Nao e senha: e uma barreira
# contra o comando executado por engano, colado de um historico de shell ou
# disparado por um script que ninguem leu ate o fim.
FRASE_DE_CONFIRMACAO = "APAGAR-DADOS-DE-HOMOLOGACAO"


class Command(BaseCommand):
    help = (
        "Remove tentativas, respostas e certificados de homologacao de um "
        "aluno especifico. Dry run por padrao."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--student-email",
            required=True,
            help=(
                "E-mail exato do aluno alvo. Obrigatorio: e o filtro que "
                "impede a limpeza de atingir a turma inteira."
            ),
        )
        parser.add_argument(
            "--module-code",
            default=None,
            help="Codigo do modulo. Combinado com --exam-title, restringe mais.",
        )
        parser.add_argument(
            "--exam-title",
            default=None,
            help="Titulo exato da prova.",
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            help=(
                "Executa de verdade. Sem esta opcao o comando apenas informa "
                "o que seria removido."
            ),
        )
        parser.add_argument(
            "--confirm",
            default="",
            help=(
                "Frase de confirmacao, obrigatoria com --execute. "
                "Precisa ser exatamente: {}".format(FRASE_DE_CONFIRMACAO)
            ),
        )
        parser.add_argument(
            "--reactivate-enrollment",
            action="store_true",
            help=(
                "Devolve a matricula a ACTIVE se ela estiver COMPLETED apenas "
                "por causa do certificado removido. Nunca acontece sozinho."
            ),
        )

    # -- validacao dos argumentos -------------------------------------------

    def _validar(self, opcoes):
        """
        Recusa antes de tocar no banco.

        A regra do filtro nao e burocracia: `--execute --confirm FRASE` sem
        alvo apagaria o historico academico da instituicao inteira, e o
        comando teria funcionado exatamente como pedido.
        """
        email = (opcoes["student_email"] or "").strip()
        if not email:
            raise CommandError("Informe --student-email.")

        codigo = (opcoes["module_code"] or "").strip()
        titulo = (opcoes["exam_title"] or "").strip()
        if not codigo and not titulo:
            raise CommandError(
                "Filtro insuficiente. Informe --module-code ou --exam-title "
                "junto com --student-email."
            )

        if opcoes["execute"] and opcoes["confirm"] != FRASE_DE_CONFIRMACAO:
            raise CommandError(
                'Para executar, repita exatamente: --confirm "{}"'.format(
                    FRASE_DE_CONFIRMACAO
                )
            )

        return email, codigo, titulo

    def _alvo(self, email, codigo, titulo):
        """Resolve aluno, modulo e o conjunto de tentativas alvo."""
        User = get_user_model()

        aluno = User.objects.filter(email__iexact=email).first()
        if aluno is None:
            raise CommandError("Nenhum usuario com o e-mail {}.".format(email))

        modulo = None
        if codigo:
            modulo = Module.objects.filter(code__iexact=codigo).first()
            if modulo is None:
                raise CommandError("Nenhum modulo com o codigo {}.".format(codigo))

        # student= sempre. Os outros dois estreitam, e nunca alargam: nao
        # existe combinacao de opcoes que faca este conjunto incluir a
        # tentativa de outro aluno.
        tentativas = ExamAttempt.objects.filter(student=aluno)
        if modulo is not None:
            tentativas = tentativas.filter(exam__module=modulo)
        if titulo:
            tentativas = tentativas.filter(exam__title=titulo)

        return aluno, modulo, tentativas

    # -- contagem ------------------------------------------------------------

    def _contar(self, tentativas):
        """
        Quantas linhas cada tabela perderia.

        Contado a partir do conjunto alvo, e nao estimado. O relatorio precisa
        dizer numeros que sao verdade agora, e nao os de uma execucao anterior.
        """
        from certificates.models import Certificate

        ids = list(tentativas.values_list("pk", flat=True))
        questoes = AttemptQuestion.objects.filter(attempt_id__in=ids)
        ids_de_questoes = list(questoes.values_list("pk", flat=True))
        respostas = Answer.objects.filter(attempt_question_id__in=ids_de_questoes)

        return {
            "tentativas": len(ids),
            "questoes_da_tentativa": questoes.count(),
            "alternativas_da_tentativa": AttemptOption.objects.filter(
                attempt_question_id__in=ids_de_questoes
            ).count(),
            "respostas": respostas.count(),
            "marcacoes": AnswerOption.objects.filter(
                answer_id__in=list(respostas.values_list("pk", flat=True))
            ).count(),
            "certificados": Certificate.objects.filter(attempt_id__in=ids).count(),
            "ids": ids,
        }

    def _matricula_para_reativar(self, aluno, modulo, contagem):
        """
        A matricula que voltaria a ACTIVE, ou None.

        Tres condicoes, todas necessarias:

            a matricula existe e esta COMPLETED
            a limpeza remove pelo menos um certificado
            nao resta outro certificado ACTIVE fora do conjunto alvo

        A terceira e a que importa. Se o aluno tiver um segundo certificado
        valido naquele modulo, ele continua com comprovacao de conclusao, e
        reabrir o modulo contradiria o documento que ele tem em maos.

        Sem --module-code nao ha modulo para avaliar, e a funcao devolve None:
        adivinhar o modulo a partir do titulo da prova seria decidir sobre
        acesso academico por heuristica.
        """
        from certificates.models import Certificate, CertificateStatus

        if modulo is None:
            return None
        if not contagem["certificados"]:
            return None

        matricula = Enrollment.objects.filter(student=aluno, module=modulo).first()
        if matricula is None or matricula.status != EnrollmentStatus.COMPLETED:
            return None

        resta = (
            Certificate.objects.filter(
                attempt__student=aluno,
                attempt__exam__module=modulo,
                status=CertificateStatus.ACTIVE,
            )
            .exclude(attempt_id__in=contagem["ids"])
            .exists()
        )
        if resta:
            return None

        return matricula

    # -- relatorio -----------------------------------------------------------

    def _relatar(self, *, aluno, modulo, titulo, contagem, matricula, executar):
        escrever = self.stdout.write

        escrever("")
        escrever(self.style.MIGRATE_HEADING(
            "EXECUCAO REAL" if executar else "DRY RUN (nada foi alterado)"
        ))
        escrever("")
        escrever("Aluno ......................... {} <{}>".format(
            aluno.full_name, aluno.email
        ))
        escrever("Modulo ........................ {}".format(
            "{} - {}".format(modulo.code, modulo.name) if modulo else "(todos)"
        ))
        escrever("Prova ......................... {}".format(titulo or "(todas)"))
        escrever("")
        escrever("Tentativas .................... {}".format(contagem["tentativas"]))
        escrever("Questoes da tentativa ......... {}".format(
            contagem["questoes_da_tentativa"]
        ))
        escrever("Alternativas da tentativa ..... {}".format(
            contagem["alternativas_da_tentativa"]
        ))
        escrever("Respostas ..................... {}".format(contagem["respostas"]))
        escrever("Marcacoes ..................... {}".format(contagem["marcacoes"]))
        escrever("Certificados .................. {}".format(
            contagem["certificados"]
        ))
        escrever("")
        # A nota nao e uma tabela. Dizer isso aqui evita a pergunta obvia de
        # quem le o relatorio e nao encontra a linha "notas".
        escrever(
            "Nota e resultado ficam na propria tentativa: apagar a tentativa "
            "remove a linha da tela de Notas."
        )
        escrever("Matricula sera reativada ...... {}".format(
            "SIM ({})".format(matricula.module.code) if matricula else "NAO"
        ))
        escrever("")
        escrever("Preservados ................... AuditLog, aluno, modulo, prova")
        escrever("")

    # -- execucao ------------------------------------------------------------

    def handle(self, *args, **opcoes):
        email, codigo, titulo = self._validar(opcoes)
        aluno, modulo, tentativas = self._alvo(email, codigo, titulo)

        contagem = self._contar(tentativas)
        matricula = None
        if opcoes["reactivate_enrollment"]:
            matricula = self._matricula_para_reativar(aluno, modulo, contagem)

        if not opcoes["execute"]:
            self._relatar(
                aluno=aluno,
                modulo=modulo,
                titulo=titulo,
                contagem=contagem,
                matricula=matricula,
                executar=False,
            )
            self.stdout.write(
                "Para executar, repita o comando com --execute e "
                '--confirm "{}".'.format(FRASE_DE_CONFIRMACAO)
            )
            return

        if not contagem["tentativas"]:
            self._relatar(
                aluno=aluno,
                modulo=modulo,
                titulo=titulo,
                contagem=contagem,
                matricula=matricula,
                executar=True,
            )
            self.stdout.write("Nada a remover.")
            return

        self._executar(
            aluno=aluno,
            modulo=modulo,
            titulo=titulo,
            contagem=contagem,
            matricula=matricula,
        )

        self._relatar(
            aluno=aluno,
            modulo=modulo,
            titulo=titulo,
            contagem=contagem,
            matricula=matricula,
            executar=True,
        )
        self.stdout.write(self.style.SUCCESS("Limpeza concluida."))

    def _executar(self, *, aluno, modulo, titulo, contagem, matricula):
        """
        A remocao, em uma transacao so.

        Se qualquer passo falhar, nada e removido — inclusive o evento de
        auditoria, que e gravado aqui dentro. A alternativa seria uma trilha
        afirmando uma limpeza que o banco desfez.
        """
        from certificates.models import Certificate

        ids = contagem["ids"]

        with transaction.atomic():
            # Certificate.attempt e PROTECT: sem apagar o certificado
            # primeiro, o DELETE da tentativa falharia.
            Certificate.objects.filter(attempt_id__in=ids).delete()

            # O CASCADE de AttemptQuestion, AttemptOption, Answer e
            # AnswerOption cuida do resto. Apagar essas tabelas na mao
            # arriscaria deixar orfa alguma linha que o desenho ja resolve.
            ExamAttempt.objects.filter(pk__in=ids).delete()

            if matricula is not None:
                travada = (
                    Enrollment.objects.select_for_update()
                    .select_related("module")
                    .get(pk=matricula.pk)
                )
                travada.status = EnrollmentStatus.ACTIVE
                travada.access_enabled = True
                travada.save(
                    update_fields=["status", "access_enabled", "updated_at"]
                )

            # actor fica nulo: um comando de gestao nao tem sessao web, e
            # atribuir a acao a um usuario qualquer seria registrar um autor
            # que nao clicou em nada.
            #
            # O e-mail nao entra na metadata — student_id ja identifica quem
            # foi, e a trilha nao precisa de uma copia a mais de dado pessoal.
            record(
                AuditEvent.HOMOLOGATION_DATA_PURGED,
                actor=None,
                student=aluno,
                entity_type="User",
                entity_id=aluno.pk,
                metadata={
                    "student_id": aluno.pk,
                    "module_code": modulo.code if modulo else None,
                    "exam_title": titulo or None,
                    "attempts_removed": contagem["tentativas"],
                    "answers_removed": contagem["respostas"],
                    "certificates_removed": contagem["certificados"],
                    "enrollment_reactivated": matricula is not None,
                },
            )
