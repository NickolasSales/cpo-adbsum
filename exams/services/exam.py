"""
Ciclo de vida da prova: criar, editar, publicar, fechar, duplicar e senha.

Nenhuma view altera Exam diretamente. Em especial, `status` nunca chega por
formulario: publicar e fechar sao operacoes proprias, que validam antes de
mudar o estado. Um campo `status` editavel deixaria a transicao a um POST
forjado.
"""

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record
from common.exceptions import DomainError, campos_alterados
from exams.models import (
    Exam,
    ExamStatus,
    MENSAGEM_REPROVACAO_PADRAO,
    NOTA_MAXIMA,
    Question,
    QuestionOption,
)
from exams.services.validation import erros_para_publicacao

# Campos que o administrador configura pela tela de prova, com o valor usado
# quando nao vem nada. Listados uma vez e reutilizados na criacao, na edicao e
# na duplicacao, para que os tres caminhos nunca divirjam sobre o que compoe a
# configuracao de uma prova.
#
# Os padroes importam: description, instructions e failure_message sao
# blank=True mas nao aceitam NULL, entao deixar um deles chegar como None
# quebraria a gravacao no banco em vez de virar string vazia.
PADROES_CONFIGURAVEIS = {
    "title": "",
    "description": "",
    "instructions": "",
    "open_at": None,
    "close_at": None,
    "duration_minutes": None,
    "passing_score": Decimal("8.00"),
    "max_attempts": 1,
    "failure_message": MENSAGEM_REPROVACAO_PADRAO,
    "randomize_questions": False,
    "randomize_options": False,
    "show_score_after_submission": True,
}

CAMPOS_CONFIGURAVEIS = tuple(PADROES_CONFIGURAVEIS)


def _normalizar_configuracao(dados):
    """
    Preenche o que nao veio com o padrao do campo.

    Um valor explicito, inclusive False ou string vazia, sempre vence o
    padrao; apenas a ausencia e o None sao substituidos.
    """
    resultado = {}
    for campo, padrao in PADROES_CONFIGURAVEIS.items():
        valor = dados.get(campo)
        resultado[campo] = padrao if valor is None else valor
    return resultado


def _validar_configuracao(dados):
    """
    Checagens que valem em qualquer estado, inclusive rascunho.

    Sao poucas de proposito: enquanto a prova e rascunho, configuracao
    incompleta e permitida. O conjunto completo de exigencias mora em
    erros_para_publicacao e so e cobrado na hora de publicar.
    """
    titulo = (dados.get("title") or "").strip()
    if not titulo:
        raise DomainError("O titulo da prova e obrigatorio.")
    dados["title"] = titulo

    nota = dados.get("passing_score")
    if nota is None:
        raise DomainError("A nota minima e obrigatoria.")
    if not (Decimal("0") <= Decimal(nota) <= NOTA_MAXIMA):
        raise DomainError("A nota minima precisa estar entre 0 e 10.")

    tentativas = dados.get("max_attempts")
    if tentativas is None or tentativas < 1:
        raise DomainError("A prova precisa permitir ao menos uma tentativa.")

    duracao = dados.get("duration_minutes")
    if duracao is not None and duracao <= 0:
        raise DomainError("A duracao precisa ser maior que zero minutos.")

    abertura = dados.get("open_at")
    encerramento = dados.get("close_at")
    if abertura and encerramento and abertura >= encerramento:
        raise DomainError("A data de encerramento deve ser posterior a abertura.")

    return dados


def _validar_modulo(module):
    if module is None:
        raise DomainError("Selecione um modulo.")
    if not module.is_active:
        raise DomainError(
            "O modulo {} esta inativo e nao aceita novas provas.".format(module.code)
        )
    return module


@transaction.atomic
def create_exam(*, module, actor=None, request=None, **dados):
    """
    Cria uma prova em rascunho.

    Nasce sempre como DRAFT, versao 1 e raiz da propria linhagem. Nenhum
    desses tres valores vem do formulario.
    """
    _validar_modulo(module)
    dados = _validar_configuracao(_normalizar_configuracao(dados))

    if not (dados.get("failure_message") or "").strip():
        dados["failure_message"] = MENSAGEM_REPROVACAO_PADRAO

    exam = Exam.objects.create(
        module=module,
        status=ExamStatus.DRAFT,
        version=1,
        root_exam=None,
        parent_exam=None,
        total_points=Decimal("0.00"),
        created_by=actor,
        **dados,
    )

    record(
        AuditEvent.EXAM_CREATED,
        request=request,
        actor=actor,
        entity_type="Exam",
        entity_id=exam.pk,
        metadata={"module_code": module.code, "version": exam.version},
    )
    return exam


