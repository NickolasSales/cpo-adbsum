"""
Questoes: os cinco tipos e as regras de estrutura de cada um.

A validacao roda sobre o que foi gravado, dentro da transacao do servico.
Estrutura invalida nao deixa rastro: o teste confere tambem que a questao nao
ficou no banco pela metade.
"""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from common.exceptions import DomainError
from exams.models import (
    Question,
    QuestionOption,
    QuestionType,
    TEXTO_FALSO,
    TEXTO_VERDADEIRO,
)
from exams.services import create_question, delete_question, reorder_questions, update_question

pytestmark = pytest.mark.django_db


def criar(prova, admin_user, **kwargs):
    padrao = {
        "type": QuestionType.SINGLE_CHOICE,
        "text": "Enunciado",
        "points": "1.00",
        "actor": admin_user,
    }
    padrao.update(kwargs)
    return create_question(prova, **padrao)


# ---------------------------------------------------------------------------
# Campos comuns
# ---------------------------------------------------------------------------


def test_enunciado_recebe_trim(prova, admin_user):
    questao = criar(
        prova,
        admin_user,
        type=QuestionType.ESSAY,
        text="   Disserte sobre o tema.   ",
    )
    assert questao.text == "Disserte sobre o tema."


@pytest.mark.parametrize("texto", ["", "   "])
def test_enunciado_vazio_e_recusado(prova, admin_user, texto):
    with pytest.raises(DomainError) as erro:
        criar(prova, admin_user, type=QuestionType.ESSAY, text=texto)
    assert "enunciado" in str(erro.value).lower()


@pytest.mark.parametrize("valor", ["0", "0.00", "-1", "-0.01"])
def test_valor_zero_ou_negativo_e_recusado(prova, admin_user, valor):
    with pytest.raises(DomainError) as erro:
        criar(prova, admin_user, type=QuestionType.ESSAY, points=valor)
    assert "maior que zero" in str(erro.value).lower()
    assert prova.questions.count() == 0


def test_valor_nao_numerico_e_recusado(prova, admin_user):
    with pytest.raises(DomainError):
        criar(prova, admin_user, type=QuestionType.ESSAY, points="muito")


def test_valor_e_decimal_e_nao_float(prova, admin_user):
    questao = criar(prova, admin_user, type=QuestionType.ESSAY, points="2.50")
    questao.refresh_from_db()
    assert isinstance(questao.points, Decimal)
    assert questao.points == Decimal("2.50")


def test_valor_zero_e_recusado_pelo_banco(prova, admin_user):
    """Contraprova: a constraint protege caminhos que nao passam pelo servico."""
    questao = criar(prova, admin_user, type=QuestionType.ESSAY, points="1.00")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Question.objects.filter(pk=questao.pk).update(points=Decimal("0.00"))


def test_tipo_invalido_e_recusado(prova, admin_user):
    with pytest.raises(DomainError) as erro:
        criar(prova, admin_user, type="TIPO_QUE_NAO_EXISTE")
    assert "tipo" in str(erro.value).lower()


def test_ordem_automatica_coloca_no_fim(prova, admin_user):
    primeira = criar(prova, admin_user, type=QuestionType.ESSAY, text="A")
    segunda = criar(prova, admin_user, type=QuestionType.ESSAY, text="B")
    terceira = criar(prova, admin_user, type=QuestionType.ESSAY, text="C")

    assert [primeira.order, segunda.order, terceira.order] == [1, 2, 3]


def test_ordenacao_usa_o_campo_ordem_e_nao_o_id(prova, admin_user):
    ultima = criar(prova, admin_user, type=QuestionType.ESSAY, text="Fim", order=99)
    primeira = criar(prova, admin_user, type=QuestionType.ESSAY, text="Inicio", order=1)

    ordenadas = list(prova.questions.all())
    assert ordenadas[0].pk == primeira.pk
    assert ordenadas[-1].pk == ultima.pk


def test_reordenar_questoes(prova, admin_user):
    a = criar(prova, admin_user, type=QuestionType.ESSAY, text="A", order=1)
    b = criar(prova, admin_user, type=QuestionType.ESSAY, text="B", order=2)

    alteradas = reorder_questions(prova, {a.pk: 2, b.pk: 1}, actor=admin_user)
    a.refresh_from_db()
    b.refresh_from_db()

    assert alteradas == 2
    assert (a.order, b.order) == (2, 1)


