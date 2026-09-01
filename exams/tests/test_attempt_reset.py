"""
Anulacao administrativa de tentativa.

O que estes testes protegem
---------------------------
Resetar retira a VALIDADE de uma tentativa sem apagar o registro dela. A
diferenca entre as duas coisas e o assunto do arquivo inteiro: quase todo
teste aqui confere que algo continua existindo depois do reset.

O reset tambem dispara cascata — revoga certificado, pode reativar matricula —
e cada ramo dessa cascata tem um caso em que ele NAO deve disparar.
"""

from decimal import Decimal

import pytest
from django.db import connection
from django.urls import reverse

from audit.models import AuditEvent, AuditLog
from certificates.models import Certificate, CertificateStatus
from certificates.services import issue_certificate
from common.exceptions import DomainError
from courses.models import Enrollment, EnrollmentStatus
from exams.models import (
    AttemptResult,
    AttemptStatus,
    ExamAttempt,
    GradingStatus,
    QuestionType,
)
from exams.services import (
    autosave_answer,
    finalize_grading,
    save_manual_grade,
    start_attempt,
    submit_attempt,
)
from exams.services import reset as reset_service

pytestmark = pytest.mark.django_db

TEXTUAIS = {QuestionType.SHORT_TEXT, QuestionType.ESSAY}
MOTIVO = "Aluno relatou queda de energia durante a realizacao."


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------


def responder_tudo(tentativa, *, certo=True):
    for linha in tentativa.questions.select_related("question").all():
        if linha.question.type in TEXTUAIS:
            autosave_answer(
                tentativa, question_token=str(linha.public_token), text="resposta"
            )
            continue
        opcoes = list(linha.options.select_related("option").all())
        escolhidas = [o for o in opcoes if o.option.is_correct]
        if not certo:
            escolhidas = [o for o in opcoes if not o.option.is_correct][:1]
        autosave_answer(
            tentativa,
            question_token=str(linha.public_token),
            option_tokens=[str(o.public_token) for o in escolhidas],
        )


def corrigir(tentativa, admin_user, *, cheio=True):
    for linha in tentativa.questions.select_related("question").all():
        if linha.question.type not in TEXTUAIS:
            continue
        save_manual_grade(
            tentativa,
            # PK da AttemptQuestion, e nao da Question.
            question_id=linha.pk,
            points=linha.points_snapshot if cheio else Decimal("0.00"),
            actor=admin_user,
        )
    return finalize_grading(tentativa, actor=admin_user)


@pytest.fixture
def aprovada(tentativa, admin_user):
    responder_tudo(tentativa, certo=True)
    fechada = corrigir(submit_attempt(tentativa), admin_user, cheio=True)
    assert fechada.result == AttemptResult.APPROVED
    return fechada


@pytest.fixture
def reprovada(tentativa, admin_user):
    responder_tudo(tentativa, certo=False)
    fechada = corrigir(submit_attempt(tentativa), admin_user, cheio=False)
    assert fechada.result == AttemptResult.FAILED
    return fechada


def matricula_de(tentativa):
    return Enrollment.objects.get(
        student=tentativa.student, module=tentativa.exam.module
    )


# ---------------------------------------------------------------------------
# O basico
# ---------------------------------------------------------------------------


def test_anular_em_andamento(tentativa, admin_user):
    anulada, _ = reset_service.reset_attempt(
        tentativa, actor=admin_user, reason=MOTIVO
    )

    assert anulada.status == AttemptStatus.RESET
    assert anulada.reset_at is not None
    assert anulada.reset_by == admin_user
    assert anulada.reset_reason == MOTIVO


def test_anular_enviada_preserva_o_carimbo_de_envio(tentativa, admin_user):
    responder_tudo(tentativa)
    enviada = submit_attempt(tentativa)
    envio = enviada.submitted_at

    anulada, _ = reset_service.reset_attempt(
        enviada, actor=admin_user, reason=MOTIVO
    )

    assert anulada.status == AttemptStatus.RESET
    assert anulada.submitted_at == envio


