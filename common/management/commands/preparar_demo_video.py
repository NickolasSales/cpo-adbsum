"""
Monta o cenario de demonstracao usado para gravar os videos de treinamento.

Idempotente
-----------
Rodar duas vezes deixa o banco no mesmo estado que rodar uma. Cada registro e
localizado por um identificador reservado (common.demo_video) e atualizado no
lugar; nada e criado em duplicata e nada e apagado para ser recriado.

O que ele toca, e so
--------------------
    User            os dois de @example.com, e nenhum outro
    Module          o de codigo DEMO-VIDEO, e nenhum outro
    Enrollment      as duas matriculas nesse modulo
    Exam            a prova "Avaliacao de Demonstracao" desse modulo
    Question        as quatro questoes dessa prova

Todo filtro parte de um identificador DEMO. Nao existe combinacao de opcoes
que faca este comando alcancar aluno, modulo ou prova de verdade — e ha teste
verificando exatamente isso.

A senha
-------
Vem de DEMO_VIDEO_PASSWORD. Se a variavel nao existir, o comando sorteia uma
com `secrets` e a imprime UMA vez no terminal, para ser copiada para o gerente
de senhas de quem vai gravar.

Ela nao entra no Git, nao entra no AuditLog e nao e gravada em arquivo. A
unica forma de recupera-la depois e definir a variavel e rodar de novo, o que
redefine a senha dos dois usuarios.

Reset entre gravacoes
---------------------
A prova DEMO tem max_attempts=1, entao depois da primeira gravacao os alunos
DEMO nao conseguem comecar outra. `--reset` apaga as tentativas e certificados
DELES para liberar nova gravacao — e nunca os de mais ninguem.

Exemplos
--------
    python manage.py preparar_demo_video
    python manage.py preparar_demo_video --reset
"""

import os
import secrets
import string
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import User, UserRole
from certificates.models import Certificate, CertificateTemplate, TemplateStatus
from common import demo_video as cenario
from courses.models import Enrollment, EnrollmentStatus, Module
from exams.models import Exam, ExamStatus, Question, QuestionOption
from exams.services.exam import publish_exam

# A janela da prova DEMO e propositalmente larga: o cenario precisa continuar
# gravavel daqui a meses sem alguem lembrar de reabrir a prova. Nao ha risco
# academico — so os dois usuarios DEMO tem matricula.
ANOS_DE_JANELA = 5


def _sortear_senha():
    """Senha temporaria forte para os usuarios de demonstracao."""
    alfabeto = string.ascii_letters + string.digits
    return "Demo-" + "".join(secrets.choice(alfabeto) for _ in range(16))


