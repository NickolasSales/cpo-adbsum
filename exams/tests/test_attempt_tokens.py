"""
Tokens, ordem sorteada e concorrencia no inicio.

Os tres assuntos andam juntos porque nascem no mesmo instante: a montagem da
tentativa. Se ela rodar duas vezes, o aluno ganha dois conjuntos de tokens e
perde as respostas do primeiro; se rodar concorrentemente, ganha duas
tentativas.

Sobre testar sorteio
--------------------
Nenhum teste aqui exige que a ordem sorteada seja diferente da original.
Embaralhar cinco itens devolve a ordem original uma vez a cada 120, e um
teste que exigisse diferenca falharia sozinho de vez em quando — o pior tipo
de teste, porque ensina a equipe a reexecutar a suite ate passar.

O que da para exigir com certeza: todo item aparece exatamente uma vez, as
posicoes formam uma sequencia sem buracos, e o resultado fica gravado.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection

from exams.models import (
    AttemptOption,
    AttemptQuestion,
    Exam,
    ExamAttempt,
    QuestionType,
    TEXTO_FALSO,
    TEXTO_VERDADEIRO,
)
from exams.services import start_attempt

pytestmark = pytest.mark.django_db


def ordem_das_questoes(tentativa):
    return list(
        AttemptQuestion.objects.filter(attempt=tentativa)
        .order_by("display_order")
        .values_list("question_id", flat=True)
    )


def tokens_das_questoes(tentativa):
    return list(
        AttemptQuestion.objects.filter(attempt=tentativa)
        .order_by("display_order")
        .values_list("public_token", flat=True)
    )


# ---------------------------------------------------------------------------
# Formato e unicidade
# ---------------------------------------------------------------------------


def test_todo_token_e_um_uuid_versao_quatro(tentativa):
    for linha in AttemptQuestion.objects.filter(attempt=tentativa):
        assert isinstance(linha.public_token, uuid.UUID)
        assert linha.public_token.version == 4

    for alternativa in AttemptOption.objects.filter(
        attempt_question__attempt=tentativa
    ):
        assert alternativa.public_token.version == 4


def test_tokens_de_questao_e_de_alternativa_nao_colidem(tentativa):
    de_questao = set(tokens_das_questoes(tentativa))
    de_alternativa = set(
        AttemptOption.objects.filter(
            attempt_question__attempt=tentativa
        ).values_list("public_token", flat=True)
    )

    assert de_questao & de_alternativa == set()
    assert len(de_questao) == 5


def test_todos_os_tokens_da_tentativa_sao_distintos(tentativa):
    todos = list(tokens_das_questoes(tentativa)) + list(
        AttemptOption.objects.filter(
            attempt_question__attempt=tentativa
        ).values_list("public_token", flat=True)
    )
    assert len(todos) == len(set(todos))


# ---------------------------------------------------------------------------
# Tokens sao por tentativa
# ---------------------------------------------------------------------------


def test_alunos_diferentes_recebem_tokens_diferentes_para_a_mesma_questao(
    prova_aberta, aluno_matriculado, outro_student, modulo
):
    """
    A propriedade que impede a combinacao entre alunos.

    Se o token fosse da questao, e nao da tentativa, bastaria um aluno passar
    "marque a opcao 4f2a..." para o colega. Como cada tentativa tem os seus,
    esse token simplesmente nao existe na tentativa do outro — e o autosave o
    recusa como qualquer token inventado.
    """
    from courses.services import create_enrollment

    create_enrollment(student=outro_student, module=modulo)

    primeira = start_attempt(aluno_matriculado, prova_aberta)
    segunda = start_attempt(outro_student, prova_aberta)

    questao = prova_aberta.questions.get(type=QuestionType.SINGLE_CHOICE)

    token_a = AttemptQuestion.objects.get(
        attempt=primeira, question=questao
    ).public_token
    token_b = AttemptQuestion.objects.get(
        attempt=segunda, question=questao
    ).public_token
    assert token_a != token_b

    opcao = questao.options.first()
    alternativa_a = AttemptOption.objects.get(
        attempt_question__attempt=primeira, option=opcao
    ).public_token
    alternativa_b = AttemptOption.objects.get(
        attempt_question__attempt=segunda, option=opcao
    ).public_token
    assert alternativa_a != alternativa_b


def test_tentativas_diferentes_do_mesmo_aluno_tem_tokens_diferentes(
    prova_aberta, aluno_matriculado
):
    from exams.services import expire_attempt

    Exam.objects.filter(pk=prova_aberta.pk).update(max_attempts=2)
    prova_aberta.refresh_from_db()

    primeira = start_attempt(aluno_matriculado, prova_aberta)
    expire_attempt(primeira, agora=primeira.expires_at)
    segunda = start_attempt(aluno_matriculado, prova_aberta)

    assert set(tokens_das_questoes(primeira)) & set(
        tokens_das_questoes(segunda)
    ) == set()


# ---------------------------------------------------------------------------
# Persistencia: o F5 nao muda nada
# ---------------------------------------------------------------------------


def test_reler_a_tentativa_devolve_os_mesmos_tokens_e_a_mesma_ordem(tentativa):
    """
    Se os tokens fossem gerados a cada request, o F5 trocaria todos eles e
    apagaria a ligacao com as respostas ja salvas.
    """
    primeira = tokens_das_questoes(tentativa)
    ordem_inicial = ordem_das_questoes(tentativa)

    for _ in range(3):
        assert tokens_das_questoes(tentativa) == primeira
        assert ordem_das_questoes(tentativa) == ordem_inicial


def test_retomar_a_tentativa_devolve_os_mesmos_tokens(
    prova_aberta, aluno_matriculado
):
    primeira = start_attempt(aluno_matriculado, prova_aberta)
    tokens_iniciais = tokens_das_questoes(primeira)

    retomada = start_attempt(aluno_matriculado, prova_aberta)

    assert retomada.pk == primeira.pk
    assert tokens_das_questoes(retomada) == tokens_iniciais


def test_retomar_nao_duplica_questoes_da_tentativa(prova_aberta, aluno_matriculado):
    start_attempt(aluno_matriculado, prova_aberta)
    start_attempt(aluno_matriculado, prova_aberta)

    assert AttemptQuestion.objects.count() == 5
    assert AttemptOption.objects.count() == 8


# ---------------------------------------------------------------------------
# Ordem sem sorteio
# ---------------------------------------------------------------------------


def test_sem_sorteio_a_ordem_e_a_cadastrada(tentativa, prova_aberta):
    esperada = list(
        prova_aberta.questions.filter(active=True)
        .order_by("order", "id")
        .values_list("id", flat=True)
    )
    assert ordem_das_questoes(tentativa) == esperada


def test_sem_sorteio_as_alternativas_seguem_a_ordem_cadastrada(
    tentativa, prova_aberta
):
    questao = prova_aberta.questions.get(type=QuestionType.SINGLE_CHOICE)
    linha = AttemptQuestion.objects.get(attempt=tentativa, question=questao)

    esperada = list(questao.options.order_by("order", "id").values_list("id", flat=True))
    obtida = list(
        AttemptOption.objects.filter(attempt_question=linha)
        .order_by("display_order")
        .values_list("option_id", flat=True)
    )
    assert obtida == esperada


# ---------------------------------------------------------------------------
# Ordem com sorteio
# ---------------------------------------------------------------------------


def test_com_sorteio_todas_as_questoes_aparecem_uma_vez(
    prova_aberta, aluno_matriculado
):
    Exam.objects.filter(pk=prova_aberta.pk).update(randomize_questions=True)
    prova_aberta.refresh_from_db()

    tentativa = start_attempt(aluno_matriculado, prova_aberta)

    esperadas = set(
        prova_aberta.questions.filter(active=True).values_list("id", flat=True)
    )
    obtidas = ordem_das_questoes(tentativa)

    assert sorted(obtidas) == sorted(esperadas)
    assert len(obtidas) == len(set(obtidas))


def test_com_sorteio_as_posicoes_continuam_uma_sequencia_sem_buracos(
    prova_aberta, aluno_matriculado
):
    Exam.objects.filter(pk=prova_aberta.pk).update(randomize_questions=True)
    prova_aberta.refresh_from_db()

    tentativa = start_attempt(aluno_matriculado, prova_aberta)

    posicoes = sorted(
        AttemptQuestion.objects.filter(attempt=tentativa).values_list(
            "display_order", flat=True
        )
    )
    assert posicoes == list(range(5))


def test_com_sorteio_a_ordem_fica_gravada(prova_aberta, aluno_matriculado):
    """
    O sorteio acontece uma vez, no start. Reler precisa devolver sempre a
    mesma ordem, senao o aluno veria as questoes trocarem de lugar a cada F5.
    """
    Exam.objects.filter(pk=prova_aberta.pk).update(randomize_questions=True)
    prova_aberta.refresh_from_db()

    tentativa = start_attempt(aluno_matriculado, prova_aberta)
    ordem = ordem_das_questoes(tentativa)

    for _ in range(5):
        assert ordem_das_questoes(tentativa) == ordem


def test_com_sorteio_de_alternativas_todas_aparecem_uma_vez(
    prova_aberta, aluno_matriculado
):
    Exam.objects.filter(pk=prova_aberta.pk).update(randomize_options=True)
    prova_aberta.refresh_from_db()

    tentativa = start_attempt(aluno_matriculado, prova_aberta)
    questao = prova_aberta.questions.get(type=QuestionType.MULTIPLE_CHOICE)
    linha = AttemptQuestion.objects.get(attempt=tentativa, question=questao)

    esperadas = set(questao.options.values_list("id", flat=True))
    obtidas = list(
        AttemptOption.objects.filter(attempt_question=linha)
        .order_by("display_order")
        .values_list("option_id", flat=True)
    )

    assert sorted(obtidas) == sorted(esperadas)
    assert list(range(len(obtidas))) == sorted(
        AttemptOption.objects.filter(attempt_question=linha).values_list(
            "display_order", flat=True
        )
    )


def test_verdadeiro_falso_mantem_a_ordem_canonica_mesmo_com_sorteio(
    prova_aberta, aluno_matriculado
):
    """
    Decisao explicita: Verdadeiro vem antes de Falso, sempre.

    Sao duas alternativas de significado fixo. Inverter a posicao delas nao
    esconde nada de ninguem — quem sabe a resposta continua sabendo — e so
    torna a leitura mais lenta, especialmente no celular, onde o aluno le
    rapido e marca. O sorteio existe para dificultar a cola entre vizinhos, e
    isso nao se aplica a um par fixo de duas opcoes.
    """
    Exam.objects.filter(pk=prova_aberta.pk).update(
        randomize_options=True, randomize_questions=True
    )
    prova_aberta.refresh_from_db()

    tentativa = start_attempt(aluno_matriculado, prova_aberta)
    questao = prova_aberta.questions.get(type=QuestionType.TRUE_FALSE)
    linha = AttemptQuestion.objects.get(attempt=tentativa, question=questao)

    textos = list(
        AttemptOption.objects.filter(attempt_question=linha)
        .order_by("display_order")
        .values_list("option__text", flat=True)
    )
    assert textos == [TEXTO_VERDADEIRO, TEXTO_FALSO]


def test_sorteio_de_alternativas_nao_afeta_questoes_textuais(
    prova_aberta, aluno_matriculado
):
    Exam.objects.filter(pk=prova_aberta.pk).update(randomize_options=True)
    prova_aberta.refresh_from_db()

    tentativa = start_attempt(aluno_matriculado, prova_aberta)

    for tipo in (QuestionType.SHORT_TEXT, QuestionType.ESSAY):
        questao = prova_aberta.questions.get(type=tipo)
        linha = AttemptQuestion.objects.get(attempt=tentativa, question=questao)
        assert AttemptOption.objects.filter(attempt_question=linha).count() == 0


def test_questao_inativa_fica_de_fora_da_tentativa(
    prova_aberta, aluno_matriculado
):
    from exams.models import Question

    questao = prova_aberta.questions.get(type=QuestionType.ESSAY)
    Question.objects.filter(pk=questao.pk).update(active=False)

    tentativa = start_attempt(aluno_matriculado, prova_aberta)

    assert AttemptQuestion.objects.filter(attempt=tentativa).count() == 4
    assert not AttemptQuestion.objects.filter(
        attempt=tentativa, question=questao
    ).exists()


# ---------------------------------------------------------------------------
# Concorrencia no inicio
# ---------------------------------------------------------------------------


def test_o_start_trava_a_linha_do_aluno(prova_aberta, aluno_matriculado):
    """
    Checagem barata e deterministica de que a trava existe.

    O aluno precisa ser lido com SELECT ... FOR UPDATE antes de a tentativa
    aberta ser procurada e o numero calculado. Sem isso, dois cliques
    simultaneos leriam o mesmo estado e criariam duas tentativas.
    """
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as consultas:
        start_attempt(aluno_matriculado, prova_aberta)

    sql = " ".join(consulta["sql"].upper() for consulta in consultas)
    assert "FOR UPDATE" in sql


@pytest.mark.django_db(transaction=True)
def test_dois_starts_simultaneos_criam_uma_unica_tentativa(
    prova_aberta, aluno_matriculado
):
    """
    Teste concorrencial de verdade: duas threads iniciando a mesma prova.

    E o cenario do duplo clique, do celular junto com o notebook, e da aba
    duplicada. Com a trava sobre o aluno, a segunda thread espera e enxerga a
    tentativa que a primeira acabou de criar, entao as duas devolvem o mesmo
    public_id.

    Sem a trava as duas calculariam attempt_number=1 e a constraint parcial
    uniq_tentativa_em_andamento derrubaria uma delas com IntegrityError — o
    aluno veria erro 500 no momento mais tenso do dia.

    Cada thread precisa da propria conexao, e por isso fecha a sua no fim; e
    por isso tambem que o teste roda com transaction=True.
    """

    def iniciar():
        try:
            return str(start_attempt(aluno_matriculado, prova_aberta).public_id)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futuros = [executor.submit(iniciar) for _ in range(2)]
        resultados = [futuro.result(timeout=30) for futuro in futuros]

    assert ExamAttempt.objects.count() == 1
    assert resultados[0] == resultados[1]
    assert AttemptQuestion.objects.count() == 5


@pytest.mark.django_db(transaction=True)
def test_quatro_starts_simultaneos_continuam_criando_uma_so(
    prova_aberta, aluno_matriculado
):
    """
    O mesmo cenario com mais pressao. Duas threads podem passar por sorte de
    escalonamento; quatro tornam a coincidencia improvavel.
    """

    def iniciar():
        try:
            return str(start_attempt(aluno_matriculado, prova_aberta).public_id)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=4) as executor:
        futuros = [executor.submit(iniciar) for _ in range(4)]
        resultados = {futuro.result(timeout=30) for futuro in futuros}

    assert ExamAttempt.objects.count() == 1
    assert len(resultados) == 1
