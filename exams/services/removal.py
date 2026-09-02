"""
Retirar uma prova da operacao: excluir ou arquivar.

Sao duas operacoes diferentes, e a diferenca e a unica coisa que importa
aqui.

    EXCLUIR    apaga a linha. So e permitido quando NAO EXISTE historico
               academico nem descendencia. Nao ha desfazer.

    ARQUIVAR   preserva tudo e tira a prova da visao operacional. E o que
               sobra quando ja existe historico — porque historico academico
               nao se apaga para arrumar uma tela.

A escolha nao e do administrador: e do estado da prova. A interface mostra a
acao possivel, e este modulo recusa a impossivel mesmo que o POST chegue
montado a mao. Esconder o botao nunca foi a protecao.

Por que a exclusao trava a prova
--------------------------------
A corrida obvia e esta:

    can_delete_exam diz "0 tentativas"
        aluno inicia uma tentativa
            DELETE apaga a prova de baixo da tentativa

O que fecha a janela e um detalhe do PostgreSQL: inserir uma linha com chave
estrangeira para a prova toma um lock FOR KEY SHARE na linha referenciada, e
SELECT ... FOR UPDATE conflita com ele. Travar a prova e reconferir DEPOIS de
adquirir o lock e, portanto, suficiente: ou o start entra antes e a recontagem
o enxerga, ou ele espera o fim desta transacao e encontra a prova ja apagada.

O PROTECT da FK continua sendo a defesa final, a que nao depende de ninguem
lembrar de abrir a transacao certa.
"""

from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record
from common.exceptions import DomainError
from common.texto import validar_motivo as validar_motivo_generico
from exams.models import AttemptStatus, Exam, ExamAttempt


class ProvaJaArquivada(DomainError):
    """A prova ja esta arquivada; nada a fazer."""


class ProvaNaoArquivada(DomainError):
    """A prova nao esta arquivada; nada a desarquivar."""


def validar_motivo(motivo):
    return validar_motivo_generico(
        motivo, vazio="Informe o motivo do arquivamento."
    )


# ---------------------------------------------------------------------------
# Exclusao fisica
# ---------------------------------------------------------------------------


def can_delete_exam(exam):
    """
    Impedimentos para apagar a prova. Lista vazia significa que pode.

    Devolve motivos legiveis, e nao um booleano, porque a tela precisa
    explicar. "Nao e possivel excluir" sem dizer o porque leva o administrador
    a tentar de novo, ou a achar que o sistema esta quebrado.

    O que impede:

        qualquer ExamAttempt, em qualquer situacao
            Inclusive IN_PROGRESS, EXPIRED e RESET. Uma tentativa anulada
            continua sendo o registro de que aquele aluno fez aquela prova
            naquele dia, e e justamente o registro que justifica a anulacao.

        qualquer certificado
            Na pratica redundante — Certificate.attempt e OneToOne com
            PROTECT, entao certificado sem tentativa nao existe. Fica
            explicito porque o dia em que esse desenho mudar, esta funcao
            precisa continuar certa.

        descendentes por parent_exam ou por root_exam
            Apagar a v1 com a v2 viva deixaria a linhagem sem comeco e
            zeraria a procedencia de quem veio depois.

    O status NAO impede. Uma prova publicada ou fechada que nunca foi usada
    por ninguem e uma prova que nao aconteceu, e obrigar arquivo eterno so
    porque alguem clicou em publicar seria transformar um clique em cicatriz
    permanente.
    """
    from certificates.models import Certificate

    impedimentos = []

    tentativas = ExamAttempt.objects.filter(exam=exam).count()
    if tentativas:
        impedimentos.append(
            "Esta prova possui {} tentativa(s) de aluno. O historico academico "
            "nao pode ser apagado.".format(tentativas)
        )

    certificados = Certificate.objects.filter(attempt__exam=exam).count()
    if certificados:
        impedimentos.append(
            "Esta prova gerou {} certificado(s).".format(certificados)
        )

    derivadas = Exam.objects.filter(parent_exam=exam).count()
    if derivadas:
        impedimentos.append(
            "Existem {} versao(oes) duplicadas a partir desta prova.".format(
                derivadas
            )
        )

    # Consulta separada da anterior: uma prova pode ser raiz de versoes que
    # nao derivam dela diretamente (a v3 nasce da v2 e aponta a raiz para a
    # v1). Contar so parent_exam deixaria a raiz apagavel com netos vivos.
    versoes = Exam.objects.filter(root_exam=exam).count()
    if versoes:
        impedimentos.append(
            "Esta prova e a raiz de uma linhagem com {} versao(oes).".format(
                versoes
            )
        )

    return impedimentos


