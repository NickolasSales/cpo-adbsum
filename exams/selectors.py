"""
Leitura de provas para quem nao pode ver o gabarito.

Este modulo existe por um motivo unico: separar fisicamente os dados que
podem chegar ao navegador do aluno dos que nao podem. `is_correct` e
`internal_explanation` nunca atravessam daqui para fora.

Por que estruturas proprias e nao o queryset
--------------------------------------------
A saida nao sao instancias de Question e QuestionOption, e sim dataclasses
frozen com exatamente os campos visiveis. Um objeto do ORM com `.only()` ou
`.defer()` nao serve como barreira: o atributo continua existindo, e ler
`opcao.is_correct` num template dispara uma consulta nova e devolve a
resposta certa, sem nenhum aviso. Uma dataclass sem o campo faz o vazamento
virar AttributeError em vez de gabarito na tela.

O preview administrativo consome exatamente estas funcoes. Isso e proposital:
a tela do aluno da Etapa 4 vai usar as mesmas, entao qualquer vazamento
aparece agora, com teste, e nao no dia da prova.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List

from exams.models import Exam, Question, QuestionOption, QuestionType


@dataclass(frozen=True)
class OpcaoVisivel:
    """Alternativa como o aluno pode ve-la. Sem is_correct, por construcao."""

    id: int
    text: str
    order: int


@dataclass(frozen=True)
class QuestaoVisivel:
    """
    Questao como o aluno pode ve-la.

    Sem internal_explanation e sem qualquer marca de qual alternativa esta
    certa. A ordem das alternativas e a ordem cadastrada; o sorteio previsto
    em randomize_options acontecera na Etapa 4, ao montar a tentativa, e nao
    aqui.
    """

    id: int
    numero: int
    type: str
    type_display: str
    text: str
    points: Decimal
    required: bool
    options: List[OpcaoVisivel] = field(default_factory=list)

    @property
    def usa_alternativas(self):
        return self.type in {
            QuestionType.SINGLE_CHOICE,
            QuestionType.MULTIPLE_CHOICE,
            QuestionType.TRUE_FALSE,
        }

    @property
    def multipla(self):
        return self.type == QuestionType.MULTIPLE_CHOICE


def questoes_para_aluno(exam):
    """
    Questoes ativas da prova, sem gabarito, prontas para renderizar.

    Duas consultas no total, independentemente do numero de questoes: uma
    para as questoes e uma para as alternativas. Nao ha consulta por questao
    nem por alternativa.
    """
    questoes = list(
        Question.objects.filter(exam=exam, active=True)
        .values("id", "type", "text", "points", "required", "order")
        .order_by("order", "id")
    )
    if not questoes:
        return []

    ids = [questao["id"] for questao in questoes]

    # sem_gabarito() e um .values() restrito a id, texto e ordem. O campo
    # is_correct nao acompanha o resultado.
    opcoes_por_questao = {}
    for opcao in QuestionOption.objects.filter(question_id__in=ids).sem_gabarito():
        opcoes_por_questao.setdefault(opcao["question_id"], []).append(
            OpcaoVisivel(
                id=opcao["id"], text=opcao["text"], order=opcao["order"]
            )
        )

    rotulos = dict(QuestionType.choices)

    visiveis = []
    for numero, questao in enumerate(questoes, start=1):
        visiveis.append(
            QuestaoVisivel(
                id=questao["id"],
                numero=numero,
                type=questao["type"],
                type_display=rotulos.get(questao["type"], questao["type"]),
                text=questao["text"],
                points=questao["points"],
                required=questao["required"],
                options=opcoes_por_questao.get(questao["id"], []),
            )
        )
    return visiveis


def gabarito(exam):
    """
    Questoes com resposta correta e explicacao interna. ADMIN somente.

    O oposto de questoes_para_aluno, e nomeado para que ninguem chame por
    engano achando que e a versao segura. Devolve instancias do ORM, com
    alternativas ja carregadas por prefetch para nao gerar uma consulta por
    questao.
    """
    return list(
        Question.objects.filter(exam=exam)
        .prefetch_related("options")
        .order_by("order", "id")
    )


# ---------------------------------------------------------------------------
# A prova sendo realizada (Etapa 4)
# ---------------------------------------------------------------------------
#
# A diferenca para QuestaoVisivel acima nao e cosmetica. Aquelas estruturas
# carregam Question.id e QuestionOption.id, porque servem a telas
# administrativas onde o id e util e legitimo. Estas nao carregam id nenhum:
# na tela do aluno o identificador de uma alternativa e uma credencial de
# escrita, e o unico identificador que ele pode conhecer e o token da propria
# tentativa dele.
#
# Nenhuma das duas carrega is_correct nem internal_explanation.


@dataclass(frozen=True)
class OpcaoDaTentativa:
    """
    Alternativa como este aluno a ve nesta tentativa.

    Sem is_correct e sem QuestionOption.id, por construcao. `token` e o UUID
    gerado para esta tentativa: a mesma alternativa tem token diferente para
    cada aluno, entao combinar "marque a B" pelo token nao funciona.
    """

    token: str
    text: str
    display_order: int
    marcada: bool = False


@dataclass(frozen=True)
class QuestaoDaTentativa:
    """
    Questao como este aluno a ve nesta tentativa.

    `numero` e a posicao na tela deste aluno, e nao a ordem cadastrada: com
    sorteio ligado, a questao 1 de um e a 3 de outro. E o unico jeito de o
    aluno se referir a uma questao, ja que ele nunca viu um id.
    """

    token: str
    numero: int
    type: str
    type_display: str
    text: str
    points: Decimal
    required: bool
    display_order: int
    texto_salvo: str = ""
    options: List[OpcaoDaTentativa] = field(default_factory=list)

    @property
    def usa_alternativas(self):
        return self.type in {
            QuestionType.SINGLE_CHOICE,
            QuestionType.MULTIPLE_CHOICE,
            QuestionType.TRUE_FALSE,
        }

    @property
    def multipla(self):
        return self.type == QuestionType.MULTIPLE_CHOICE

    @property
    def dissertativa(self):
        return self.type == QuestionType.ESSAY

    @property
    def respondida(self):
        if self.usa_alternativas:
            return any(opcao.marcada for opcao in self.options)
        return bool(self.texto_salvo.strip())


def questoes_da_tentativa(attempt):
    """
    O que a tela da prova recebe: enunciados, alternativas e o que ja foi
    respondido, na ordem sorteada para este aluno.

    Quatro consultas no total, qualquer que seja o tamanho da prova:

        1  questoes da tentativa, com a Question
        2  alternativas da tentativa, com o texto da QuestionOption
        3  respostas ja gravadas
        4  alternativas marcadas

    A ordem vem exclusivamente de display_order, gravado no start. Nunca de
    QuestionOption.id: ordenar por id devolveria as alternativas na ordem em
    que o administrador as digitou, e como a correta costuma ser a primeira
    digitada, isso entregaria o gabarito pela ordem.

    Os ids internos de AttemptQuestion e AttemptOption sao lidos aqui para
    cruzar as respostas, e ficam de fora das estruturas devolvidas.
    """
    from exams.models import Answer, AnswerOption, AttemptOption, AttemptQuestion

    linhas = list(
        AttemptQuestion.objects.filter(attempt=attempt)
        .select_related("question")
        .order_by("display_order", "id")
    )
    if not linhas:
        return []

    ids_das_linhas = [linha.pk for linha in linhas]

    # Somente os campos publicos da alternativa. O texto vem por travessia de
    # FK no .values(), entao is_correct nao acompanha o resultado nem por
    # acidente — o campo nao existe na estrutura devolvida pelo banco.
    alternativas = list(
        AttemptOption.objects.filter(attempt_question_id__in=ids_das_linhas)
        .values("id", "attempt_question_id", "public_token", "display_order", "option__text")
        .order_by("display_order", "id")
    )

    textos_salvos = {}
    ids_de_resposta = {}
    for resposta in Answer.objects.filter(
        attempt_question_id__in=ids_das_linhas
    ).values("id", "attempt_question_id", "text_answer"):
        textos_salvos[resposta["attempt_question_id"]] = resposta["text_answer"]
        ids_de_resposta[resposta["id"]] = resposta["attempt_question_id"]

    marcadas = set(
        AnswerOption.objects.filter(answer_id__in=ids_de_resposta).values_list(
            "attempt_option_id", flat=True
        )
    )

    opcoes_por_linha = {}
    for alternativa in alternativas:
        opcoes_por_linha.setdefault(alternativa["attempt_question_id"], []).append(
            OpcaoDaTentativa(
                token=str(alternativa["public_token"]),
                text=alternativa["option__text"],
                display_order=alternativa["display_order"],
                marcada=alternativa["id"] in marcadas,
            )
        )

    rotulos = dict(QuestionType.choices)

    return [
        QuestaoDaTentativa(
            token=str(linha.public_token),
            numero=linha.display_order + 1,
            type=linha.question.type,
            type_display=rotulos.get(linha.question.type, linha.question.type),
            text=linha.question.text,
            points=linha.question.points,
            required=linha.question.required,
            display_order=linha.display_order,
            texto_salvo=textos_salvos.get(linha.pk, ""),
            options=opcoes_por_linha.get(linha.pk, []),
        )
        for linha in linhas
    ]


@dataclass(frozen=True)
class ProvaDoAluno:
    """
    Uma prova como ela aparece na tela do modulo.

    Reune, num objeto so, o que o template precisa para decidir o que mostrar
    sem consultar nada: a situacao da prova, a situacao da ultima tentativa do
    aluno e quantas tentativas ainda restam. Deixar essa combinacao para o
    template significaria espalhar regra por HTML.

    `attempt_public_id` so vem preenchido quando ha tentativa em andamento, e
    e o unico identificador de tentativa que chega ao navegador.
    """

    id: int
    title: str
    version: int
    duration_minutes: int
    passing_score: Decimal
    total_points: Decimal
    open_at: object
    close_at: object
    tem_senha: bool
    estado: str
    rotulo: str
    pode_iniciar: bool
    tentativas_utilizadas: int
    tentativas_permitidas: int
    attempt_public_id: str = ""

    @property
    def tentativas_restantes(self):
        return max(0, self.tentativas_permitidas - self.tentativas_utilizadas)


def provas_do_modulo_para_aluno(module, student, *, agora):
    """
    Provas publicadas do modulo, com a situacao de cada uma para este aluno.

    Duas consultas: as provas do modulo e as tentativas do aluno nelas. A
    combinacao acontece em Python, e nao com uma consulta por prova.

    Provas fechadas continuam na lista quando o aluno ja tentou. Sumir com a
    prova que ele acabou de fazer daria a impressao de que o envio se perdeu.
    Uma prova fechada que ele nunca abriu nao aparece: nao ha nada a fazer com
    ela nem historico para mostrar.
    """
    from exams.models import AttemptStatus, ExamAttempt, ExamStatus

    provas = list(
        Exam.objects.filter(
            module=module, status__in=(ExamStatus.PUBLISHED, ExamStatus.CLOSED)
        ).order_by("title", "-version")
    )
    if not provas:
        return []

    tentativas_por_prova = {}
    for tentativa in (
        ExamAttempt.objects.filter(
            student=student, exam_id__in=[prova.pk for prova in provas]
        )
        .que_contam_para_o_limite()
        .order_by("exam_id", "attempt_number")
    ):
        tentativas_por_prova.setdefault(tentativa.exam_id, []).append(tentativa)

    cartoes = []
    for prova in provas:
        tentativas = tentativas_por_prova.get(prova.pk, [])
        cartao = _cartao_da_prova(prova, tentativas, agora=agora)
        if cartao is not None:
            cartoes.append(cartao)
    return cartoes


def _cartao_da_prova(prova, tentativas, *, agora):
    """
    Traduz prova + tentativas do aluno num unico estado exibivel.

    A ordem das perguntas e a prioridade do que o aluno precisa saber: se ha
    prova aberta agora, isso vem antes de qualquer outra coisa.
    """
    from exams.models import AttemptStatus, ExamStatus

    utilizadas = len(tentativas)
    ultima = tentativas[-1] if tentativas else None

    em_andamento = next(
        (t for t in tentativas if t.status == AttemptStatus.IN_PROGRESS), None
    )
    if em_andamento is not None and not em_andamento.prazo_vencido(agora):
        return _montar_cartao(
            prova,
            estado="EM_ANDAMENTO",
            rotulo="Em andamento",
            pode_iniciar=False,
            utilizadas=utilizadas,
            attempt_public_id=str(em_andamento.public_id),
        )

    # Em andamento com prazo vencido ainda nao processado pelo comando de
    # expiracao. Para o aluno o tempo acabou, e e isso que a tela diz.
    if em_andamento is not None:
        return _montar_cartao(
            prova,
            estado="EXPIRADA",
            rotulo="Tempo encerrado",
            pode_iniciar=False,
            utilizadas=utilizadas,
        )

    janela_aberta = (
        prova.status == ExamStatus.PUBLISHED
        and prova.open_at is not None
        and prova.close_at is not None
        and prova.open_at <= agora < prova.close_at
    )
    restam = utilizadas < prova.max_attempts

    if ultima is not None and not (janela_aberta and restam):
        if ultima.status == AttemptStatus.SUBMITTED:
            return _montar_cartao(
                prova,
                estado="ENVIADA",
                rotulo="Enviada",
                pode_iniciar=False,
                utilizadas=utilizadas,
            )
        return _montar_cartao(
            prova,
            estado="EXPIRADA",
            rotulo="Tempo encerrado",
            pode_iniciar=False,
            utilizadas=utilizadas,
        )

    if prova.status == ExamStatus.CLOSED:
        # Sem tentativa e sem historico: nao ha o que o aluno faca aqui.
        if ultima is None:
            return None
        return _montar_cartao(
            prova,
            estado="ENCERRADA",
            rotulo="Periodo encerrado",
            pode_iniciar=False,
            utilizadas=utilizadas,
        )

    if prova.open_at is not None and agora < prova.open_at:
        return _montar_cartao(
            prova,
            estado="AGENDADA",
            rotulo="Ainda nao disponivel",
            pode_iniciar=False,
            utilizadas=utilizadas,
        )

    if prova.close_at is not None and agora >= prova.close_at:
        if ultima is None:
            return None
        return _montar_cartao(
            prova,
            estado="ENCERRADA",
            rotulo="Periodo encerrado",
            pode_iniciar=False,
            utilizadas=utilizadas,
        )

    if not restam:
        return _montar_cartao(
            prova,
            estado="SEM_TENTATIVAS",
            rotulo="Tentativa utilizada",
            pode_iniciar=False,
            utilizadas=utilizadas,
        )

    return _montar_cartao(
        prova,
        estado="DISPONIVEL",
        rotulo="Disponivel",
        pode_iniciar=True,
        utilizadas=utilizadas,
    )


def _montar_cartao(
    prova, *, estado, rotulo, pode_iniciar, utilizadas, attempt_public_id=""
):
    return ProvaDoAluno(
        id=prova.pk,
        title=prova.title,
        version=prova.version,
        duration_minutes=prova.duration_minutes,
        passing_score=prova.passing_score,
        total_points=prova.total_points,
        open_at=prova.open_at,
        close_at=prova.close_at,
        tem_senha=prova.tem_senha,
        estado=estado,
        rotulo=rotulo,
        pode_iniciar=pode_iniciar,
        tentativas_utilizadas=utilizadas,
        tentativas_permitidas=prova.max_attempts,
        attempt_public_id=attempt_public_id,
    )
