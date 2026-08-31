"""
Correcao manual: o avaliador lendo dissertativas.

O que este arquivo protege
--------------------------
Salvar e finalizar sao operacoes separadas. Uma prova com cinco redacoes
costuma ser corrigida em mais de uma sessao, e um "salvar" que fechasse a nota
por engano nao teria volta — a tentativa passaria a GRADED e o aluno veria um
resultado incompleto.

O teto de pontos e o valor da propria questao. Uma dissertativa de 2,00 que
recebesse 5,00 elevaria a nota da prova acima do total possivel, e nenhuma
tela mostraria de onde veio o excedente. A regra existe duas vezes: no servico,
com mensagem que o avaliador entende, e como CheckConstraint, para quem nao
passa pelo servico.
"""

from decimal import Decimal

import pytest

from audit.models import AuditEvent, AuditLog
from exams.models import (
    AttemptQuestion,
    AttemptResult,
    GradingStatus,
    QuestionGradingStatus,
    QuestionType,
)
from exams.services import (
    ManuaisPendentes,
    NotaForaDoIntervalo,
    autosave_answer,
    finalize_grading,
    save_manual_grade,
    submit_attempt,
)
from exams.services.grading import TentativaNaoCorrigivel

pytestmark = pytest.mark.django_db

MANUAIS = [QuestionType.SHORT_TEXT, QuestionType.ESSAY]


def corretas(linha):
    return [a for a in linha.options.select_related("option").all() if a.option.is_correct]


@pytest.fixture
def aguardando(tentativa):
    """Tentativa enviada, com objetivas certas e dissertativas respondidas."""
    for linha in tentativa.questions.select_related("question").all():
        if linha.question.type in MANUAIS:
            autosave_answer(
                tentativa,
                question_token=str(linha.public_token),
                text="Resposta dissertativa com conteudo.",
            )
        else:
            autosave_answer(
                tentativa,
                question_token=str(linha.public_token),
                option_tokens=[str(a.public_token) for a in corretas(linha)],
            )
    enviada = submit_attempt(tentativa)
    assert enviada.grading_status == GradingStatus.AWAITING_REVIEW
    return enviada


def manuais(tentativa):
    return list(
        AttemptQuestion.objects.filter(
            attempt=tentativa, question__type__in=MANUAIS
        ).select_related("question").order_by("display_order")
    )


def corrigir_todas(tentativa, admin_user, *, pontos=None):
    for linha in manuais(tentativa):
        save_manual_grade(
            tentativa,
            question_id=linha.pk,
            points=linha.points_snapshot if pontos is None else pontos,
            actor=admin_user,
        )


# ---------------------------------------------------------------------------
# Limites
# ---------------------------------------------------------------------------


def test_nota_zero_e_aceita(aguardando, admin_user):
    """Zero e uma nota legitima: a resposta existe e nao vale nada."""
    linha = manuais(aguardando)[0]

    save_manual_grade(
        aguardando, question_id=linha.pk, points="0", actor=admin_user
    )

    linha.refresh_from_db()
    assert linha.awarded_points == Decimal("0.00")
    assert linha.grading_status == QuestionGradingStatus.MANUALLY_GRADED


def test_nota_maxima_e_aceita(aguardando, admin_user):
    linha = manuais(aguardando)[0]

    save_manual_grade(
        aguardando,
        question_id=linha.pk,
        points=linha.points_snapshot,
        actor=admin_user,
    )

    linha.refresh_from_db()
    assert linha.awarded_points == linha.points_snapshot


def test_nota_decimal_e_aceita(aguardando, admin_user):
    """Meia nota numa redacao e o caso normal, nao a excecao."""
    linha = manuais(aguardando)[0]

    save_manual_grade(
        aguardando, question_id=linha.pk, points="1.25", actor=admin_user
    )

    linha.refresh_from_db()
    assert linha.awarded_points == Decimal("1.25")


def test_nota_com_virgula_e_aceita(aguardando, admin_user):
    """
    O avaliador digita como fala.

    Um teclado brasileiro produz "1,25", e recusar isso seria transformar um
    detalhe de localizacao em erro de validacao.
    """
    linha = manuais(aguardando)[0]

    save_manual_grade(
        aguardando, question_id=linha.pk, points="1,25", actor=admin_user
    )

    linha.refresh_from_db()
    assert linha.awarded_points == Decimal("1.25")


def test_nota_negativa_e_recusada(aguardando, admin_user):
    linha = manuais(aguardando)[0]

    with pytest.raises(NotaForaDoIntervalo):
        save_manual_grade(
            aguardando, question_id=linha.pk, points="-1", actor=admin_user
        )

    linha.refresh_from_db()
    assert linha.awarded_points is None


def test_nota_acima_do_valor_da_questao_e_recusada(aguardando, admin_user):
    linha = manuais(aguardando)[0]
    acima = linha.points_snapshot + Decimal("0.01")

    with pytest.raises(NotaForaDoIntervalo):
        save_manual_grade(
            aguardando, question_id=linha.pk, points=acima, actor=admin_user
        )

    linha.refresh_from_db()
    assert linha.awarded_points is None


