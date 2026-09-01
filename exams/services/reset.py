"""
Anulacao administrativa de tentativa.

O que "resetar" significa aqui
------------------------------
Retirar a VALIDADE de uma tentativa, sem apagar o registro dela.

    status vira RESET
    reset_at, reset_by e reset_reason sao gravados

    respostas, pontos, nota, resultado, situacao da correcao e os carimbos
    de envio ou expiracao PERMANECEM

A distincao importa. A tentativa anulada continua respondendo "o que este
aluno escreveu, quanto tirou e quando entregou" — que e a informacao que
justifica a anulacao. O que ela deixa de ser e o resultado vigente, e deixa de
consumir uma das tentativas permitidas.

O que o reset NAO faz
---------------------
Nao abre a prova. Se a janela encerrou, se o modulo esta inativo ou se a
matricula nao da acesso, o aluno continua sem conseguir comecar — e isso e
correto. Resetar libera o SLOT da tentativa, e nada mais. Criar um atalho que
ignorasse a janela seria um bypass escondido dentro de uma funcao chamada
"reset".

A view avisa o administrador quando a janela ja fechou, para que ele saiba que
o aluno ainda nao podera refazer.

Efeitos em cascata
------------------
    certificado ACTIVE daquela tentativa  ->  revogado, na mesma transacao
    matricula concluida por ele           ->  avaliada para reativacao

A reativacao so acontece se NAO restar outro certificado ACTIVE do mesmo aluno
no mesmo modulo. Se restar, o aluno ainda tem comprovacao valida de conclusao,
e reabrir o modulo contradiria o documento que ele tem na mao.
"""

from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record
from common.exceptions import DomainError
from courses.models import Enrollment, EnrollmentStatus
from exams.models import AttemptStatus, ExamAttempt

LIMITE_DO_MOTIVO = 1000


class TentativaJaAnulada(DomainError):
    """A tentativa ja esta anulada; nada a fazer."""


def pode_resetar(attempt):
    return attempt.status != AttemptStatus.RESET


def validar_motivo(motivo):
    """
    O motivo e obrigatorio e nao pode ser so espaco.

    Resetar anula o trabalho de um aluno e pode revogar um certificado. Seis
    meses depois, "por que esta tentativa foi anulada?" precisa ter resposta
    no proprio registro — nao na memoria de quem clicou.
    """
    texto = (motivo or "").strip()
    if not texto:
        raise DomainError("Informe o motivo da anulacao.")
    if len(texto) > LIMITE_DO_MOTIVO:
        raise DomainError(
            "O motivo pode ter no maximo {} caracteres.".format(LIMITE_DO_MOTIVO)
        )
    return texto


def reset_attempt(attempt, *, actor, reason, request=None):
    """
    Anula uma tentativa, preservando o historico.

    Devolve (tentativa, resumo). O resumo diz o que aconteceu em cascata:

        {"certificate_revoked": bool, "enrollment_reactivated": bool}

    Idempotente: chamar de novo sobre uma tentativa ja anulada levanta
    TentativaJaAnulada e nao produz segundo evento, segunda revogacao nem
    segunda reativacao.
    """
    motivo = validar_motivo(reason)
    agora = timezone.now()

    with transaction.atomic():
        travada = (
            ExamAttempt.objects.select_for_update()
            .select_related("student", "exam", "exam__module")
            .get(pk=attempt.pk)
        )

        if travada.status == AttemptStatus.RESET:
            # Nao e "sucesso silencioso": quem clicou duas vezes precisa saber
            # que a segunda nao fez nada, e a view transforma isto em 409.
            raise TentativaJaAnulada("Esta tentativa ja foi anulada.")

        certificado_revogado = _revogar_certificado(
            travada, actor=actor, request=request
        )

        travada.status = AttemptStatus.RESET
        travada.reset_at = agora
        travada.reset_by = actor if getattr(actor, "pk", None) else None
        travada.reset_reason = motivo
        travada.save(
            update_fields=["status", "reset_at", "reset_by", "reset_reason"]
        )

        matricula_reativada = _avaliar_reativacao(
            travada, actor=actor, request=request
        )

        record(
            AuditEvent.ATTEMPT_RESET,
            request=request,
            actor=actor,
            student=travada.student,
            entity_type="ExamAttempt",
            entity_id=travada.pk,
            # O motivo NAO entra aqui: ele ja esta em ExamAttempt.reset_reason,
            # e duplicar texto livre na trilha so cria duas versoes do mesmo
            # fato para divergirem depois.
            metadata={
                "attempt_number": travada.attempt_number,
                "exam_id": travada.exam_id,
                "previous_status": attempt.status,
                "certificate_revoked": certificado_revogado,
                "enrollment_reactivated": matricula_reativada,
            },
        )

    resumo = {
        "certificate_revoked": certificado_revogado,
        "enrollment_reactivated": matricula_reativada,
        "janela_aberta": janela_aberta(travada.exam, agora),
    }
    return travada, resumo


# ---------------------------------------------------------------------------
# Cascata
# ---------------------------------------------------------------------------


def _revogar_certificado(attempt, *, actor, request):
    """
    Revoga o certificado daquela tentativa, se houver um valido.

    Import local: exams nao depende de certificates em tempo de importacao, e
    inverter isso criaria um ciclo — certificates ja importa exams.
    """
    from certificates.models import Certificate, CertificateStatus
    from certificates.services import revoke_certificate

    certificado = Certificate.objects.filter(attempt=attempt).first()
    if certificado is None or certificado.status == CertificateStatus.REVOKED:
        return False

    revoke_certificate(
        certificado,
        actor=actor,
        request=request,
        motivo="Tentativa resetada administrativamente.",
    )
    return True


def _avaliar_reativacao(attempt, *, actor, request):
    """
    Devolve a matricula ao estado ativo, quando faz sentido.

    Nao reativa se ainda existir outro certificado ACTIVE do mesmo aluno no
    mesmo modulo: o aluno continua com comprovacao valida de conclusao, e
    reabrir o modulo contradiria o documento que ele tem em maos.

    Nao reativa se o modulo estiver inativo: reativar a matricula nao pode
    ligar um modulo que a administracao desligou de proposito.
    """
    from certificates.models import Certificate, CertificateStatus

    matricula = (
        Enrollment.objects.select_for_update()
        .select_related("module", "student")
        .filter(student=attempt.student, module=attempt.exam.module)
        .first()
    )
    if matricula is None:
        return False
    if matricula.status != EnrollmentStatus.COMPLETED:
        # So faz sentido reabrir o que a emissao fechou. Uma matricula
        # INACTIVE foi desativada por outra decisao administrativa, e nao cabe
        # ao reset de tentativa desfaze-la.
        return False

    ainda_tem_certificado = Certificate.objects.filter(
        attempt__student=attempt.student,
        attempt__exam__module=attempt.exam.module,
        status=CertificateStatus.ACTIVE,
    ).exists()
    if ainda_tem_certificado:
        return False

    if not matricula.module.is_active:
        return False

    from courses.services import reactivate_enrollment

    reactivate_enrollment(matricula, actor=actor, request=request)
    return True


def janela_aberta(exam, agora):
    """Se a prova ainda aceita inicio de tentativa, pela janela dela."""
    from exams.models import ExamStatus

    if exam.status != ExamStatus.PUBLISHED:
        return False
    if exam.open_at is None or agora < exam.open_at:
        return False
    if exam.close_at is None or agora >= exam.close_at:
        return False
    return True