def test_anular_expirada_preserva_o_carimbo_de_expiracao(tentativa, admin_user):
    from exams.services import expire_attempt

    expirada = expire_attempt(tentativa)
    carimbo = expirada.expired_at

    anulada, _ = reset_service.reset_attempt(
        expirada, actor=admin_user, reason=MOTIVO
    )

    assert anulada.status == AttemptStatus.RESET
    assert anulada.expired_at == carimbo


def test_anular_corrigida_preserva_nota_e_resultado(aprovada, admin_user):
    nota = aprovada.final_score
    pontos = aprovada.obtained_points

    anulada, _ = reset_service.reset_attempt(
        aprovada, actor=admin_user, reason=MOTIVO
    )

    assert anulada.status == AttemptStatus.RESET
    assert anulada.final_score == nota
    assert anulada.obtained_points == pontos
    assert anulada.result == AttemptResult.APPROVED
    assert anulada.grading_status == GradingStatus.GRADED


def test_as_respostas_permanecem(aprovada, admin_user):
    """
    A anulacao retira a validade, e nao o registro.

    Sem as respostas nao ha como auditar depois por que a tentativa foi
    anulada, nem o que o aluno tinha escrito.
    """
    from exams.models import Answer, AttemptQuestion

    linhas_antes = AttemptQuestion.objects.filter(attempt=aprovada).count()
    respostas_antes = Answer.objects.filter(attempt_question__attempt=aprovada).count()
    assert respostas_antes > 0

    reset_service.reset_attempt(aprovada, actor=admin_user, reason=MOTIVO)

    assert AttemptQuestion.objects.filter(attempt=aprovada).count() == linhas_antes
    assert (
        Answer.objects.filter(attempt_question__attempt=aprovada).count()
        == respostas_antes
    )


# ---------------------------------------------------------------------------
# Motivo
# ---------------------------------------------------------------------------


def test_motivo_vazio_e_recusado(tentativa, admin_user):
    with pytest.raises(DomainError):
        reset_service.reset_attempt(tentativa, actor=admin_user, reason="")

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.IN_PROGRESS


def test_motivo_so_com_espacos_e_recusado(tentativa, admin_user):
    with pytest.raises(DomainError):
        reset_service.reset_attempt(tentativa, actor=admin_user, reason="   \n  ")


def test_motivo_longo_demais_e_recusado(tentativa, admin_user):
    with pytest.raises(DomainError):
        reset_service.reset_attempt(tentativa, actor=admin_user, reason="x" * 1001)


# ---------------------------------------------------------------------------
# Numeracao e limite
# ---------------------------------------------------------------------------


def test_o_numero_da_tentativa_nunca_e_reaproveitado(
    tentativa, admin_user, prova_aberta, aluno_matriculado
):
    """
    Anular a tentativa 1 e refazer produz 1 RESET e 1 nova de numero 2.

    Duas tentativas de numero 1 tornariam o historico ilegivel: qual delas e
    "a primeira"?
    """
    assert tentativa.attempt_number == 1
    reset_service.reset_attempt(tentativa, actor=admin_user, reason=MOTIVO)

    nova = start_attempt(aluno_matriculado, prova_aberta)

    assert nova.attempt_number == 2
    assert ExamAttempt.objects.filter(attempt_number=1).count() == 1


def test_anulada_nao_conta_para_o_limite(
    tentativa, admin_user, prova_aberta, aluno_matriculado
):
    """max_attempts da fixture e 1: sem esta regra, a nova seria recusada."""
    assert prova_aberta.max_attempts == 1

    reset_service.reset_attempt(tentativa, actor=admin_user, reason=MOTIVO)

    nova = start_attempt(aluno_matriculado, prova_aberta)
    assert nova.status == AttemptStatus.IN_PROGRESS