def test_nota_vazia_e_recusada(aguardando, admin_user):
    linha = manuais(aguardando)[0]

    with pytest.raises(NotaForaDoIntervalo):
        save_manual_grade(
            aguardando, question_id=linha.pk, points="", actor=admin_user
        )


def test_nota_nao_numerica_e_recusada(aguardando, admin_user):
    linha = manuais(aguardando)[0]

    with pytest.raises(NotaForaDoIntervalo):
        save_manual_grade(
            aguardando, question_id=linha.pk, points="dez", actor=admin_user
        )


# ---------------------------------------------------------------------------
# Comentario
# ---------------------------------------------------------------------------


def test_comentario_e_gravado(aguardando, admin_user):
    linha = manuais(aguardando)[0]

    save_manual_grade(
        aguardando,
        question_id=linha.pk,
        points="1.00",
        comment="  Faltou fundamentar o segundo paragrafo.  ",
        actor=admin_user,
    )

    linha.refresh_from_db()
    assert linha.grader_comment == "Faltou fundamentar o segundo paragrafo."


def test_comentario_e_opcional(aguardando, admin_user):
    linha = manuais(aguardando)[0]

    save_manual_grade(
        aguardando, question_id=linha.pk, points="1.00", actor=admin_user
    )

    linha.refresh_from_db()
    assert linha.grader_comment == ""


def test_o_avaliador_fica_registrado(aguardando, admin_user):
    linha = manuais(aguardando)[0]

    save_manual_grade(
        aguardando, question_id=linha.pk, points="1.00", actor=admin_user
    )

    linha.refresh_from_db()
    assert linha.graded_by_id == admin_user.pk
    assert linha.graded_at is not None


# ---------------------------------------------------------------------------
# O que nao aceita nota manual
# ---------------------------------------------------------------------------


def test_questao_objetiva_recusa_nota_manual(aguardando, admin_user):
    """
    Mesmo que o POST venha forjado com o id de uma objetiva.

    Sem esta barreira, um administrador — ou um POST montado a mao — poderia
    reescrever a correcao automatica e o gabarito deixaria de valer.
    """
    from common.exceptions import DomainError

    objetiva = AttemptQuestion.objects.filter(
        attempt=aguardando, question__type=QuestionType.SINGLE_CHOICE
    ).first()
    antes = objetiva.awarded_points

    with pytest.raises(DomainError):
        save_manual_grade(
            aguardando, question_id=objetiva.pk, points="0", actor=admin_user
        )

    objetiva.refresh_from_db()
    assert objetiva.awarded_points == antes


def test_questao_de_outra_tentativa_e_recusada(
    aguardando, admin_user, outro_student, modulo, prova_aberta
):
    """
    IDOR na correcao.

    O filtro inclui a tentativa, entao o id de uma questao alheia simplesmente
    nao e encontrado — mesma recusa de um id inexistente.
    """
    from common.exceptions import DomainError
    from courses.services import create_enrollment
    from exams.services import start_attempt

    create_enrollment(student=outro_student, module=modulo)
    outra = start_attempt(outro_student, prova_aberta)
    alheia = manuais(outra)[0]

    with pytest.raises(DomainError):
        save_manual_grade(
            aguardando, question_id=alheia.pk, points="1.00", actor=admin_user
        )


def test_questao_inexistente_e_recusada(aguardando, admin_user):
    from common.exceptions import DomainError

    with pytest.raises(DomainError):
        save_manual_grade(
            aguardando, question_id=999999, points="1.00", actor=admin_user
        )


# ---------------------------------------------------------------------------
# Rascunho e finalizacao sao operacoes diferentes
# ---------------------------------------------------------------------------


def test_salvar_nao_finaliza(aguardando, admin_user):
    linha = manuais(aguardando)[0]

    save_manual_grade(
        aguardando, question_id=linha.pk, points="1.00", actor=admin_user
    )

    aguardando.refresh_from_db()
    assert aguardando.grading_status == GradingStatus.AWAITING_REVIEW
    assert aguardando.final_score is None


def test_salvar_varias_vezes_mantem_o_ultimo_valor(aguardando, admin_user):
    """Corrigir e revisar: a ultima palavra do avaliador e a que vale."""
    linha = manuais(aguardando)[0]

    save_manual_grade(aguardando, question_id=linha.pk, points="0.50", actor=admin_user)
    save_manual_grade(aguardando, question_id=linha.pk, points="1.50", actor=admin_user)

    linha.refresh_from_db()
    assert linha.awarded_points == Decimal("1.50")


def test_finalizar_com_pendencia_e_recusado_com_a_lista(aguardando, admin_user):
    linha = manuais(aguardando)[0]
    save_manual_grade(
        aguardando, question_id=linha.pk, points="1.00", actor=admin_user
    )

    with pytest.raises(ManuaisPendentes) as erro:
        finalize_grading(aguardando, actor=admin_user)

    # A que sobrou, e so ela.
    restante = manuais(aguardando)[1]
    assert erro.value.numeros == [restante.display_order + 1]

    aguardando.refresh_from_db()
    assert aguardando.grading_status == GradingStatus.AWAITING_REVIEW