def test_reordenar_ignora_questao_de_outra_prova(prova, outro_modulo, admin_user):
    """
    A ordem vem de um formulario; um id de outra prova ali dentro nao pode
    virar escrita.
    """
    from exams.services import create_exam

    outra_prova = create_exam(
        module=outro_modulo, title="Outra prova", actor=admin_user
    )
    de_outra = criar(outra_prova, admin_user, type=QuestionType.ESSAY, text="Alheia")
    ordem_original = de_outra.order

    reorder_questions(prova, {de_outra.pk: 42}, actor=admin_user)

    de_outra.refresh_from_db()
    assert de_outra.order == ordem_original


# ---------------------------------------------------------------------------
# SINGLE_CHOICE
# ---------------------------------------------------------------------------


def test_escolha_unica_com_duas_opcoes_e_uma_correta_e_valida(prova, admin_user):
    questao = criar(
        prova,
        admin_user,
        type=QuestionType.SINGLE_CHOICE,
        opcoes=[
            {"text": "Certa", "is_correct": True},
            {"text": "Errada", "is_correct": False},
        ],
    )
    assert questao.options.count() == 2
    assert questao.options.corretas().count() == 1


def test_escolha_unica_sem_correta_e_invalida(prova, admin_user):
    with pytest.raises(DomainError) as erro:
        criar(
            prova,
            admin_user,
            type=QuestionType.SINGLE_CHOICE,
            opcoes=[
                {"text": "A", "is_correct": False},
                {"text": "B", "is_correct": False},
            ],
        )
    assert "correta" in str(erro.value).lower()
    assert prova.questions.count() == 0


def test_escolha_unica_com_duas_corretas_e_invalida(prova, admin_user):
    with pytest.raises(DomainError) as erro:
        criar(
            prova,
            admin_user,
            type=QuestionType.SINGLE_CHOICE,
            opcoes=[
                {"text": "A", "is_correct": True},
                {"text": "B", "is_correct": True},
            ],
        )
    assert "escolha unica" in str(erro.value).lower()
    assert prova.questions.count() == 0


def test_escolha_unica_com_uma_opcao_e_invalida(prova, admin_user):
    with pytest.raises(DomainError) as erro:
        criar(
            prova,
            admin_user,
            type=QuestionType.SINGLE_CHOICE,
            opcoes=[{"text": "Unica", "is_correct": True}],
        )
    assert "2 alternativas" in str(erro.value)
    assert prova.questions.count() == 0


def test_escolha_unica_sem_nenhuma_opcao_e_invalida(prova, admin_user):
    with pytest.raises(DomainError):
        criar(prova, admin_user, type=QuestionType.SINGLE_CHOICE, opcoes=[])
    assert prova.questions.count() == 0


def test_alternativa_em_branco_e_descartada(prova, admin_user):
    questao = criar(
        prova,
        admin_user,
        type=QuestionType.SINGLE_CHOICE,
        opcoes=[
            {"text": "Certa", "is_correct": True},
            {"text": "Errada", "is_correct": False},
            {"text": "   ", "is_correct": False},
            {"text": "", "is_correct": False},
        ],
    )
    assert questao.options.count() == 2


# ---------------------------------------------------------------------------
# MULTIPLE_CHOICE
# ---------------------------------------------------------------------------


def test_multipla_com_duas_corretas_e_uma_errada_e_valida(prova, admin_user):
    questao = criar(
        prova,
        admin_user,
        type=QuestionType.MULTIPLE_CHOICE,
        opcoes=[
            {"text": "A", "is_correct": True},
            {"text": "B", "is_correct": True},
            {"text": "C", "is_correct": False},
        ],
    )
    assert questao.options.corretas().count() == 2


def test_multipla_sem_correta_e_invalida(prova, admin_user):
    with pytest.raises(DomainError) as erro:
        criar(
            prova,
            admin_user,
            type=QuestionType.MULTIPLE_CHOICE,
            opcoes=[
                {"text": "A", "is_correct": False},
                {"text": "B", "is_correct": False},
            ],
        )
    assert "correta" in str(erro.value).lower()
    assert prova.questions.count() == 0


