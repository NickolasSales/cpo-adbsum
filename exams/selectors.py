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


def provas_do_modulo_para_aluno(module, *, agora):
    """
    Provas que um aluno poderia ver num modulo, hoje.

    Existe para a Etapa 4 e ja fica com o criterio no lugar certo: somente
    provas publicadas e dentro da janela. Nesta etapa nenhuma tela de aluno
    chama esta funcao — a area do aluno continua sem provas, de proposito.
    """
    return (
        Exam.objects.filter(module=module, status="PUBLISHED")
        .filter(open_at__lte=agora, close_at__gte=agora)
        .order_by("title", "-version")
    )
