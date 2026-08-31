"""
Modelos de provas.

Dividido em dois modulos por tamanho, pelo mesmo motivo de exams.services:
a administracao da prova e a realizacao dela pelo aluno somariam mais de
oitocentas linhas num arquivo unico, e sao assuntos que se leem separados.

    exam.py      Exam, Question, QuestionOption
                 o que o administrador monta

    attempt.py   ExamAttempt, AttemptQuestion, AttemptOption,
                 Answer, AnswerOption
                 o que o aluno responde

Este pacote reexporta tudo, entao `from exams.models import Exam` continua
funcionando igual, e as migrations existentes seguem validas: uma migration
referencia app_label e nome do modelo, nunca o caminho do modulo.

O cuidado central atravessa os dois arquivos: QuestionOption.is_correct e a
resposta certa e nao pode chegar ao navegador. A tentativa nao copia esse
campo — AttemptOption aponta para a QuestionOption e a leitura fica no
servidor. A barreira de renderizacao esta em exams.selectors.
"""

from exams.models.attempt import (  # noqa: F401
    CASAS_DA_NOTA,
    ESTADOS_CORRIGIVEIS,
    LIMITE_ESSAY,
    LIMITE_SHORT_TEXT,
    TIPOS_AUTOCORRIGIVEIS,
    Answer,
    AnswerOption,
    AttemptOption,
    AttemptQuestion,
    AttemptResult,
    AttemptStatus,
    ExamAttempt,
    GradingStatus,
    QuestionGradingStatus,
)
from exams.models.exam import (  # noqa: F401
    MENSAGEM_REPROVACAO_PADRAO,
    NOTA_MAXIMA,
    TEXTO_FALSO,
    TEXTO_VERDADEIRO,
    TIPOS_COM_ALTERNATIVAS,
    TIPOS_DE_CORRECAO_MANUAL,
    Exam,
    ExamQuerySet,
    ExamStatus,
    Question,
    QuestionOption,
    QuestionType,
)

__all__ = [
    # exam.py
    "Exam",
    "ExamQuerySet",
    "ExamStatus",
    "Question",
    "QuestionOption",
    "QuestionType",
    "MENSAGEM_REPROVACAO_PADRAO",
    "NOTA_MAXIMA",
    "TEXTO_FALSO",
    "TEXTO_VERDADEIRO",
    "TIPOS_COM_ALTERNATIVAS",
    "TIPOS_DE_CORRECAO_MANUAL",
    # attempt.py
    "Answer",
    "AnswerOption",
    "AttemptOption",
    "AttemptQuestion",
    "AttemptResult",
    "AttemptStatus",
    "CASAS_DA_NOTA",
    "ESTADOS_CORRIGIVEIS",
    "ExamAttempt",
    "GradingStatus",
    "LIMITE_ESSAY",
    "LIMITE_SHORT_TEXT",
    "QuestionGradingStatus",
    "TIPOS_AUTOCORRIGIVEIS",
]