@transaction.atomic
def update_exam(exam, *, module, actor=None, request=None, **dados):
    """
    Edita a configuracao de uma prova em rascunho.

    Fora de DRAFT nada e alterado. A preferencia registrada foi por congelar
    a prova inteira na publicacao, e nao apenas a estrutura: uma excecao
    "operacional" hoje vira precedente para a proxima amanha, e a saida certa
    quando algo precisa mudar continua sendo duplicar a prova.
    """
    if exam.status != ExamStatus.DRAFT:
        raise DomainError(
            "Somente provas em rascunho podem ser editadas. Esta prova esta "
            "{}. Duplique-a para criar uma versao editavel.".format(
                exam.get_status_display().lower()
            )
        )

    _validar_modulo(module)
    dados = _validar_configuracao(_normalizar_configuracao(dados))

    if not (dados.get("failure_message") or "").strip():
        dados["failure_message"] = MENSAGEM_REPROVACAO_PADRAO

    novos = {"module": module, **dados}
    alterados = campos_alterados(exam, novos)

    for campo, valor in novos.items():
        setattr(exam, campo, valor)
    exam.save()

    if alterados:
        record(
            AuditEvent.EXAM_UPDATED,
            request=request,
            actor=actor,
            entity_type="Exam",
            entity_id=exam.pk,
            metadata={"changed_fields": sorted(alterados)},
        )
    return exam


@transaction.atomic
def publish_exam(exam, *, actor=None, request=None):
    """
    Publica a prova.

    Tudo acontece numa transacao so: validar, congelar o total de pontos,
    mudar o estado e auditar. Nao existe prova meio publicada — se qualquer
    passo falhar, nada e gravado.

    O total de pontos vira snapshot aqui. E o que permite a prova carregar a
    propria escala historica: uma correcao feita daqui a um ano divide pelo
    total que valia no dia da aplicacao, e nao pelo que a soma das questoes
    der naquele momento.
    """
    if exam.status == ExamStatus.PUBLISHED:
        raise DomainError("Esta prova ja esta publicada.")
    if exam.status == ExamStatus.CLOSED:
        raise DomainError(
            "Esta prova esta fechada e nao pode ser publicada de novo. "
            "Duplique-a para criar uma versao nova."
        )

    erros = erros_para_publicacao(exam)
    if erros:
        raise DomainError(erros)

    exam.total_points = exam.pontos_das_questoes
    exam.status = ExamStatus.PUBLISHED
    exam.published_at = timezone.now()
    exam.save(update_fields=["total_points", "status", "published_at", "updated_at"])

    record(
        AuditEvent.EXAM_PUBLISHED,
        request=request,
        actor=actor,
        entity_type="Exam",
        entity_id=exam.pk,
        metadata={
            "version": exam.version,
            "total_points": str(exam.total_points),
            "questions": exam.questions.filter(active=True).count(),
        },
    )
    return exam


@transaction.atomic
def close_exam(exam, *, actor=None, request=None):
    """
    Fecha a prova. Nada e excluido: a prova passa a ser somente leitura.
    """
    if exam.status == ExamStatus.DRAFT:
        raise DomainError("Uma prova em rascunho nao pode ser fechada; ela nem foi publicada.")
    if exam.status == ExamStatus.CLOSED:
        raise DomainError("Esta prova ja esta fechada.")

    exam.status = ExamStatus.CLOSED
    exam.closed_at = timezone.now()
    exam.save(update_fields=["status", "closed_at", "updated_at"])

    record(
        AuditEvent.EXAM_CLOSED,
        request=request,
        actor=actor,
        entity_type="Exam",
        entity_id=exam.pk,
        metadata={"version": exam.version},
    )
    return exam