def test_reset_nao_reabre_a_janela_fechada(
    tentativa, admin_user, prova_aberta, aluno_matriculado
):
    """
    Resetar libera o SLOT, e nao a prova.

    Um atalho que ignorasse a janela seria um bypass escondido dentro de uma
    funcao chamada "reset".
    """
    from django.utils import timezone

    from exams.models import Exam

    _, resumo = reset_service.reset_attempt(
        tentativa, actor=admin_user, reason=MOTIVO
    )
    assert resumo["janela_aberta"] is True

    Exam.objects.filter(pk=prova_aberta.pk).update(
        close_at=timezone.now() - timezone.timedelta(minutes=1)
    )
    prova_aberta.refresh_from_db()

    with pytest.raises(DomainError) as erro:
        start_attempt(aluno_matriculado, prova_aberta)
    assert "encerrado" in str(erro.value)


def test_a_url_da_tentativa_anulada_deixa_de_aceitar_escrita(tentativa, admin_user):
    from exams.services import TentativaNaoEditavel

    linha = tentativa.questions.select_related("question").first()
    reset_service.reset_attempt(tentativa, actor=admin_user, reason=MOTIVO)
    tentativa.refresh_from_db()

    with pytest.raises(TentativaNaoEditavel):
        autosave_answer(
            tentativa, question_token=str(linha.public_token), text="tarde demais"
        )


# ---------------------------------------------------------------------------
# Certificado
# ---------------------------------------------------------------------------


def test_reset_revoga_o_certificado(aprovada, admin_user):
    certificado, _ = issue_certificate(aprovada, actor=admin_user)
    assert certificado.status == CertificateStatus.ACTIVE

    _, resumo = reset_service.reset_attempt(
        aprovada, actor=admin_user, reason=MOTIVO
    )

    certificado.refresh_from_db()
    assert resumo["certificate_revoked"] is True
    assert certificado.status == CertificateStatus.REVOKED
    assert certificado.revoked_at is not None
    assert "resetada administrativamente" in certificado.revocation_reason


def test_o_certificado_nao_e_apagado(aprovada, admin_user):
    certificado, _ = issue_certificate(aprovada, actor=admin_user)
    codigo = certificado.verification_code

    reset_service.reset_attempt(aprovada, actor=admin_user, reason=MOTIVO)

    assert Certificate.objects.filter(verification_code=codigo).exists()


def test_a_validacao_publica_passa_a_dizer_revogado(client, aprovada, admin_user):
    certificado, _ = issue_certificate(aprovada, actor=admin_user)
    endereco = reverse(
        "certificates:validate",
        kwargs={"verification_code": certificado.verification_code},
    )

    reset_service.reset_attempt(aprovada, actor=admin_user, reason=MOTIVO)

    corpo = client.get(endereco).content.decode("utf-8")
    assert "Certificado revogado" in corpo
    assert "Certificado valido" not in corpo


def test_reset_sem_certificado_nao_quebra(reprovada, admin_user):
    _, resumo = reset_service.reset_attempt(
        reprovada, actor=admin_user, reason=MOTIVO
    )

    assert resumo["certificate_revoked"] is False


# ---------------------------------------------------------------------------
# Matricula
# ---------------------------------------------------------------------------


def test_reset_reativa_a_matricula_concluida(aprovada, admin_user):
    issue_certificate(aprovada, actor=admin_user)

    matricula = matricula_de(aprovada)
    assert matricula.status == EnrollmentStatus.COMPLETED
    assert matricula.access_enabled is False

    _, resumo = reset_service.reset_attempt(
        aprovada, actor=admin_user, reason=MOTIVO
    )

    matricula.refresh_from_db()
    assert resumo["enrollment_reactivated"] is True
    assert matricula.status == EnrollmentStatus.ACTIVE
    assert matricula.access_enabled is True
    assert (
        AuditLog.objects.filter(event=AuditEvent.ENROLLMENT_REACTIVATED).count() == 1
    )


