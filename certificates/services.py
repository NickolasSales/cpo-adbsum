"""
Emissao e revogacao de certificados.

Regra unica de quem pode receber
--------------------------------
Somente uma tentativa corrigida e aprovada. Nao existe caminho alternativo:
nem a tela do aluno, nem a do administrador, nem o Django Admin criam
Certificate sem passar por aqui.

Idempotencia e concorrencia
---------------------------
Emitir e uma acao de um clique numa pagina que o aluno pode recarregar. Dois
cliques, dois toques no celular ou duas abas nao podem produzir dois
documentos com codigos diferentes para a mesma conclusao — cada codigo extra
seria um certificado autentico e verificavel a mais circulando por engano.

A garantia vem em duas camadas:

    OneToOneField        o banco recusa a segunda linha
    select_for_update    a segunda requisicao espera a primeira terminar,
                         encontra o certificado pronto e o devolve

Sem o lock, duas requisicoes simultaneas leriam "nao existe" ao mesmo tempo e
a segunda morreria com IntegrityError na cara do aluno. Com ele, a segunda
simplesmente recebe o mesmo documento.
"""

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record
from certificates.models import (
    VERSAO_ATUAL_DO_MODELO,
    Certificate,
    CertificateStatus,
)
from common.exceptions import DomainError
from courses.models import Enrollment
from courses.services import complete_enrollment
from exams.models import AttemptResult, AttemptStatus, ExamAttempt, GradingStatus


class TentativaNaoAprovada(DomainError):
    """A tentativa nao satisfaz as condicoes para gerar certificado."""


class CertificadoRevogado(DomainError):
    """Operacao invalida sobre um certificado ja revogado."""


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------


def pode_emitir(attempt):
    """
    Se esta tentativa, como esta agora, geraria um certificado.

    Usada pela interface para decidir se o botao aparece. A validacao real
    acontece no servico: um botao escondido nao e controle de acesso.
    """
    return (
        attempt.status != AttemptStatus.RESET
        and attempt.grading_status == GradingStatus.GRADED
        and attempt.result == AttemptResult.APPROVED
    )


def certificado_da_tentativa(attempt):
    return Certificate.objects.filter(attempt=attempt).first()


def certificados_do_aluno(user):
    return (
        Certificate.objects.do_aluno(user)
        .select_related("attempt", "attempt__exam", "attempt__exam__module")
        .order_by("-issued_at")
    )


def _validar_emissivel(attempt):
    if attempt.status == AttemptStatus.RESET:
        raise TentativaNaoAprovada(
            "Esta tentativa foi anulada e nao gera certificado."
        )
    if attempt.grading_status != GradingStatus.GRADED:
        raise TentativaNaoAprovada(
            "A correcao desta tentativa ainda nao foi finalizada."
        )
    if attempt.result != AttemptResult.APPROVED:
        raise TentativaNaoAprovada(
            "Somente tentativas aprovadas geram certificado."
        )


# ---------------------------------------------------------------------------
# Emissao
# ---------------------------------------------------------------------------


def issue_certificate(attempt, *, actor=None, request=None):
    """
    Emite o certificado de uma tentativa aprovada.

    Devolve (certificado, emitido_agora). O segundo valor e False quando o
    certificado ja existia: quem chama usa isso para escolher a mensagem, sem
    precisar comparar datas.

    Efeitos, todos na mesma transacao:

        cria o Certificate com os textos congelados
        conclui a matricula do aluno naquele modulo e encerra o acesso
        registra CERTIFICATE_ISSUED
    """
    with transaction.atomic():
        travada = (
            ExamAttempt.objects.select_for_update()
            .select_related("student", "exam", "exam__module")
            .get(pk=attempt.pk)
        )

        existente = Certificate.objects.filter(attempt=travada).first()
        if existente is not None:
            # Segundo clique, segunda aba, ou a requisicao que perdeu a
            # corrida. Nao e erro: e o mesmo documento.
            return existente, False

        _validar_emissivel(travada)

        modulo = travada.exam.module
        certificado = Certificate.objects.create(
            attempt=travada,
            status=CertificateStatus.ACTIVE,
            student_name_snapshot=travada.student.full_name,
            module_name_snapshot=modulo.name,
            exam_title_snapshot=travada.exam.title,
            institution_name_snapshot=settings.INSTITUTION_NAME,
            template_version=VERSAO_ATUAL_DO_MODELO,
        )

        _encerrar_matricula(travada, actor=actor, request=request)

        record(
            AuditEvent.CERTIFICATE_ISSUED,
            request=request,
            actor=actor,
            student=travada.student,
            entity_type="Certificate",
            entity_id=certificado.pk,
            metadata={
                "module_id": modulo.pk,
                "module_code": modulo.code,
                "attempt_number": travada.attempt_number,
                "certificate_status": certificado.status,
            },
        )

    return certificado, True


def _encerrar_matricula(attempt, *, actor, request):
    """
    Conclui a matricula do aluno no modulo da prova.

    O lock e o mesmo padrao da tentativa: sem ele, duas conclusoes
    concorrentes poderiam gravar em cima uma da outra.
    """
    matricula = (
        Enrollment.objects.select_for_update()
        .select_related("module", "student")
        .filter(student=attempt.student, module=attempt.exam.module)
        .first()
    )
    if matricula is None:
        # Matricula removida entre a prova e a emissao. O certificado ainda
        # vale: ele atesta o que aconteceu, e nao o vinculo de hoje.
        return None
    return complete_enrollment(
        matricula, encerrar_acesso=True, actor=actor, request=request
    )


# ---------------------------------------------------------------------------
# Revogacao
# ---------------------------------------------------------------------------


def revoke_certificate(certificado, *, actor=None, request=None, motivo=""):
    """
    Revoga um certificado, preservando-o.

    Nunca apaga: o codigo antigo continua consultavel e passa a responder
    "revogado". Quem recebeu o documento em papel precisa conseguir descobrir
    que ele deixou de valer — e isso e impossivel se o codigo simplesmente
    desaparecer.

    Revogar NAO reativa a matricula automaticamente. Uma revogacao pode vir
    de erro administrativo, fraude ou correcao documental, e cada uma dessas
    pede uma decisao academica diferente.

    Devolve (certificado, revogado_agora).
    """
    with transaction.atomic():
        travado = (
            Certificate.objects.select_for_update()
            .select_related("attempt", "attempt__student")
            .get(pk=certificado.pk)
        )

        if travado.status == CertificateStatus.REVOKED:
            return travado, False

        travado.status = CertificateStatus.REVOKED
        travado.revoked_at = timezone.now()
        travado.revoked_by = actor if getattr(actor, "pk", None) else None
        travado.revocation_reason = (motivo or "").strip()
        travado.save(
            update_fields=[
                "status",
                "revoked_at",
                "revoked_by",
                "revocation_reason",
                "updated_at",
            ]
        )

        record(
            AuditEvent.CERTIFICATE_REVOKED,
            request=request,
            actor=actor,
            student=travado.attempt.student,
            entity_type="Certificate",
            entity_id=travado.pk,
            metadata={"certificate_status": travado.status},
        )

    return travado, True