def delete_exam(exam, *, actor=None, request=None):
    """
    Apaga a prova fisicamente, se nenhuma dependencia impedir.

    Questoes e alternativas vao junto por CASCADE — elas sao a prova, e nao
    historico de aluno. O que jamais e apagado por aqui e tentativa, resposta
    ou certificado: se existirem, a operacao nem comeca.

    O evento entra na trilha ANTES do DELETE, dentro da mesma transacao. Se a
    exclusao falhar por qualquer motivo, o rollback leva o evento junto e a
    trilha nunca afirma uma exclusao que nao aconteceu.
    """
    with transaction.atomic():
        travada = (
            Exam.objects.select_for_update()
            .select_related("module")
            .get(pk=exam.pk)
        )

        # Reconferido DEPOIS do lock, e nao antes. Ver a docstring do modulo.
        impedimentos = can_delete_exam(travada)
        if impedimentos:
            raise DomainError(impedimentos)

        record(
            AuditEvent.EXAM_DELETED,
            request=request,
            actor=actor,
            entity_type="Exam",
            entity_id=travada.pk,
            # Titulo, versao e modulo respondem "o que foi apagado". Questoes,
            # alternativas e gabarito ficam de fora: a trilha registra o ato,
            # e nao e lugar de guardar copia do conteudo da prova.
            metadata={
                "title": travada.title,
                "version": travada.version,
                "status": travada.status,
                "module_id": travada.module_id,
                "module_code": travada.module.code,
            },
        )

        travada.delete()

    return True


# ---------------------------------------------------------------------------
# Arquivamento
# ---------------------------------------------------------------------------


def tentativas_em_andamento(exam):
    return ExamAttempt.objects.filter(
        exam=exam, status=AttemptStatus.IN_PROGRESS
    ).count()


def archive_exam(exam, *, actor=None, reason="", request=None):
    """
    Retira a prova da operacao preservando tudo.

    Efeito imediato: a prova some da lista padrao, some da area do aluno e
    para de aceitar novas tentativas — mesmo continuando PUBLISHED. O status
    historico nao e sobrescrito, porque ele conta o que a prova foi, e
    arquivar nao muda o passado.

    Recusa enquanto houver tentativa IN_PROGRESS. Arquivar no meio de uma
    prova derrubaria o aluno em silencio, no unico momento em que ele nao tem
    como perguntar o que aconteceu. O administrador precisa antes finalizar,
    expirar ou resetar essas tentativas — tres acoes que ele ja tem, e que
    deixam registro de quem decidiu.
    """
    motivo = validar_motivo(reason)
    agora = timezone.now()

    with transaction.atomic():
        travada = (
            Exam.objects.select_for_update()
            .select_related("module")
            .get(pk=exam.pk)
        )

        if travada.is_archived:
            # Nao e sucesso silencioso: quem clicou duas vezes precisa saber
            # que a segunda nao fez nada. A view transforma isto em 409.
            raise ProvaJaArquivada("Esta prova ja esta arquivada.")

        abertas = tentativas_em_andamento(travada)
        if abertas:
            raise DomainError(
                "Esta prova possui {} tentativa(s) em andamento. Finalize, "
                "expire ou resete essas tentativas antes de arquivar.".format(
                    abertas
                )
            )

        travada.is_archived = True
        travada.archived_at = agora
        travada.archived_by = actor if getattr(actor, "pk", None) else None
        travada.archive_reason = motivo
        travada.save(
            update_fields=[
                "is_archived",
                "archived_at",
                "archived_by",
                "archive_reason",
                "updated_at",
            ]
        )

        record(
            AuditEvent.EXAM_ARCHIVED,
            request=request,
            actor=actor,
            entity_type="Exam",
            entity_id=travada.pk,
            # O motivo NAO entra na metadata: ja esta em Exam.archive_reason,
            # e duplicar texto livre cria duas versoes do mesmo fato para
            # divergirem depois. Mesmo criterio do reset de tentativa.
            metadata={
                "title": travada.title,
                "version": travada.version,
                "status": travada.status,
                "module_code": travada.module.code,
            },
        )

    return travada


def unarchive_exam(exam, *, actor=None, request=None):
    """
    Devolve a prova a operacao.

    Nao estava no pedido da Etapa 9, que so descreveu o caminho de ida. Foi
    incluida porque sem ela um arquivamento por engano vira um beco sem saida:
    a prova sai da lista e so o Django Admin a traz de volta. E o mesmo
    raciocinio de restore_revoked_enrollment, que o pedido descreve para
    matricula.

    Nao reabre nada sozinha: a prova volta com o status que sempre teve, e se
    a janela ja passou ela continua sem aceitar tentativa.
    """
    with transaction.atomic():
        travada = (
            Exam.objects.select_for_update()
            .select_related("module")
            .get(pk=exam.pk)
        )

        if not travada.is_archived:
            raise ProvaNaoArquivada("Esta prova nao esta arquivada.")

        travada.is_archived = False
        travada.archived_at = None
        travada.archived_by = None
        travada.archive_reason = ""
        travada.save(
            update_fields=[
                "is_archived",
                "archived_at",
                "archived_by",
                "archive_reason",
                "updated_at",
            ]
        )

        record(
            AuditEvent.EXAM_UNARCHIVED,
            request=request,
            actor=actor,
            entity_type="Exam",
            entity_id=travada.pk,
            metadata={
                "title": travada.title,
                "version": travada.version,
                "module_code": travada.module.code,
            },
        )

    return travada