def test_nao_reativa_se_existir_outro_certificado_valido(
    aprovada, admin_user, prova_aberta, aluno_matriculado
):
    """
    O aluno ainda tem comprovacao valida de conclusao do modulo.

    Reabrir o modulo contradiria o documento que ele tem na mao.
    """
    issue_certificate(aprovada, actor=admin_user)

    # Segunda prova do MESMO modulo, com o proprio certificado.
    from exams.services import create_exam, create_question, publish_exam

    outra = create_exam(
        module=prova_aberta.module,
        title="Segunda prova do modulo",
        duration_minutes=30,
        open_at=prova_aberta.open_at,
        close_at=prova_aberta.close_at,
        passing_score=Decimal("6.00"),
        actor=admin_user,
    )
    create_question(
        outra,
        type=QuestionType.SINGLE_CHOICE,
        text="Dois mais dois?",
        points=Decimal("10.00"),
        order=1,
        opcoes=[{"text": "4", "is_correct": True}, {"text": "5", "is_correct": False}],
        actor=admin_user,
    )
    outra = publish_exam(outra, actor=admin_user)

    # A matricula foi concluida pela emissao; reabrir para fazer a segunda.
    from courses.services import reactivate_enrollment

    reactivate_enrollment(matricula_de(aprovada))

    segunda = start_attempt(aluno_matriculado, outra)
    responder_tudo(segunda, certo=True)
    segunda = submit_attempt(segunda)
    assert segunda.result == AttemptResult.APPROVED
    issue_certificate(segunda, actor=admin_user)

    # Agora anula a PRIMEIRA. O certificado da segunda continua valido.
    _, resumo = reset_service.reset_attempt(
        aprovada, actor=admin_user, reason=MOTIVO
    )

    assert resumo["certificate_revoked"] is True
    assert resumo["enrollment_reactivated"] is False

    matricula = matricula_de(aprovada)
    assert matricula.status == EnrollmentStatus.COMPLETED


def test_nao_reativa_matricula_de_modulo_inativo(aprovada, admin_user):
    """
    Reativar a matricula nao pode ligar um modulo que a administracao
    desligou de proposito.
    """
    from courses.models import Module

    issue_certificate(aprovada, actor=admin_user)
    Module.objects.filter(pk=aprovada.exam.module_id).update(is_active=False)

    _, resumo = reset_service.reset_attempt(
        aprovada, actor=admin_user, reason=MOTIVO
    )

    assert resumo["enrollment_reactivated"] is False
    assert matricula_de(aprovada).status == EnrollmentStatus.COMPLETED


def test_reprovada_nao_mexe_na_matricula(reprovada, admin_user):
    antes = matricula_de(reprovada)
    assert antes.status == EnrollmentStatus.ACTIVE

    _, resumo = reset_service.reset_attempt(
        reprovada, actor=admin_user, reason=MOTIVO
    )

    depois = matricula_de(reprovada)
    assert resumo["enrollment_reactivated"] is False
    assert depois.status == EnrollmentStatus.ACTIVE
    assert depois.access_enabled is True


# ---------------------------------------------------------------------------
# Idempotencia e concorrencia
# ---------------------------------------------------------------------------


def test_resetar_duas_vezes_e_recusado(tentativa, admin_user):
    reset_service.reset_attempt(tentativa, actor=admin_user, reason=MOTIVO)
    tentativa.refresh_from_db()

    with pytest.raises(reset_service.TentativaJaAnulada):
        reset_service.reset_attempt(tentativa, actor=admin_user, reason="de novo")

    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_RESET).count() == 1
    tentativa.refresh_from_db()
    assert tentativa.reset_reason == MOTIVO