@transaction.atomic
def duplicate_exam(exam, *, actor=None, request=None):
    """
    Cria uma versao nova a partir desta prova.

    A copia e independente em tudo: Exam, Question e QuestionOption novos,
    com PKs proprias. Se as questoes fossem compartilhadas, editar a v2
    reescreveria a v1 e uma prova ja aplicada deixaria de descrever o que o
    aluno respondeu.

    Concorrencia: a raiz da linhagem e travada com select_for_update antes de
    calcular a proxima versao. Dois administradores clicando em duplicar ao
    mesmo tempo entram em fila, e o segundo le a versao que o primeiro
    acabou de gravar. A constraint unica em (root_exam, version) e a rede
    embaixo disso, para o caso de alguem escrever um caminho novo e esquecer
    da transacao.
    """
    raiz_id = exam.root_exam_id or exam.pk

    # A trava e sobre a raiz, nao sobre a prova sendo duplicada: e a raiz que
    # identifica a linhagem, e e sobre a linhagem que a versao e unica.
    raiz = Exam.objects.select_for_update().get(pk=raiz_id)

    maior = Exam.objects.da_linhagem_de(raiz).aggregate(maior=Max("version"))["maior"]
    proxima_versao = (maior or 0) + 1

    copia = Exam.objects.create(
        module=exam.module,
        status=ExamStatus.DRAFT,
        version=proxima_versao,
        parent_exam=exam,
        root_exam=raiz,
        created_by=actor,
        # Snapshot nao se copia: o total de pontos de uma publicacao anterior
        # nao descreve a copia, que e rascunho e ainda pode mudar. A tela
        # mostra a soma corrente ate a nova publicacao congelar a sua.
        total_points=Decimal("0.00"),
        published_at=None,
        closed_at=None,
        # A senha acompanha a configuracao. Continua so como hash, e a tela
        # de detalhe permite trocar ou remover na versao nova.
        access_password_hash=exam.access_password_hash,
        **{campo: getattr(exam, campo) for campo in CAMPOS_CONFIGURAVEIS},
    )

    total_questoes = 0
    total_opcoes = 0

    for questao in exam.questions.prefetch_related("options").order_by("order", "id"):
        nova = Question.objects.create(
            exam=copia,
            type=questao.type,
            text=questao.text,
            points=questao.points,
            required=questao.required,
            order=questao.order,
            internal_explanation=questao.internal_explanation,
            active=questao.active,
        )
        opcoes = [
            QuestionOption(
                question=nova,
                text=opcao.text,
                is_correct=opcao.is_correct,
                order=opcao.order,
            )
            for opcao in questao.options.all()
        ]
        if opcoes:
            QuestionOption.objects.bulk_create(opcoes)
        total_questoes += 1
        total_opcoes += len(opcoes)

    record(
        AuditEvent.EXAM_DUPLICATED,
        request=request,
        actor=actor,
        entity_type="Exam",
        entity_id=copia.pk,
        metadata={
            "source_exam_id": exam.pk,
            "source_version": exam.version,
            "new_version": copia.version,
            "questions": total_questoes,
            "options": total_opcoes,
        },
    )
    return copia


# ---------------------------------------------------------------------------
# Senha de acesso
#
# O banco guarda somente o hash. A senha em texto existe apenas na
# requisicao que a definiu, e nao chega a log, auditoria, template ou
# mensagem. A trilha registra que houve troca, nunca o que foi trocado.
# ---------------------------------------------------------------------------


def _exigir_prova_administravel(exam, acao):
    if exam.status == ExamStatus.CLOSED:
        raise DomainError(
            "Nao e possivel {} de uma prova fechada.".format(acao)
        )
    return exam


@transaction.atomic
def set_exam_password(exam, senha, *, actor=None, request=None):
    """
    Define ou troca a senha de acesso da prova.

    Permitido tambem com a prova publicada, ao contrario das mudancas
    estruturais: se a senha vazar as vesperas da aplicacao, trocar precisa
    ser possivel sem invalidar a prova inteira. Trocar a senha nao altera
    questao, gabarito nem pontuacao, entao nao ameaca o historico.
    """
    _exigir_prova_administravel(exam, "alterar a senha")

    senha = (senha or "").strip()
    if not senha:
        raise DomainError("Informe a nova senha da prova.")
    if len(senha) < 4:
        raise DomainError("A senha da prova precisa ter ao menos 4 caracteres.")

    ja_tinha = exam.tem_senha
    exam.access_password_hash = make_password(senha)
    exam.save(update_fields=["access_password_hash", "updated_at"])

    record(
        AuditEvent.EXAM_PASSWORD_CHANGED,
        request=request,
        actor=actor,
        entity_type="Exam",
        entity_id=exam.pk,
        # Somente o fato. Nem a senha, nem o hash, nem o comprimento.
        metadata={"password_changed": True, "replaced_existing": ja_tinha},
    )
    return exam


@transaction.atomic
def remove_exam_password(exam, *, actor=None, request=None):
    _exigir_prova_administravel(exam, "remover a senha")

    if not exam.tem_senha:
        return exam

    exam.access_password_hash = ""
    exam.save(update_fields=["access_password_hash", "updated_at"])

    record(
        AuditEvent.EXAM_PASSWORD_REMOVED,
        request=request,
        actor=actor,
        entity_type="Exam",
        entity_id=exam.pk,
        metadata={"password_removed": True},
    )
    return exam
