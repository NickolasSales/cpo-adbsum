"""
Telas administrativas de tentativas.

Esta e a unica area do sistema que mostra gabarito, resposta do aluno, IP e
user-agent na mesma pagina. Metade destes testes existe para provar que ela
continua sendo a unica.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from audit.models import AuditEvent, AuditLog
from certificates.services import issue_certificate
from exams.models import AttemptResult, AttemptStatus, QuestionType
from exams.services import (
    autosave_answer,
    finalize_grading,
    save_manual_grade,
    submit_attempt,
)
from exams.services import reset as reset_service

pytestmark = pytest.mark.django_db

TEXTUAIS = {QuestionType.SHORT_TEXT, QuestionType.ESSAY}
MOTIVO = "Queda de energia durante a realizacao."


def url(nome, *args):
    return reverse("admin_panel:{}".format(nome), args=args)


def responder_tudo(tentativa):
    for linha in tentativa.questions.select_related("question").all():
        if linha.question.type in TEXTUAIS:
            autosave_answer(
                tentativa, question_token=str(linha.public_token), text="resposta"
            )
            continue
        certas = [
            o
            for o in linha.options.select_related("option").all()
            if o.option.is_correct
        ]
        autosave_answer(
            tentativa,
            question_token=str(linha.public_token),
            option_tokens=[str(o.public_token) for o in certas],
        )


@pytest.fixture
def aprovada(tentativa, admin_user):
    responder_tudo(tentativa)
    enviada = submit_attempt(tentativa)
    for linha in enviada.questions.select_related("question").all():
        if linha.question.type in TEXTUAIS:
            save_manual_grade(
                enviada,
                question_id=linha.pk,
                points=linha.points_snapshot,
                actor=admin_user,
            )
    fechada = finalize_grading(enviada, actor=admin_user)
    assert fechada.result == AttemptResult.APPROVED
    return fechada


# ---------------------------------------------------------------------------
# Acesso
# ---------------------------------------------------------------------------


def test_o_admin_ve_a_lista(admin_client_logado, tentativa):
    corpo = admin_client_logado.get(url("attempt_list")).content.decode("utf-8")

    assert tentativa.student.full_name in corpo
    assert tentativa.exam.title in corpo


def test_aluno_recebe_403_na_lista(student_client_logado, tentativa):
    assert student_client_logado.get(url("attempt_list")).status_code == 403


def test_aluno_recebe_403_no_detalhe(student_client_logado, tentativa):
    assert (
        student_client_logado.get(url("attempt_detail", tentativa.pk)).status_code
        == 403
    )


def test_anonimo_vai_para_o_login(client, tentativa):
    resposta = client.get(url("attempt_list"))
    assert resposta.status_code == 302
    assert "/login/" in resposta["Location"]


def test_tentativa_inexistente_responde_404(admin_client_logado):
    assert admin_client_logado.get(url("attempt_detail", 999999)).status_code == 404


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------


def test_filtro_por_situacao(admin_client_logado, tentativa, admin_user):
    reset_service.reset_attempt(tentativa, actor=admin_user, reason=MOTIVO)

    corpo = admin_client_logado.get(
        url("attempt_list"), {"situacao": AttemptStatus.RESET}
    ).content.decode("utf-8")
    assert tentativa.student.full_name in corpo

    corpo = admin_client_logado.get(
        url("attempt_list"), {"situacao": AttemptStatus.IN_PROGRESS}
    ).content.decode("utf-8")
    assert "Nenhuma tentativa encontrada" in corpo


def test_filtro_por_resultado(admin_client_logado, aprovada):
    corpo = admin_client_logado.get(
        url("attempt_list"), {"resultado": AttemptResult.FAILED}
    ).content.decode("utf-8")

    assert "Nenhuma tentativa encontrada" in corpo


def test_data_invalida_nao_derruba_a_tela(admin_client_logado, tentativa):
    resposta = admin_client_logado.get(
        url("attempt_list"), {"de": "31/02", "ate": "nao-e-data"}
    )
    assert resposta.status_code == 200


def test_busca_por_aluno(admin_client_logado, tentativa):
    corpo = admin_client_logado.get(
        url("attempt_list"), {"q": tentativa.student.email}
    ).content.decode("utf-8")
    assert tentativa.exam.title in corpo


# ---------------------------------------------------------------------------
# Detalhe
# ---------------------------------------------------------------------------


def test_o_detalhe_mostra_gabarito_e_resposta(admin_client_logado, aprovada):
    """
    Aqui PODE. Esta e a tela de inspecao administrativa, e sem o gabarito ao
    lado da resposta nao ha como conferir a correcao.
    """
    corpo = admin_client_logado.get(
        url("attempt_detail", aprovada.pk)
    ).content.decode("utf-8")

    assert "gabarito" in corpo
    assert "[marcada]" in corpo


def test_o_detalhe_mostra_ip_e_navegador(admin_client_logado, aprovada):
    corpo = admin_client_logado.get(
        url("attempt_detail", aprovada.pk)
    ).content.decode("utf-8")

    assert "IP" in corpo
    assert "Navegador" in corpo


def test_o_resultado_do_aluno_continua_sem_gabarito(
    student_client_logado, aprovada
):
    """
    A contraprova do teste acima: o que a tela administrativa mostra nao pode
    aparecer na tela do aluno.
    """
    endereco = reverse(
        "student:attempt_result", kwargs={"public_id": aprovada.public_id}
    )
    corpo = student_client_logado.get(endereco).content.decode("utf-8").lower()

    for proibido in ("gabarito", "is_correct", "[marcada]", "user-agent"):
        assert proibido not in corpo


def test_o_detalhe_liga_para_o_certificado(admin_client_logado, aprovada, admin_user):
    certificado, _ = issue_certificate(aprovada, actor=admin_user)

    corpo = admin_client_logado.get(
        url("attempt_detail", aprovada.pk)
    ).content.decode("utf-8")

    assert url("certificate_detail", certificado.pk) in corpo


# ---------------------------------------------------------------------------
# Reset pela tela
# ---------------------------------------------------------------------------


def test_resetar_por_get_responde_405(admin_client_logado, tentativa):
    assert admin_client_logado.get(url("attempt_reset", tentativa.pk)).status_code == 405

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.IN_PROGRESS


def test_resetar_sem_motivo_e_recusado(admin_client_logado, tentativa):
    admin_client_logado.post(url("attempt_reset", tentativa.pk), {"motivo": "   "})

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.IN_PROGRESS
    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_RESET).count() == 0


def test_resetar_com_motivo(admin_client_logado, tentativa, admin_user):
    resposta = admin_client_logado.post(
        url("attempt_reset", tentativa.pk), {"motivo": MOTIVO}
    )

    assert resposta.status_code == 302
    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.RESET
    assert tentativa.reset_by == admin_user
    assert tentativa.reset_reason == MOTIVO


def test_segundo_reset_responde_409(admin_client_logado, tentativa):
    admin_client_logado.post(url("attempt_reset", tentativa.pk), {"motivo": MOTIVO})
    resposta = admin_client_logado.post(
        url("attempt_reset", tentativa.pk), {"motivo": "de novo"}
    )

    assert resposta.status_code == 409
    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_RESET).count() == 1


def test_aluno_nao_reseta(student_client_logado, tentativa):
    resposta = student_client_logado.post(
        url("attempt_reset", tentativa.pk), {"motivo": MOTIVO}
    )

    assert resposta.status_code == 403
    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.IN_PROGRESS


def test_o_navegador_nao_escolhe_o_estado_resultante(
    admin_client_logado, aprovada
):
    """
    Mass assignment no reset.

    O POST manda status, nota e resultado forjados. O servidor decide tudo:
    o unico campo que ele le do corpo e o motivo.
    """
    admin_client_logado.post(
        url("attempt_reset", aprovada.pk),
        {
            "motivo": MOTIVO,
            "status": "SUBMITTED",
            "final_score": "10.000000",
            "result": "APPROVED",
            "grading_status": "PENDING",
            "obtained_points": "999",
        },
    )

    aprovada.refresh_from_db()
    assert aprovada.status == AttemptStatus.RESET
    # A nota e o resultado sao preservados como historico, e nao substituidos
    # pelo que veio do navegador.
    assert aprovada.obtained_points != Decimal("999")


def test_o_motivo_com_script_e_escapado(admin_client_logado, tentativa):
    script = "<script>alert(1)</script>"
    admin_client_logado.post(url("attempt_reset", tentativa.pk), {"motivo": script})

    corpo = admin_client_logado.get(
        url("attempt_detail", tentativa.pk)
    ).content.decode("utf-8")

    assert script not in corpo
    assert "&lt;script&gt;" in corpo


def test_a_tela_avisa_quando_a_janela_ja_fechou(
    admin_client_logado, tentativa, prova_aberta
):
    from django.utils import timezone

    from exams.models import Exam

    Exam.objects.filter(pk=prova_aberta.pk).update(
        close_at=timezone.now() - timezone.timedelta(minutes=1)
    )

    resposta = admin_client_logado.post(
        url("attempt_reset", tentativa.pk), {"motivo": MOTIVO}, follow=True
    )
    corpo = resposta.content.decode("utf-8")

    assert "janela da prova esta encerrada" in corpo
    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.RESET