def test_multipla_com_todas_corretas_e_invalida(prova, admin_user):
    """
    Uma questao em que tudo esta certo nao mede nada, e na correcao
    transformaria qualquer marcacao em acerto.
    """
    with pytest.raises(DomainError) as erro:
        criar(
            prova,
            admin_user,
            type=QuestionType.MULTIPLE_CHOICE,
            opcoes=[
                {"text": "A", "is_correct": True},
                {"text": "B", "is_correct": True},
                {"text": "C", "is_correct": True},
            ],
        )
    assert "todas as alternativas" in str(erro.value).lower()
    assert prova.questions.count() == 0


def test_multipla_com_uma_opcao_e_invalida(prova, admin_user):
    with pytest.raises(DomainError) as erro:
        criar(
            prova,
            admin_user,
            type=QuestionType.MULTIPLE_CHOICE,
            opcoes=[{"text": "Unica", "is_correct": True}],
        )
    assert "2 alternativas" in str(erro.value)


# ---------------------------------------------------------------------------
# TRUE_FALSE
# ---------------------------------------------------------------------------


def test_verdadeiro_falso_cria_as_duas_opcoes_automaticamente(prova, admin_user):
    questao = criar(
        prova,
        admin_user,
        type=QuestionType.TRUE_FALSE,
        text="A Terra e redonda.",
        resposta_verdadeira=True,
    )
    opcoes = list(questao.options.order_by("order"))

    assert len(opcoes) == 2
    assert [opcao.text for opcao in opcoes] == [TEXTO_VERDADEIRO, TEXTO_FALSO]
    assert opcoes[0].is_correct is True
    assert opcoes[1].is_correct is False


def test_verdadeiro_falso_com_resposta_falso(prova, admin_user):
    questao = criar(
        prova,
        admin_user,
        type=QuestionType.TRUE_FALSE,
        text="A Terra e plana.",
        resposta_verdadeira=False,
    )
    corretas = list(questao.options.corretas())
    assert len(corretas) == 1
    assert corretas[0].text == TEXTO_FALSO


def test_verdadeiro_falso_sem_escolher_a_resposta_e_recusada(prova, admin_user):
    with pytest.raises(DomainError) as erro:
        criar(
            prova,
            admin_user,
            type=QuestionType.TRUE_FALSE,
            text="Sem gabarito",
            resposta_verdadeira=None,
        )
    assert "verdadeiro ou falso" in str(erro.value).lower()
    assert prova.questions.count() == 0


def test_verdadeiro_falso_ignora_alternativas_enviadas(prova, admin_user):
    """
    Os textos sao fixos. Mandar "Opcao A" e "Opcao B" nao muda nada: o
    servico monta as duas alternativas por conta propria.
    """
    questao = criar(
        prova,
        admin_user,
        type=QuestionType.TRUE_FALSE,
        text="Enunciado",
        resposta_verdadeira=True,
        opcoes=[
            {"text": "Opcao A", "is_correct": True},
            {"text": "Opcao B", "is_correct": False},
        ],
    )
    textos = sorted(opcao.text for opcao in questao.options.all())
    assert textos == sorted([TEXTO_VERDADEIRO, TEXTO_FALSO])


def test_verdadeiro_falso_com_texto_adulterado_no_banco_fica_invalida(prova, admin_user):
    from exams.services import erros_da_questao

    questao = criar(
        prova,
        admin_user,
        type=QuestionType.TRUE_FALSE,
        text="Enunciado",
        resposta_verdadeira=True,
    )
    QuestionOption.objects.filter(question=questao, text=TEXTO_VERDADEIRO).update(
        text="Opcao A"
    )

    erros = erros_da_questao(questao)
    assert any("Verdadeiro" in erro for erro in erros)


# ---------------------------------------------------------------------------
# SHORT_TEXT e ESSAY
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tipo", [QuestionType.SHORT_TEXT, QuestionType.ESSAY]
)
def test_correcao_manual_sem_alternativas_e_valida(prova, admin_user, tipo):
    questao = criar(prova, admin_user, type=tipo, text="Responda.")
    assert questao.options.count() == 0
    assert questao.correcao_manual is True


