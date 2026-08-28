"""
Manutencao de questoes e alternativas.

Questao e alternativas sao salvas juntas, sempre dentro de uma transacao. O
motivo e simples: uma questao de escolha unica sem alternativas nao e uma
questao pela metade, e uma estrutura invalida. Gravar o enunciado e falhar
nas alternativas deixaria exatamente isso no banco.

O padrao usado aqui e construir e depois validar: as linhas sao criadas,
`erros_da_questao` roda sobre o que ficou gravado e, havendo problema, a
excecao desfaz a transacao inteira. Validar sobre o resultado real, e nao
sobre os dados de entrada, evita que a validacao e a gravacao discordem.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Max

from audit.models import AuditEvent
from audit.services import record
from common.exceptions import DomainError
from exams.models import (
    Question,
    QuestionOption,
    QuestionType,
    TEXTO_FALSO,
    TEXTO_VERDADEIRO,
    TIPOS_COM_ALTERNATIVAS,
)
from exams.services.validation import erros_da_questao, exigir_estrutura_editavel


def _proxima_ordem(exam):
    maior = exam.questions.aggregate(maior=Max("order"))["maior"]
    return (maior or 0) + 1


def _validar_valor(points):
    if points is None:
        raise DomainError("O valor da questao e obrigatorio.")
    try:
        valor = Decimal(points)
    except (InvalidOperation, TypeError, ValueError):
        raise DomainError("O valor da questao precisa ser um numero.")
    if valor <= 0:
        raise DomainError("O valor da questao precisa ser maior que zero.")
    return valor


def _validar_tipo(tipo):
    if tipo not in QuestionType.values:
        raise DomainError("Tipo de questao invalido.")
    return tipo


def _gravar_alternativas(questao, *, opcoes, resposta_verdadeira):
    """
    Regrava as alternativas da questao conforme o tipo.

    Substituicao completa, e nao atualizacao linha a linha: manter as PKs
    antigas nao traz beneficio enquanto nao existem respostas de alunos
    apontando para elas, e a substituicao elimina toda a classe de bugs de
    sincronizacao entre o que veio do formulario e o que estava no banco.
    """
    questao.options.all().delete()

    if questao.type == QuestionType.TRUE_FALSE:
        # Os dois textos sao fixos. O administrador escolhe apenas qual e a
        # correta; o resto e construido aqui para que a tela do aluno possa
        # contar com a forma sempre igual.
        if resposta_verdadeira is None:
            raise DomainError(
                "Escolha se a resposta correta e Verdadeiro ou Falso."
            )
        QuestionOption.objects.bulk_create(
            [
                QuestionOption(
                    question=questao,
                    text=TEXTO_VERDADEIRO,
                    is_correct=bool(resposta_verdadeira),
                    order=1,
                ),
                QuestionOption(
                    question=questao,
                    text=TEXTO_FALSO,
                    is_correct=not bool(resposta_verdadeira),
                    order=2,
                ),
            ]
        )
        return

    if questao.type not in TIPOS_COM_ALTERNATIVAS:
        # SHORT_TEXT e ESSAY nunca tem alternativas. Ignorar o que veio e
        # coerente com a interface, que nem oferece o campo.
        return

    limpas = []
    for indice, dados in enumerate(opcoes or [], start=1):
        texto = (dados.get("text") or "").strip()
        if not texto:
            continue
        limpas.append(
            QuestionOption(
                question=questao,
                text=texto,
                is_correct=bool(dados.get("is_correct")),
                order=dados.get("order") or indice,
            )
        )

    if limpas:
        QuestionOption.objects.bulk_create(limpas)


def _auditar(questao, evento, *, actor, request, extra=None):
    metadata = {
        "exam_id": questao.exam_id,
        "type": questao.type,
        "order": questao.order,
    }
    if extra:
        metadata.update(extra)
    record(
        evento,
        request=request,
        actor=actor,
        entity_type="Question",
        entity_id=questao.pk,
        metadata=metadata,
    )


@transaction.atomic
def create_question(
    exam,
    *,
    type,
    text,
    points,
    required=True,
    order=None,
    internal_explanation="",
    active=True,
    opcoes=None,
    resposta_verdadeira=None,
    actor=None,
    request=None,
):
    exigir_estrutura_editavel(exam, "criar questao")

    tipo = _validar_tipo(type)
    valor = _validar_valor(points)
    enunciado = (text or "").strip()
    if not enunciado:
        raise DomainError("O enunciado da questao e obrigatorio.")

    questao = Question.objects.create(
        exam=exam,
        type=tipo,
        text=enunciado,
        points=valor,
        required=bool(required),
        order=order if order is not None else _proxima_ordem(exam),
        internal_explanation=(internal_explanation or "").strip(),
        active=bool(active),
    )
    _gravar_alternativas(
        questao, opcoes=opcoes, resposta_verdadeira=resposta_verdadeira
    )

    erros = erros_da_questao(questao)
    if erros:
        raise DomainError(erros)

    _auditar(questao, AuditEvent.QUESTION_CREATED, actor=actor, request=request)
    return questao


@transaction.atomic
def update_question(
    questao,
    *,
    type,
    text,
    points,
    required=True,
    order=None,
    internal_explanation="",
    active=True,
    opcoes=None,
    resposta_verdadeira=None,
    actor=None,
    request=None,
):
    exigir_estrutura_editavel(questao.exam, "editar questao")

    tipo = _validar_tipo(type)
    valor = _validar_valor(points)
    enunciado = (text or "").strip()
    if not enunciado:
        raise DomainError("O enunciado da questao e obrigatorio.")

    questao.type = tipo
    questao.text = enunciado
    questao.points = valor
    questao.required = bool(required)
    questao.active = bool(active)
    questao.internal_explanation = (internal_explanation or "").strip()
    if order is not None:
        questao.order = order
    questao.save()

    _gravar_alternativas(
        questao, opcoes=opcoes, resposta_verdadeira=resposta_verdadeira
    )

    erros = erros_da_questao(questao)
    if erros:
        raise DomainError(erros)

    _auditar(questao, AuditEvent.QUESTION_UPDATED, actor=actor, request=request)
    return questao


@transaction.atomic
def delete_question(questao, *, actor=None, request=None):
    """
    Remove a questao e as suas alternativas.

    Exclusao fisica so acontece com a prova em rascunho, quando nada foi
    aplicado ainda. Quando existirem tentativas de alunos, a Etapa 4 vai
    reforcar o bloqueio: apagar uma questao ja respondida destruiria a
    resposta junto.
    """
    exigir_estrutura_editavel(questao.exam, "excluir questao")

    identificacao = {
        "exam_id": questao.exam_id,
        "type": questao.type,
        "order": questao.order,
        "points": str(questao.points),
    }
    pk = questao.pk
    questao.delete()

    record(
        AuditEvent.QUESTION_DELETED,
        request=request,
        actor=actor,
        entity_type="Question",
        entity_id=pk,
        metadata=identificacao,
    )
    return pk


@transaction.atomic
def reorder_questions(exam, ordens, *, actor=None, request=None):
    """
    Aplica uma nova ordem as questoes da prova.

    `ordens` mapeia id da questao para a posicao desejada. Ids que nao
    pertencam a esta prova sao ignorados: a reordenacao vem de um formulario,
    e um id de outra prova ali dentro nao pode virar escrita.
    """
    exigir_estrutura_editavel(exam, "reordenar questoes")

    validas = {questao.pk: questao for questao in exam.questions.all()}
    alteradas = []

    for identificador, posicao in (ordens or {}).items():
        questao = validas.get(int(identificador))
        if questao is None:
            continue
        posicao = int(posicao)
        if posicao < 0:
            raise DomainError("A ordem nao pode ser negativa.")
        if questao.order != posicao:
            questao.order = posicao
            alteradas.append(questao)

    if alteradas:
        Question.objects.bulk_update(alteradas, ["order"])

    return len(alteradas)