def test_segundo_reset_nao_revoga_nem_reativa_de_novo(aprovada, admin_user):
    issue_certificate(aprovada, actor=admin_user)
    reset_service.reset_attempt(aprovada, actor=admin_user, reason=MOTIVO)
    aprovada.refresh_from_db()

    with pytest.raises(reset_service.TentativaJaAnulada):
        reset_service.reset_attempt(aprovada, actor=admin_user, reason="de novo")

    assert AuditLog.objects.filter(event=AuditEvent.CERTIFICATE_REVOKED).count() == 1
    assert (
        AuditLog.objects.filter(event=AuditEvent.ENROLLMENT_REACTIVATED).count() == 1
    )


@pytest.mark.django_db(transaction=True)
def test_dois_administradores_resetando_ao_mesmo_tempo(tentativa, admin_user):
    """
    PostgreSQL de verdade, duas threads.

    Sem select_for_update as duas leriam "nao anulada" ao mesmo tempo e
    produziriam dois eventos ATTEMPT_RESET para a mesma anulacao.
    """
    import threading

    resultados = []
    recusas = []

    def anular():
        try:
            resultados.append(
                reset_service.reset_attempt(
                    tentativa, actor=admin_user, reason=MOTIVO
                )
            )
        except reset_service.TentativaJaAnulada:
            recusas.append(True)
        finally:
            connection.close()

    linhas = [threading.Thread(target=anular) for _ in range(2)]
    for linha in linhas:
        linha.start()
    for linha in linhas:
        linha.join(timeout=30)

    assert len(resultados) == 1, "mais de um reset efetivo"
    assert len(recusas) == 1
    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_RESET).count() == 1


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------


def test_o_evento_registra_o_que_aconteceu_sem_o_motivo(aprovada, admin_user):
    """
    O motivo fica em ExamAttempt.reset_reason.

    Duplicar texto livre na trilha criaria duas versoes do mesmo fato para
    divergirem depois.
    """
    issue_certificate(aprovada, actor=admin_user)
    reset_service.reset_attempt(aprovada, actor=admin_user, reason=MOTIVO)

    evento = AuditLog.objects.get(event=AuditEvent.ATTEMPT_RESET)
    assert evento.entity_type == "ExamAttempt"
    assert evento.student_id == aprovada.student_id
    assert evento.metadata["certificate_revoked"] is True
    assert evento.metadata["enrollment_reactivated"] is True
    assert evento.metadata["attempt_number"] == aprovada.attempt_number
    assert MOTIVO not in str(evento.metadata)


def test_a_trilha_nao_recebe_resposta_nem_gabarito(aprovada, admin_user):
    reset_service.reset_attempt(aprovada, actor=admin_user, reason=MOTIVO)

    trilha = str(AuditLog.objects.get(event=AuditEvent.ATTEMPT_RESET).metadata).lower()
    for proibido in ("resposta", "gabarito", "senha", "password", "is_correct"):
        assert proibido not in trilha


# ---------------------------------------------------------------------------
# Resultado do aluno
# ---------------------------------------------------------------------------


def test_o_aluno_ve_que_a_tentativa_foi_anulada(
    student_client_logado, aprovada, admin_user
):
    reset_service.reset_attempt(aprovada, actor=admin_user, reason=MOTIVO)

    endereco = reverse(
        "student:attempt_result", kwargs={"public_id": aprovada.public_id}
    )
    corpo = student_client_logado.get(endereco).content.decode("utf-8")

    assert "anulada administrativamente" in corpo
    assert "Aprovado" not in corpo
    assert "Reprovado" not in corpo


def test_o_motivo_nao_aparece_para_o_aluno(
    student_client_logado, aprovada, admin_user
):
    """
    O motivo e nota administrativa. Pode conter juizo sobre a conduta do
    aluno, e nao e escrito para ele ler.
    """
    reset_service.reset_attempt(
        aprovada, actor=admin_user, reason="Suspeita de fraude apurada."
    )

    endereco = reverse(
        "student:attempt_result", kwargs={"public_id": aprovada.public_id}
    )
    corpo = student_client_logado.get(endereco).content.decode("utf-8")

    assert "Suspeita de fraude" not in corpo