@pytest.mark.parametrize(
    "tipo", [QuestionType.SHORT_TEXT, QuestionType.ESSAY]
)
def test_correcao_manual_descarta_alternativas_enviadas(prova, admin_user, tipo):
    """A interface nem oferece o campo; se vier, e ignorado."""
    questao = criar(
        prova,
        admin_user,
        type=tipo,
        text="Responda.",
        opcoes=[{"text": "A", "is_correct": True}],
    )
    assert questao.options.count() == 0


@pytest.mark.parametrize(
    "tipo", [QuestionType.SHORT_TEXT, QuestionType.ESSAY]
)
def test_correcao_manual_com_alternativa_no_banco_fica_invalida(prova, admin_user, tipo):
    """
    Estrutura inconsistente vinda de fora do servico. A validacao precisa
    detectar, porque e ela que barra a publicacao.
    """
    from exams.services import erros_da_questao

    questao = criar(prova, admin_user, type=tipo, text="Responda.")
    QuestionOption.objects.create(question=questao, text="Intrusa", order=1)

    erros = erros_da_questao(questao)
    assert any("correcao manual" in erro.lower() for erro in erros)


# ---------------------------------------------------------------------------
# Edicao e exclusao
# ---------------------------------------------------------------------------


def test_edicao_troca_o_gabarito(prova, admin_user):
    questao = criar(
        prova,
        admin_user,
        type=QuestionType.SINGLE_CHOICE,
        opcoes=[
            {"text": "A", "is_correct": True},
            {"text": "B", "is_correct": False},
        ],
    )
    update_question(
        questao,
        type=QuestionType.SINGLE_CHOICE,
        text=questao.text,
        points=questao.points,
        opcoes=[
            {"text": "A", "is_correct": False},
            {"text": "B", "is_correct": True},
        ],
        actor=admin_user,
    )
    corretas = list(questao.options.corretas())
    assert len(corretas) == 1
    assert corretas[0].text == "B"


def test_edicao_invalida_nao_deixa_a_questao_sem_gabarito(prova, admin_user):
    """
    A gravacao das alternativas e a validacao acontecem na mesma transacao.
    Se a nova estrutura for invalida, a antiga permanece intacta.
    """
    questao = criar(
        prova,
        admin_user,
        type=QuestionType.SINGLE_CHOICE,
        opcoes=[
            {"text": "A", "is_correct": True},
            {"text": "B", "is_correct": False},
        ],
    )
    with pytest.raises(DomainError):
        update_question(
            questao,
            type=QuestionType.SINGLE_CHOICE,
            text=questao.text,
            points=questao.points,
            opcoes=[
                {"text": "A", "is_correct": False},
                {"text": "B", "is_correct": False},
            ],
            actor=admin_user,
        )

    questao.refresh_from_db()
    assert questao.options.corretas().count() == 1


def test_edicao_troca_o_tipo_e_reconstroi_as_alternativas(prova, admin_user):
    questao = criar(
        prova,
        admin_user,
        type=QuestionType.SINGLE_CHOICE,
        opcoes=[
            {"text": "A", "is_correct": True},
            {"text": "B", "is_correct": False},
        ],
    )
    update_question(
        questao,
        type=QuestionType.ESSAY,
        text="Agora e dissertativa.",
        points="3.00",
        actor=admin_user,
    )
    questao.refresh_from_db()

    assert questao.type == QuestionType.ESSAY
    assert questao.options.count() == 0


def test_exclusao_remove_questao_e_alternativas(prova, admin_user):
    questao = criar(
        prova,
        admin_user,
        type=QuestionType.SINGLE_CHOICE,
        opcoes=[
            {"text": "A", "is_correct": True},
            {"text": "B", "is_correct": False},
        ],
    )
    ids_opcoes = list(questao.options.values_list("pk", flat=True))

    delete_question(questao, actor=admin_user)

    assert not Question.objects.filter(pk=questao.pk).exists()
    assert not QuestionOption.objects.filter(pk__in=ids_opcoes).exists()