def test_finalizar_com_tudo_corrigido_fecha_a_nota(aguardando, admin_user):
    corrigir_todas(aguardando, admin_user)

    fechada = finalize_grading(aguardando, actor=admin_user)

    assert fechada.grading_status == GradingStatus.GRADED
    assert fechada.result == AttemptResult.APPROVED
    assert fechada.final_score == Decimal("10.000000")
    assert fechada.graded_at is not None


def test_os_pontos_ficam_separados_por_origem(aguardando, admin_user):
    """
    Objetivas e manuais somadas separadamente.

    E a primeira pergunta de qualquer recurso de nota: quanto veio da maquina
    e quanto veio de uma pessoa.
    """
    corrigir_todas(aguardando, admin_user)
    fechada = finalize_grading(aguardando, actor=admin_user)

    assert fechada.objective_points > 0
    assert fechada.manual_points > 0
    assert fechada.obtained_points == (
        fechada.objective_points + fechada.manual_points
    )


def test_finalizar_duas_vezes_e_idempotente(aguardando, admin_user):
    corrigir_todas(aguardando, admin_user)

    primeira = finalize_grading(aguardando, actor=admin_user)
    quando = primeira.graded_at
    nota = primeira.final_score

    segunda = finalize_grading(aguardando, actor=admin_user)

    assert segunda.graded_at == quando
    assert segunda.final_score == nota

    eventos = AuditLog.objects.filter(
        event=AuditEvent.GRADING_COMPLETED,
        entity_type="ExamAttempt",
        entity_id=str(aguardando.pk),
    )
    assert eventos.count() == 1


def test_depois_de_finalizada_nao_aceita_mais_nota(aguardando, admin_user):
    corrigir_todas(aguardando, admin_user)
    finalize_grading(aguardando, actor=admin_user)

    linha = manuais(aguardando)[0]
    with pytest.raises(TentativaNaoCorrigivel):
        save_manual_grade(
            aguardando, question_id=linha.pk, points="0", actor=admin_user
        )


# ---------------------------------------------------------------------------
# Reprovacao
# ---------------------------------------------------------------------------


def test_nota_abaixo_do_minimo_reprova(aguardando, admin_user):
    """Objetivas certas, dissertativas zeradas: nao alcanca a nota minima."""
    corrigir_todas(aguardando, admin_user, pontos="0")

    fechada = finalize_grading(aguardando, actor=admin_user)

    assert fechada.result == AttemptResult.FAILED
    assert fechada.final_score < Decimal(fechada.passing_score_snapshot)


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------


def test_a_trilha_registra_a_nota_manual_sem_o_texto(aguardando, admin_user):
    """
    A metadata guarda a questao e os pontos.

    O texto da resposta e o comentario do avaliador ficam de fora: a trilha
    responde "que nota foi dada, por quem e quando", e nao guarda o conteudo
    da prova.
    """
    linha = manuais(aguardando)[0]
    save_manual_grade(
        aguardando,
        question_id=linha.pk,
        points="1.00",
        comment="Comentario privado do avaliador.",
        actor=admin_user,
    )

    trilha = " ".join(
        str(m)
        for m in AuditLog.objects.filter(
            event=AuditEvent.MANUAL_GRADE_SAVED
        ).values_list("metadata", flat=True)
    )

    assert "Comentario privado" not in trilha
    assert "Resposta dissertativa" not in trilha
    assert "1.00" in trilha


def test_a_trilha_registra_o_fechamento_com_a_nota(aguardando, admin_user):
    """
    A nota PODE ficar na trilha: ela nao e segredo, e a pergunta "que nota foi
    fechada, por quem e quando" e exatamente o que uma auditoria precisa
    responder.
    """
    corrigir_todas(aguardando, admin_user)
    fechada = finalize_grading(aguardando, actor=admin_user)

    evento = AuditLog.objects.filter(
        event=AuditEvent.GRADING_COMPLETED,
        entity_type="ExamAttempt",
        entity_id=str(fechada.pk),
    ).first()

    assert evento is not None
    assert evento.metadata["result"] == "APPROVED"
    assert evento.metadata["final_score"] == str(fechada.final_score)
    assert evento.actor_id == admin_user.pk


def test_a_trilha_da_correcao_nao_guarda_gabarito(aguardando, admin_user):
    corrigir_todas(aguardando, admin_user)
    finalize_grading(aguardando, actor=admin_user)

    trilha = " ".join(
        str(m)
        for m in AuditLog.objects.filter(
            entity_type="ExamAttempt", entity_id=str(aguardando.pk)
        ).values_list("metadata", flat=True)
    ).lower()

    for proibido in ("is_correct", "brasilia", "gabarito", "resposta dissertativa"):
        assert proibido not in trilha