class Command(BaseCommand):
    help = (
        "Cria ou atualiza o cenario de demonstracao (2 alunos, modulo, prova "
        "e questoes) usado para gravar os videos de treinamento."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help=(
                "Antes de preparar, apaga tentativas e certificados dos DOIS "
                "usuarios DEMO, liberando nova gravacao."
            ),
        )

    # -- partes ------------------------------------------------------------

    def _usuarios(self, senha):
        """
        Os dois alunos DEMO, com a senha atual.

        update_or_create nao serve aqui: a senha precisa passar por
        set_password, que faz o hashing. Atribuir ao campo direto gravaria
        texto puro no banco.
        """
        for email, nome in (
            (cenario.EMAIL_APROVADO, cenario.NOME_APROVADO),
            (cenario.EMAIL_REPROVADO, cenario.NOME_REPROVADO),
        ):
            usuario = User.objects.filter(email__iexact=email).first()
            criado = usuario is None
            if criado:
                usuario = User.objects.create_user(
                    email=email,
                    full_name=nome,
                    password=senha,
                    role=UserRole.STUDENT,
                )
            else:
                usuario.full_name = nome
                usuario.role = UserRole.STUDENT
                usuario.set_password(senha)

            # Os tres do enunciado. must_change_password=False porque a troca
            # obrigatoria abriria uma tela a mais no comeco do video, que nao
            # e o que o aluno real vai encontrar.
            usuario.is_active = True
            usuario.must_change_password = False
            usuario.is_staff = False
            usuario.is_superuser = False
            usuario.save()
            yield usuario, criado

    def _modulo(self):
        """
        O modulo DEMO, com os dados que o certificado imprime.

        Os quatro campos de certificado sao preenchidos porque sem eles a
        emissao e recusada (Module.dados_do_certificado_ausentes), e o video
        do aprovado precisa mostrar o certificado saindo. Os textos dizem
        DEMONSTRACAO em letra maiuscula: se um frame do video vazar, o
        documento se identifica sozinho.
        """
        dados = {
            "name": cenario.NOME_DO_MODULO,
            "description": cenario.DESCRICAO_DO_MODULO,
            "is_active": True,
            "order": 999,
            "certificate_display_name": cenario.NOME_NO_CERTIFICADO,
            "certificate_course_dates_text": cenario.DATAS_NO_CERTIFICADO,
            "certificate_location": cenario.LOCAL_NO_CERTIFICADO,
            "certificate_workload_hours": cenario.CARGA_HORARIA,
            "certificate_year": timezone.now().year,
        }

        modulo, criado = Module.objects.get_or_create(
            code=cenario.CODIGO_DO_MODULO, defaults=dados
        )
        if not criado:
            for campo, valor in dados.items():
                setattr(modulo, campo, valor)
            modulo.save()
        return modulo, criado

    def _modelo_de_certificado(self, modulo):
        """
        Aponta o modulo DEMO para um modelo de certificado ativo.

        Aponta NO MODULO, e nunca ativa nem torna global um modelo: ativar e
        um ato de alcance institucional, que trocaria a aparencia de todo
        documento emitido dali para a frente. Aqui a mudanca cabe inteira
        dentro do modulo DEMO — resolver_template devolve o modelo escolhido
        pelo modulo antes de olhar o padrao global, e nenhum outro modulo
        passa a enxergar coisa diferente.

        Se nao houver modelo ativo, o comando nao inventa um: devolve None, e
        quem grava vai encontrar o resultado sem botao de certificado. Criar
        um layout aqui produziria um documento oficial com estetica que
        ninguem aprovou.
        """
        ativo = (
            CertificateTemplate.objects.filter(status=TemplateStatus.ACTIVE)
            .order_by("-is_global", "-version", "-pk")
            .first()
        )
        if ativo is None:
            if modulo.certificate_template_id is not None:
                modulo.certificate_template = None
                modulo.save(update_fields=["certificate_template", "updated_at"])
            return None

        if modulo.certificate_template_id != ativo.pk:
            modulo.certificate_template = ativo
            modulo.save(update_fields=["certificate_template", "updated_at"])
        return ativo

    def _matriculas(self, usuarios, modulo):
        for usuario in usuarios:
            matricula, criada = Enrollment.objects.get_or_create(
                student=usuario,
                module=modulo,
                defaults={
                    "status": EnrollmentStatus.ACTIVE,
                    "access_enabled": True,
                    "notes": "Matricula de demonstracao (videos de treinamento).",
                },
            )
            if not criada:
                matricula.status = EnrollmentStatus.ACTIVE
                matricula.access_enabled = True
                matricula.revoked_at = None
                matricula.revoked_by = None
                matricula.save()
            yield matricula, criada

    def _prova(self, modulo):
        agora = timezone.now()
        prova = Exam.objects.filter(
            module=modulo, title=cenario.TITULO_DA_PROVA
        ).first()

        valores = {
            "description": "Avaliacao ficticia usada apenas em treinamento.",
            "instructions": cenario.INSTRUCOES,
            "open_at": agora - timedelta(days=1),
            "close_at": agora + timedelta(days=365 * ANOS_DE_JANELA),
            "duration_minutes": cenario.DURACAO_EM_MINUTOS,
            "passing_score": cenario.NOTA_MINIMA,
            "max_attempts": cenario.MAXIMO_DE_TENTATIVAS,
            "show_score_after_submission": True,
            "failure_message": cenario.MENSAGEM_DE_REPROVACAO,
            "is_archived": False,
        }

        if prova is None:
            return Exam.objects.create(
                module=modulo,
                title=cenario.TITULO_DA_PROVA,
                status=ExamStatus.DRAFT,
                **valores
            ), True

        # Ja publicada: a estrutura esta congelada de proposito e o comando
        # nao a reescreve. So reabre a janela, que e o que impede uma gravacao
        # futura de esbarrar em "o periodo desta prova foi encerrado".
        prova.open_at = valores["open_at"]
        prova.close_at = valores["close_at"]
        prova.is_archived = False
        if prova.status == ExamStatus.DRAFT:
            for campo, valor in valores.items():
                setattr(prova, campo, valor)
        prova.save()
        return prova, False

    def _questoes(self, prova):
        """
        As quatro questoes. So mexe enquanto a prova esta em DRAFT.

        Depois de publicada, o proprio dominio proibe alterar a estrutura
        (exams.services.validation.exigir_estrutura_editavel), e forcar isso
        por fora seria criar um caminho que nenhum administrador tem.
        """
        if prova.status != ExamStatus.DRAFT:
            return 0

        for dados in cenario.QUESTOES:
            questao, _ = Question.objects.update_or_create(
                exam=prova,
                order=dados["ordem"],
                defaults={
                    "type": dados["tipo"],
                    "text": dados["enunciado"],
                    "points": cenario.PONTOS_POR_QUESTAO,
                    "required": True,
                    "active": True,
                },
            )
            questao.options.all().delete()
            for posicao, alternativa in enumerate(dados["alternativas"], start=1):
                QuestionOption.objects.create(
                    question=questao,
                    text=alternativa["texto"],
                    is_correct=alternativa["correta"],
                    order=posicao,
                )
        return len(cenario.QUESTOES)

    def _resetar(self, usuarios):
        """
        Apaga tentativas e certificados dos usuarios DEMO.

        O filtro e a lista de usuarios DEMO ja resolvida, e nao um campo de
        texto: nao existe erro de digitacao aqui que alcance outro aluno.

        Certificado primeiro — Certificate.attempt e PROTECT. O CASCADE de
        ExamAttempt leva as respostas junto.
        """
        from exams.models import ExamAttempt

        ids = [usuario.pk for usuario in usuarios]
        tentativas = ExamAttempt.objects.filter(student_id__in=ids)
        certificados = Certificate.objects.filter(attempt__student_id__in=ids)

        quantos = {
            "certificados": certificados.count(),
            "tentativas": tentativas.count(),
        }
        certificados.delete()
        tentativas.delete()
        return quantos

    # -- execucao ----------------------------------------------------------

    @transaction.atomic
    def handle(self, *args, **opcoes):
        escrever = self.stdout.write

        senha = os.environ.get(cenario.VARIAVEL_DA_SENHA, "").strip()
        senha_sorteada = not senha
        if senha_sorteada:
            senha = _sortear_senha()

        usuarios = []
        novos = []
        for usuario, criado in self._usuarios(senha):
            usuarios.append(usuario)
            if criado:
                novos.append(usuario.email)

        resetado = None
        if opcoes["reset"]:
            resetado = self._resetar(usuarios)

        modulo, modulo_criado = self._modulo()
        modelo = self._modelo_de_certificado(modulo)
        matriculas = list(self._matriculas(usuarios, modulo))
        prova, _ = self._prova(modulo)
        questoes = self._questoes(prova)

        publicada_agora = False
        if prova.status == ExamStatus.DRAFT:
            publish_exam(prova)
            publicada_agora = True

        # -- relatorio -----------------------------------------------------
        escrever("")
        escrever(self.style.MIGRATE_HEADING("CENARIO DE DEMONSTRACAO"))
        escrever("")
        escrever("Modulo ........................ {} - {}".format(
            modulo.code, modulo.name
        ))
        escrever("  criado agora ................ {}".format(
            "SIM" if modulo_criado else "nao (ja existia)"
        ))
        escrever("  ativo ....................... {}".format(
            "SIM" if modulo.is_active else "NAO"
        ))
        escrever("  dados do certificado ........ {}".format(
            "completos"
            if modulo.pronto_para_certificar
            else "FALTANDO: {}".format(
                ", ".join(modulo.dados_do_certificado_ausentes())
            )
        ))
        escrever("  modelo do certificado ....... {}".format(
            "id={} {!r} (so este modulo aponta para ele)".format(
                modelo.pk, modelo.name
            )
            if modelo
            else "NENHUM ATIVO - o video para antes do certificado"
        ))
        escrever("")
        escrever("Prova ......................... {}".format(prova.title))
        escrever("  status ...................... {}".format(prova.status))
        escrever("  publicada agora ............. {}".format(
            "SIM" if publicada_agora else "nao (ja estava)"
        ))
        escrever("  questoes gravadas ........... {}".format(
            questoes if questoes else "0 (estrutura ja congelada)"
        ))
        escrever("  questoes ativas ............. {}".format(
            prova.questions.filter(active=True).count()
        ))
        escrever("  total de pontos ............. {}".format(prova.total_points))
        escrever("  nota minima ................. {}".format(prova.passing_score))
        escrever("  tentativas por aluno ........ {}".format(prova.max_attempts))
        escrever("  mostra nota ao enviar ....... {}".format(
            prova.show_score_after_submission
        ))
        escrever("  janela ...................... {} ate {}".format(
            prova.open_at.strftime("%d/%m/%Y"),
            prova.close_at.strftime("%d/%m/%Y"),
        ))
        escrever("")
        escrever("Alunos de demonstracao:")
        for usuario in usuarios:
            escrever("  {:<32} ativo={} trocar_senha={}".format(
                usuario.email, usuario.is_active, usuario.must_change_password
            ))
        escrever("  criados agora ............... {}".format(
            ", ".join(novos) if novos else "nenhum (ja existiam)"
        ))
        escrever("  matriculas ativas ........... {}".format(
            sum(1 for m, _ in matriculas if m.status == EnrollmentStatus.ACTIVE)
        ))
        escrever("")

        if resetado is not None:
            escrever("Reset (somente usuarios DEMO):")
            escrever("  tentativas removidas ........ {}".format(
                resetado["tentativas"]
            ))
            escrever("  certificados removidos ...... {}".format(
                resetado["certificados"]
            ))
            escrever("")

        escrever("Nenhum aluno real enxerga este modulo: a visibilidade vem da")
        escrever("matricula, e so os dois alunos acima estao matriculados.")
        escrever("")

        if senha_sorteada:
            escrever(self.style.WARNING(
                "Senha sorteada agora (aparece uma unica vez):"
            ))
            escrever("")
            escrever("    {}".format(senha))
            escrever("")
            escrever("Guarde-a no gerente de senhas. Ela nao foi gravada em")
            escrever("arquivo nem na trilha de auditoria. Para usar uma senha")
            escrever("propria, defina {} e rode de novo.".format(
                cenario.VARIAVEL_DA_SENHA
            ))
        else:
            escrever("Senha definida a partir de {}.".format(
                cenario.VARIAVEL_DA_SENHA
            ))
        escrever("")
