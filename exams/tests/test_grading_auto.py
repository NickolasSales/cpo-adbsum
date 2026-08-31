"""
Correcao automatica das questoes objetivas.

Regra unica nos tres tipos: tudo ou nada. Nao ha pontuacao parcial, e isso e
decisao de negocio — meio ponto por acertar metade de uma multipla escolha
premiaria quem marca tudo, que e exatamente o comportamento que a regra
"exact set" existe para desencorajar.

O gabarito e lido no servidor, de QuestionOption.is_correct. Ele nunca esteve
dentro da tentativa: AttemptOption guarda a referencia e a posicao, e nao a
resposta certa. Por isso um aluno com o HTML da prova na mao nao tem como
inferir nada — e por isso a correcao precisa voltar ate a Question.
"""

from decimal import Decimal

import pytest
from django.utils import timezone

from exams.models import (
    AttemptQuestion,
    AttemptResult,
    ExamAttempt,
    GradingStatus,
    QuestionGradingStatus,
    QuestionType,
)
from exams.services import (
    autosave_answer,
    finalize_grading,
    grade_objective_questions,
    start_attempt,
    submit_attempt,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------


def linha_do_tipo(tentativa, tipo):
    return (
        AttemptQuestion.objects.filter(attempt=tentativa, question__type=tipo)
        .select_related("question")
        .first()
    )


def alternativas(linha):
    """As alternativas como o aluno as viu, com o gabarito ao lado."""
    return sorted(
        linha.options.select_related("option").all(), key=lambda o: o.display_order
    )


def corretas(linha):
    return [a for a in alternativas(linha) if a.option.is_correct]


def erradas(linha):
    return [a for a in alternativas(linha) if not a.option.is_correct]


def marcar(tentativa, linha, opcoes):
    autosave_answer(
        tentativa,
        question_token=str(linha.public_token),
        option_tokens=[str(o.public_token) for o in opcoes],
    )


def pontos(linha):
    linha.refresh_from_db()
    return linha.awarded_points


def responder_resto(tentativa, alvo):
    """
    Preenche todas as questoes MENOS a que o teste esta exercitando.

    O envio exige as obrigatorias respondidas, e todas as questoes da fixture
    sao obrigatorias. Sem isto, cada teste morreria em ObrigatoriasPendentes
    antes de chegar na correcao — que e o que ele quer medir.

    As objetivas ficam CERTAS de proposito: assim a nota das outras questoes
    nao interfere no que o teste observa na questao alvo.
    """
    for linha in tentativa.questions.select_related("question").all():
        if linha.pk == alvo.pk:
            continue
        if linha.question.type in {QuestionType.SHORT_TEXT, QuestionType.ESSAY}:
            autosave_answer(
                tentativa, question_token=str(linha.public_token), text="resposta"
            )
        else:
            marcar(tentativa, linha, corretas(linha))


def tornar_opcional(linha):
    """
    Marca a questao alvo como nao obrigatoria.

    Os testes de "em branco" precisam ENVIAR a prova com aquela questao vazia,
    e o envio recusa obrigatoria em branco — a regra da Etapa 4. Sem isto o
    teste morreria antes de chegar na correcao, medindo a validacao de envio
    em vez da regra que ele quer verificar.
    """
    from exams.models import Question

    Question.objects.filter(pk=linha.question_id).update(required=False)


def responder_texto(tentativa, texto="resposta"):
    for linha in tentativa.questions.select_related("question").all():
        if linha.question.type in {QuestionType.SHORT_TEXT, QuestionType.ESSAY}:
            autosave_answer(
                tentativa, question_token=str(linha.public_token), text=texto
            )


@pytest.fixture
def tentativa_enviada(tentativa):
    """Tentativa com tudo respondido corretamente e ja enviada."""
    for linha in tentativa.questions.select_related("question").all():
        if linha.question.type in {QuestionType.SHORT_TEXT, QuestionType.ESSAY}:
            autosave_answer(
                tentativa, question_token=str(linha.public_token), text="ok"
            )
        else:
            marcar(tentativa, linha, corretas(linha))
    return submit_attempt(tentativa)


# ---------------------------------------------------------------------------
# SINGLE_CHOICE
# ---------------------------------------------------------------------------


def test_escolha_unica_correta_vale_tudo(tentativa):
    linha = linha_do_tipo(tentativa, QuestionType.SINGLE_CHOICE)
    marcar(tentativa, linha, corretas(linha))
    responder_resto(tentativa, linha)

    submit_attempt(tentativa)

    assert pontos(linha) == linha.points_snapshot


def test_escolha_unica_errada_vale_zero(tentativa):
    linha = linha_do_tipo(tentativa, QuestionType.SINGLE_CHOICE)
    marcar(tentativa, linha, [erradas(linha)[0]])
    responder_resto(tentativa, linha)

    submit_attempt(tentativa)

    assert pontos(linha) == Decimal("0.00")


def test_escolha_unica_em_branco_vale_zero(tentativa):
    """
    Nao respondeu e errou valem o mesmo.

    O aluno que deixou em branco nao demonstrou o conhecimento, e nao ha
    diferenca de merito entre nao saber e nao tentar.
    """
    linha = linha_do_tipo(tentativa, QuestionType.SINGLE_CHOICE)
    tornar_opcional(linha)
    responder_resto(tentativa, linha)

    submit_attempt(tentativa)

    assert pontos(linha) == Decimal("0.00")
    linha.refresh_from_db()
    assert linha.grading_status == QuestionGradingStatus.AUTO_GRADED


# ---------------------------------------------------------------------------
# TRUE_FALSE
# ---------------------------------------------------------------------------


def test_verdadeiro_falso_correto_vale_tudo(tentativa):
    linha = linha_do_tipo(tentativa, QuestionType.TRUE_FALSE)
    marcar(tentativa, linha, corretas(linha))
    responder_resto(tentativa, linha)

    submit_attempt(tentativa)

    assert pontos(linha) == linha.points_snapshot


def test_verdadeiro_falso_errado_vale_zero(tentativa):
    linha = linha_do_tipo(tentativa, QuestionType.TRUE_FALSE)
    marcar(tentativa, linha, [erradas(linha)[0]])
    responder_resto(tentativa, linha)

    submit_attempt(tentativa)

    assert pontos(linha) == Decimal("0.00")


# ---------------------------------------------------------------------------
# MULTIPLE_CHOICE: conjunto exato
# ---------------------------------------------------------------------------


def test_multipla_com_o_conjunto_exato_vale_tudo(tentativa):
    linha = linha_do_tipo(tentativa, QuestionType.MULTIPLE_CHOICE)
    marcar(tentativa, linha, corretas(linha))
    responder_resto(tentativa, linha)

    submit_attempt(tentativa)

    assert pontos(linha) == linha.points_snapshot


def test_multipla_faltando_uma_correta_vale_zero(tentativa):
    """
    Resposta incompleta e zero, e nao proporcional.

    Marcar A e C quando o gabarito e A, C e D nao demonstra que o aluno sabia
    que D tambem estava certa — demonstra que ele nao sabia.
    """
    linha = linha_do_tipo(tentativa, QuestionType.MULTIPLE_CHOICE)
    todas = corretas(linha)
    assert len(todas) >= 2, "a fixture precisa de pelo menos duas corretas"

    marcar(tentativa, linha, todas[:-1])
    responder_resto(tentativa, linha)

    submit_attempt(tentativa)

    assert pontos(linha) == Decimal("0.00")


def test_multipla_com_uma_alternativa_a_mais_vale_zero(tentativa):
    """
    O caso que a regra existe para cobrir.

    Sem "conjunto exato", marcar TODAS as alternativas garantiria pontuacao
    parcial em qualquer questao de multipla escolha — e seria a estrategia
    otima para quem nao estudou.
    """
    linha = linha_do_tipo(tentativa, QuestionType.MULTIPLE_CHOICE)
    marcar(tentativa, linha, corretas(linha) + [erradas(linha)[0]])
    responder_resto(tentativa, linha)

    submit_attempt(tentativa)

    assert pontos(linha) == Decimal("0.00")


def test_multipla_com_todas_marcadas_vale_zero(tentativa):
    linha = linha_do_tipo(tentativa, QuestionType.MULTIPLE_CHOICE)
    marcar(tentativa, linha, alternativas(linha))
    responder_resto(tentativa, linha)

    submit_attempt(tentativa)

    assert pontos(linha) == Decimal("0.00")


def test_multipla_em_branco_vale_zero(tentativa):
    linha = linha_do_tipo(tentativa, QuestionType.MULTIPLE_CHOICE)
    tornar_opcional(linha)
    responder_resto(tentativa, linha)

    submit_attempt(tentativa)

    assert pontos(linha) == Decimal("0.00")


def test_a_ordem_das_marcacoes_nao_importa(tentativa):
    """
    Comparacao de conjuntos, e nao de listas.

    Com randomize_options ligado, cada aluno ve as alternativas numa ordem
    diferente — comparar sequencias reprovaria pela posicao.
    """
    linha = linha_do_tipo(tentativa, QuestionType.MULTIPLE_CHOICE)
    marcar(tentativa, linha, list(reversed(corretas(linha))))
    responder_resto(tentativa, linha)

    submit_attempt(tentativa)

    assert pontos(linha) == linha.points_snapshot


# ---------------------------------------------------------------------------
# Questoes manuais
# ---------------------------------------------------------------------------


def test_texto_com_conteudo_fica_pendente(tentativa_enviada):
    """As dissertativas respondidas esperam um avaliador."""
    manuais = AttemptQuestion.objects.filter(
        attempt=tentativa_enviada,
        question__type__in=[QuestionType.SHORT_TEXT, QuestionType.ESSAY],
    )
    assert manuais.count() == 2
    for linha in manuais:
        assert linha.grading_status == QuestionGradingStatus.PENDING
        assert linha.awarded_points is None


def test_texto_em_branco_recebe_zero_automatico(tentativa):
    """
    A decisao de projeto explicada.

    Uma redacao em branco nao tem conteudo para avaliar. Deixa-la pendente
    obrigaria o administrador a abrir cada uma so para escrever 0, e a fila de
    correcao viraria uma fila de cliques — pior, uma prova inteiramente em
    branco ficaria eternamente AWAITING_REVIEW se ninguem lembrasse.
    """
    for linha in tentativa.questions.select_related("question").all():
        if linha.question.type in {QuestionType.SHORT_TEXT, QuestionType.ESSAY}:
            tornar_opcional(linha)
        else:
            marcar(tentativa, linha, corretas(linha))

    submit_attempt(tentativa)
    tentativa.refresh_from_db()

    manuais = AttemptQuestion.objects.filter(
        attempt=tentativa,
        question__type__in=[QuestionType.SHORT_TEXT, QuestionType.ESSAY],
    )
    for linha in manuais:
        assert linha.grading_status == QuestionGradingStatus.AUTO_GRADED
        assert linha.awarded_points == Decimal("0.00")

    # Sem pendencia manual, a correcao fecha sozinha.
    assert tentativa.grading_status == GradingStatus.GRADED


def test_texto_so_com_espacos_conta_como_branco(tentativa):
    """
    Espaco e quebra de linha nao sao conteudo.

    O envio ja trata assim — questoes_obrigatorias_sem_resposta recusa uma
    obrigatoria com so espacos —, entao aqui as dissertativas precisam ser
    opcionais para que o teste chegue na correcao. As duas regras concordam, e
    e bom que concordem: seria estranho o envio dizer "voce nao respondeu" e a
    correcao dizer "ha algo para avaliar".
    """
    for linha in tentativa.questions.select_related("question").all():
        if linha.question.type in {QuestionType.SHORT_TEXT, QuestionType.ESSAY}:
            tornar_opcional(linha)
            autosave_answer(
                tentativa, question_token=str(linha.public_token), text="   \n  "
            )
        else:
            marcar(tentativa, linha, corretas(linha))

    submit_attempt(tentativa)
    tentativa.refresh_from_db()

    assert tentativa.grading_status == GradingStatus.GRADED


# ---------------------------------------------------------------------------
# Fluxo e situacao
# ---------------------------------------------------------------------------


def test_prova_com_dissertativa_vai_para_aguardando_avaliador(tentativa_enviada):
    assert tentativa_enviada.grading_status == GradingStatus.AWAITING_REVIEW
    assert tentativa_enviada.result is None
    assert tentativa_enviada.final_score is None


def test_prova_so_objetiva_fecha_sozinha(prova_aberta, aluno_matriculado):
    """
    Sem questao manual nao ha o que esperar: submit, correcao e nota fechada,
    tudo no mesmo request.
    """
    from exams.models import Question

    Question.objects.filter(
        exam=prova_aberta,
        type__in=[QuestionType.SHORT_TEXT, QuestionType.ESSAY],
    ).update(active=False)

    nova = start_attempt(aluno_matriculado, prova_aberta)
    for linha in nova.questions.select_related("question").all():
        marcar(nova, linha, corretas(linha))

    enviada = submit_attempt(nova)

    # Fechou sozinha, sem passar por AWAITING_REVIEW: e isso que o teste mede.
    assert enviada.grading_status == GradingStatus.GRADED
    assert enviada.final_score is not None
    assert enviada.graded_at is not None
    assert enviada.result in (AttemptResult.APPROVED, AttemptResult.FAILED)

    # total_points_snapshot continua sendo o total da prova PUBLICADA, e nao a
    # soma das questoes ativas. Desativar uma questao depois de publicar nao
    # reduz a escala da nota — e por isso que a prova publicada e imutavel na
    # operacao real, e este teste so consegue desativar por escrever direto na
    # tabela.
    assert enviada.total_points_snapshot == nova.exam.total_points


def test_expirada_tambem_e_corrigida(prova_aberta, aluno_matriculado):
    """
    Expirada nao e "sem nota".

    O aluno teve o tempo dele; o que ficou em branco vale zero e o resultado e
    um resultado. Tratar expirada como pendente para sempre deixaria o aluno
    num limbo sem explicacao.
    """
    from datetime import timedelta

    from exams.services import expire_attempt

    nova = start_attempt(aluno_matriculado, prova_aberta)
    agora = timezone.now()
    ExamAttempt.objects.filter(pk=nova.pk).update(
        started_at=agora - timedelta(hours=2), expires_at=agora - timedelta(hours=1)
    )
    nova.refresh_from_db()

    expirada = expire_attempt(nova)

    # Nada respondido: todas as objetivas zeradas, todas as manuais vazias
    # zeradas, correcao fechada.
    assert expirada.grading_status == GradingStatus.GRADED
    assert expirada.obtained_points == Decimal("0.00")
    assert expirada.final_score == Decimal("0.000000")
    assert expirada.result == AttemptResult.FAILED


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------


def test_corrigir_duas_vezes_nao_soma_pontos(tentativa_enviada):
    """
    Cada questao recebe um valor ABSOLUTO, e nao um incremento.

    Importa porque a correcao e chamada tanto pelo envio quanto pela
    expiracao, e uma tentativa pode passar pelos dois caminhos.
    """
    linha = linha_do_tipo(tentativa_enviada, QuestionType.SINGLE_CHOICE)
    primeiro = pontos(linha)

    grade_objective_questions(tentativa_enviada)
    grade_objective_questions(tentativa_enviada)

    assert pontos(linha) == primeiro


def test_corrigir_duas_vezes_nao_duplica_auditoria(tentativa_enviada):
    from audit.models import AuditEvent, AuditLog

    grade_objective_questions(tentativa_enviada)
    grade_objective_questions(tentativa_enviada)

    eventos = AuditLog.objects.filter(
        event=AuditEvent.GRADING_STARTED,
        entity_type="ExamAttempt",
        entity_id=str(tentativa_enviada.pk),
    )
    assert eventos.count() == 1


def test_nao_recorrige_tentativa_ja_fechada(tentativa_enviada, admin_user):
    """
    Uma nota fechada e uma nota que o aluno pode ter visto.

    Recorrigir mudaria o resultado dele depois do fato, sem que ninguem
    tivesse pedido.
    """
    from exams.services import save_manual_grade

    for linha in AttemptQuestion.objects.filter(
        attempt=tentativa_enviada, grading_status=QuestionGradingStatus.PENDING
    ):
        save_manual_grade(
            tentativa_enviada,
            question_id=linha.pk,
            points=linha.points_snapshot,
            actor=admin_user,
        )
    fechada = finalize_grading(tentativa_enviada, actor=admin_user)
    nota = fechada.final_score

    grade_objective_questions(fechada)

    fechada.refresh_from_db()
    assert fechada.final_score == nota


def test_tentativa_em_andamento_nao_e_corrigivel(tentativa):
    from exams.services import TentativaNaoCorrigivel

    with pytest.raises(TentativaNaoCorrigivel):
        grade_objective_questions(tentativa)
