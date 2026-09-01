"""
Correcao das tentativas: pontuacao, nota e aprovacao.

Duas dimensoes que nao se misturam
----------------------------------
ExamAttempt.status responde "o aluno ainda esta fazendo?" e continua com os
mesmos quatro valores da Etapa 4. grading_status responde "ja sabemos a
nota?". Uma tentativa SUBMITTED pode estar PENDING, AWAITING_REVIEW ou GRADED
sem que o aluno faca nada — sao perguntas independentes, e um campo unico
obrigaria a perder uma das duas respostas.

Fluxo
-----
    SUBMITTED / EXPIRED
        |
        v
    grade_objective_questions()   corrige tudo que a maquina sabe corrigir
        |
        +-- nenhuma pendencia manual --> finalize_grading() --> GRADED
        |
        +-- ha dissertativa a ler ----> AWAITING_REVIEW
                                            |
                                    save_manual_grade()  (quantas vezes quiser)
                                            |
                                    finalize_grading()  --> GRADED

Precisao
--------
Tudo em Decimal, do primeiro ponto ate a comparacao final. A nota exibida ao
aluno tem duas casas, mas a decisao de aprovacao usa o valor cheio, com seis
casas: uma nota de 7.996 aparece como 8,00 e ainda assim reprova contra uma
nota minima de 8,00. Arredondar antes de comparar seria aprovar quem nao
atingiu o minimo — e o motivo de final_score ter seis casas decimais.

Nada aqui aceita valor vindo do navegador. O avaliador informa apenas os
pontos de cada questao manual e um comentario opcional; todo o resto —
somatorios, nota, resultado — e calculado no servidor.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record
from common.exceptions import DomainError
from exams.models import (
    ESTADOS_CORRIGIVEIS,
    TIPOS_AUTOCORRIGIVEIS,
    Answer,
    AttemptQuestion,
    AttemptResult,
    ExamAttempt,
    GradingStatus,
    QuestionGradingStatus,
    QuestionType,
)

ZERO = Decimal("0.00")
CENTAVO = Decimal("0.01")
ESCALA_DA_NOTA = Decimal("10")
PRECISAO_DA_NOTA = Decimal("0.000001")


# ---------------------------------------------------------------------------
# Excecoes
# ---------------------------------------------------------------------------


class TentativaNaoCorrigivel(DomainError):
    """A tentativa nao esta num estado que admita correcao."""


class NotaForaDoIntervalo(DomainError):
    """Pontos abaixo de zero ou acima do valor da questao."""


class ManuaisPendentes(DomainError):
    """Faltam questoes manuais sem nota para poder finalizar."""

    def __init__(self, numeros):
        self.numeros = list(numeros)
        super().__init__(
            "Ainda faltam notas: questao(oes) {}.".format(
                ", ".join(str(n) for n in self.numeros)
            )
        )


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def tentativas_para_corrigir():
    """Fila de correcao: o que espera um avaliador humano."""
    return (
        ExamAttempt.objects.filter(grading_status=GradingStatus.AWAITING_REVIEW)
        .select_related("student", "exam", "exam__module")
        .order_by("submitted_at", "id")
    )


def tentativas_corrigidas():
    """Notas fechadas."""
    return (
        ExamAttempt.objects.filter(grading_status=GradingStatus.GRADED)
        .select_related("student", "exam", "exam__module")
        .order_by("-graded_at", "-id")
    )


def linhas_da_correcao(attempt):
    """
    As questoes da tentativa com resposta e alternativas ja carregadas.

    Uma consulta por relacao, e nao uma por questao: a tela de correcao de uma
    prova de vinte questoes faria dezenas de idas ao banco sem isto.
    """
    return (
        AttemptQuestion.objects.filter(attempt=attempt)
        .select_related("question", "graded_by")
        .prefetch_related(
            "options__option",
            Prefetch("answer", queryset=Answer.objects.prefetch_related(
                "selected_options__attempt_option__option"
            )),
        )
        .order_by("display_order", "id")
    )


def e_automatica(linha):
    """Se a maquina corrige esta questao sozinha."""
    return linha.question.type in TIPOS_AUTOCORRIGIVEIS


def resposta_vazia(linha):
    """
    Se nao ha o que corrigir nesta questao.

    Vale tanto para "nunca respondeu" quanto para "abriu e apagou": nos dois
    casos nao existe conteudo, e o resultado e o mesmo.
    """
    resposta = getattr(linha, "answer", None)
    if resposta is None:
        return True
    if linha.question.type in TIPOS_AUTOCORRIGIVEIS:
        return not resposta.selected_options.exists()
    return not (resposta.text_answer or "").strip()


# ---------------------------------------------------------------------------
# Correcao automatica
# ---------------------------------------------------------------------------


def _pontos_da_objetiva(linha):
    """
    Corrige uma questao objetiva. Tudo ou nada.

    Nao ha pontuacao parcial em nenhum dos tres tipos, e isso e decisao de
    negocio, nao limitacao: meio ponto por acertar metade de uma multipla
    escolha premiaria quem marca tudo.

    O gabarito e lido AQUI, no servidor, a partir de QuestionOption.is_correct.
    Ele nunca esteve na tentativa e nunca foi ao navegador — AttemptOption
    guarda apenas a referencia e a posicao em que a alternativa apareceu.
    """
    valor = linha.points_snapshot if linha.points_snapshot is not None else linha.question.points

    resposta = getattr(linha, "answer", None)
    if resposta is None:
        return ZERO

    marcadas = {
        selecao.attempt_option.option_id for selecao in resposta.selected_options.all()
    }
    corretas = {
        alternativa.option_id
        for alternativa in linha.options.all()
        if alternativa.option.is_correct
    }

    # Comparacao de conjuntos, e nao contagem de acertos. "A, C" quando o
    # gabarito e "A, C, D" e uma resposta incompleta, e "A, C, D, E" e uma
    # resposta com sobra; as duas valem zero, e so o conjunto exato pontua.
    if marcadas and marcadas == corretas:
        return valor
    return ZERO


@transaction.atomic
def grade_objective_questions(attempt, *, actor=None, request=None):
    """
    Corrige tudo que nao depende de leitura humana e decide o proximo estado.

    Idempotente: rodar duas vezes produz o mesmo resultado e nao soma pontos
    de novo, porque cada questao recebe um valor absoluto em vez de um
    incremento. Isso importa porque a funcao e chamada tanto pelo envio quanto
    pela expiracao, e uma tentativa pode passar pelos dois caminhos.

    Questoes manuais VAZIAS tambem sao fechadas aqui, com zero. Nao existe
    conteudo para avaliar numa redacao em branco, e obrigar o administrador a
    abrir cada uma delas so para escrever 0 transformaria a fila de correcao
    numa fila de cliques. O que fica pendente e o que tem texto.

    Depois de corrigir, ou finaliza sozinha (prova so objetiva) ou marca
    AWAITING_REVIEW.
    """
    travada = (
        ExamAttempt.objects.select_for_update().select_related("exam").get(pk=attempt.pk)
    )

    if travada.status not in ESTADOS_CORRIGIVEIS:
        raise TentativaNaoCorrigivel(
            "So tentativas enviadas ou expiradas podem ser corrigidas."
        )

    if travada.grading_status == GradingStatus.GRADED:
        # Ja fechada. Recorrigir mudaria uma nota que o aluno pode ter visto.
        return travada

    primeira_vez = travada.grading_status == GradingStatus.PENDING

    agora = timezone.now()
    pendentes = []

    for linha in linhas_da_correcao(travada):
        if e_automatica(linha):
            _gravar_automatica(linha, _pontos_da_objetiva(linha), agora)
            continue

        if resposta_vazia(linha):
            _gravar_automatica(linha, ZERO, agora)
            continue

        if linha.grading_status == QuestionGradingStatus.PENDING:
            pendentes.append(linha)

    if primeira_vez:
        record(
            AuditEvent.GRADING_STARTED,
            request=request,
            actor=actor,
            student=travada.student,
            entity_type="ExamAttempt",
            entity_id=travada.pk,
            metadata={"manuais_pendentes": len(pendentes)},
        )

    if pendentes:
        travada.grading_status = GradingStatus.AWAITING_REVIEW
        travada.save(update_fields=["grading_status", "updated_at"])
        return travada

    return _fechar(travada, actor=actor, request=request, agora=agora)


def _gravar_automatica(linha, pontos, agora):
    """
    Grava uma nota que a maquina decidiu.

    Nao sobrescreve o trabalho de um avaliador: uma questao ja marcada como
    MANUALLY_GRADED e deixada como esta, porque recorrigir automaticamente
    apagaria a nota que uma pessoa atribuiu depois de ler a resposta.
    """
    if linha.grading_status == QuestionGradingStatus.MANUALLY_GRADED:
        return

    linha.awarded_points = pontos
    linha.grading_status = QuestionGradingStatus.AUTO_GRADED
    linha.graded_by = None
    linha.graded_at = agora
    linha.save(
        update_fields=["awarded_points", "grading_status", "graded_by", "graded_at"]
    )


# ---------------------------------------------------------------------------
# Correcao manual
# ---------------------------------------------------------------------------


@transaction.atomic
def save_manual_grade(
    attempt, *, question_id, points, comment="", actor=None, request=None
):
    """
    Registra a nota de UMA questao dissertativa ou de texto curto.

    Chamado quantas vezes o avaliador quiser: salvar rascunho nao fecha a
    nota. Fechar e outra operacao, finalize_grading, porque uma prova com
    cinco redacoes costuma ser corrigida em mais de uma sessao e um "salvar"
    que finalizasse por engano nao teria volta.

    O que o navegador pode influenciar: qual questao, quantos pontos, qual
    comentario. Nada mais. Somatorios, nota e resultado sao calculados aqui.

    ATENCAO ao nome do parametro: `question_id` e a PK da AttemptQuestion — a
    linha DAQUELA tentativa —, e nao a PK da Question. A busca abaixo filtra
    por attempt E pk justamente para que uma linha de outra tentativa nao seja
    encontrada. Numa base pequena os dois numeros costumam coincidir, entao o
    engano passa em teste isolado e so aparece quando a suite inteira roda.
    """
    travada = ExamAttempt.objects.select_for_update().get(pk=attempt.pk)

    if travada.grading_status == GradingStatus.GRADED:
        raise TentativaNaoCorrigivel(
            "Esta correcao ja foi finalizada e nao aceita mais alteracao."
        )
    if travada.status not in ESTADOS_CORRIGIVEIS:
        raise TentativaNaoCorrigivel(
            "So tentativas enviadas ou expiradas podem ser corrigidas."
        )

    linha = (
        AttemptQuestion.objects.select_related("question")
        .filter(attempt=travada, pk=question_id)
        .first()
    )
    if linha is None:
        # Questao de outra tentativa, ou inexistente. Mesma recusa nos dois
        # casos: distinguir transformaria o endpoint num oraculo.
        raise DomainError("Questao nao encontrada nesta tentativa.")

    if e_automatica(linha):
        raise DomainError(
            "Questoes objetivas sao corrigidas automaticamente e nao aceitam "
            "nota manual."
        )

    valor = _validar_pontos(points, linha)

    linha.awarded_points = valor
    linha.grader_comment = (comment or "").strip()
    linha.grading_status = QuestionGradingStatus.MANUALLY_GRADED
    linha.graded_by = actor
    linha.graded_at = timezone.now()
    linha.save(
        update_fields=[
            "awarded_points",
            "grader_comment",
            "grading_status",
            "graded_by",
            "graded_at",
        ]
    )

    record(
        AuditEvent.MANUAL_GRADE_SAVED,
        request=request,
        actor=actor,
        student=travada.student,
        entity_type="ExamAttempt",
        entity_id=travada.pk,
        # Os pontos e a posicao da questao, nunca o texto da resposta nem o
        # comentario do avaliador.
        metadata={
            "questao": linha.display_order + 1,
            "pontos": str(valor),
        },
    )
    return linha


def _validar_pontos(points, linha):
    """
    Converte e confere os pontos informados.

    O teto e o valor da propria questao. Uma dissertativa de 2,00 que
    recebesse 5,00 elevaria a nota da prova acima do total possivel, e
    nenhuma tela mostraria de onde veio o excedente.

    A mesma regra existe como CheckConstraint no banco. Aqui ela produz uma
    mensagem que o avaliador entende; la ela protege contra quem nao passa
    por este servico.
    """
    if points is None or (isinstance(points, str) and not points.strip()):
        raise NotaForaDoIntervalo("Informe os pontos da questao.")

    try:
        valor = Decimal(str(points).replace(",", ".")).quantize(
            CENTAVO, rounding=ROUND_HALF_UP
        )
    except (ArithmeticError, ValueError):
        raise NotaForaDoIntervalo("Pontos invalidos.")

    maximo = (
        linha.points_snapshot
        if linha.points_snapshot is not None
        else linha.question.points
    )

    if valor < ZERO:
        raise NotaForaDoIntervalo("Os pontos nao podem ser negativos.")
    if valor > maximo:
        raise NotaForaDoIntervalo(
            "Os pontos nao podem passar de {} nesta questao.".format(maximo)
        )
    return valor


def questoes_manuais_pendentes(attempt):
    """Posicoes (1-based) das questoes manuais ainda sem nota."""
    return [
        linha.display_order + 1
        for linha in AttemptQuestion.objects.filter(
            attempt=attempt, grading_status=QuestionGradingStatus.PENDING
        ).order_by("display_order")
    ]


# ---------------------------------------------------------------------------
# Fechamento
# ---------------------------------------------------------------------------


@transaction.atomic
def finalize_grading(attempt, *, actor=None, request=None):
    """
    Fecha a nota: soma, calcula, decide aprovacao e registra.

    Idempotente e serializada. O select_for_update garante que dois
    administradores clicando em "Finalizar" ao mesmo tempo produzam UMA
    finalizacao: o segundo encontra a tentativa ja GRADED e devolve o
    resultado existente, sem recalcular e sem gravar um segundo evento.
    """
    travada = ExamAttempt.objects.select_for_update().select_related(
        "student", "exam"
    ).get(pk=attempt.pk)

    if travada.grading_status == GradingStatus.GRADED:
        return travada

    if travada.status not in ESTADOS_CORRIGIVEIS:
        raise TentativaNaoCorrigivel(
            "So tentativas enviadas ou expiradas podem ser corrigidas."
        )

    pendentes = questoes_manuais_pendentes(travada)
    if pendentes:
        raise ManuaisPendentes(pendentes)

    return _fechar(travada, actor=actor, request=request, agora=timezone.now())


def _fechar(attempt, *, actor, request, agora):
    """
    A regra do fechamento, num lugar so.

    Chamada tanto pela correcao automatica (prova sem questao manual) quanto
    por finalize_grading. Duas implementacoes acabariam discordando sobre o
    que "corrigida" significa.

    Espera a tentativa ja travada por quem chamou.
    """
    objetivos = ZERO
    manuais = ZERO

    for linha in AttemptQuestion.objects.filter(attempt=attempt):
        pontos = linha.awarded_points or ZERO
        if linha.grading_status == QuestionGradingStatus.MANUALLY_GRADED:
            manuais += pontos
        else:
            objetivos += pontos

    obtidos = objetivos + manuais
    nota = calculate_final_score(obtidos, attempt.total_points_snapshot)

    # A comparacao usa a nota CHEIA, antes de qualquer arredondamento visual.
    # 7.996 exibido como 8,00 continua sendo menor que 8,00.
    aprovado = nota >= Decimal(attempt.passing_score_snapshot)

    attempt.objective_points = objetivos
    attempt.manual_points = manuais
    attempt.obtained_points = obtidos
    attempt.final_score = nota
    attempt.result = AttemptResult.APPROVED if aprovado else AttemptResult.FAILED
    attempt.grading_status = GradingStatus.GRADED
    attempt.graded_at = agora
    attempt.save(
        update_fields=[
            "objective_points",
            "manual_points",
            "obtained_points",
            "final_score",
            "result",
            "grading_status",
            "graded_at",
            "updated_at",
        ]
    )

    record(
        AuditEvent.GRADING_COMPLETED,
        request=request,
        actor=actor,
        student=attempt.student,
        entity_type="ExamAttempt",
        entity_id=attempt.pk,
        # A nota pode ficar na trilha: ela nao e segredo, e a pergunta "que
        # nota foi fechada, por quem e quando" e exatamente o que uma trilha
        # de auditoria precisa responder. Respostas e gabarito ficam de fora.
        metadata={
            "result": str(attempt.result),
            "final_score": str(attempt.final_score),
        },
    )
    return attempt


def calculate_final_score(obtained_points, total_points):
    """
    Converte pontos para a escala de 0 a 10.

        nota = pontos_obtidos / total_de_pontos * 10

    Decimal do inicio ao fim. Com float, 0.1 + 0.2 nao e 0.3, e uma prova de
    dez questoes de 0,1 ponto poderia somar 0,9999999999999999 — reprovando
    quem acertou tudo.

    Prova com total zero devolve zero em vez de estourar: nao deveria existir,
    porque publicar exige pontuacao positiva, mas uma divisao por zero aqui
    derrubaria a correcao inteira em vez de sinalizar o problema.

    Seis casas decimais no resultado, e nao duas. As duas casas sao da tela;
    aqui o que importa e nao perder o digito que separa 7.996 de 8.000.
    """
    total = Decimal(total_points or 0)
    if total <= 0:
        return ZERO.quantize(PRECISAO_DA_NOTA)

    bruta = (Decimal(obtained_points or 0) / total) * ESCALA_DA_NOTA
    return bruta.quantize(PRECISAO_DA_NOTA, rounding=ROUND_HALF_UP)


def nota_para_exibicao(valor):
    """
    A nota como o usuario le: duas casas, virgula decimal.

    NUNCA usar para decidir aprovacao. Este helper existe justamente para que
    o arredondamento aconteca num lugar so, longe da comparacao — 7.996 vira
    "8,00" aqui e continua reprovando la.
    """
    if valor is None:
        return ""
    return "{:.2f}".format(
        Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    ).replace(".", ",")
