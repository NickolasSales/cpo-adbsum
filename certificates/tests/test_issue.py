"""
Emissao: quem pode, o que muda, e o que acontece quando clicam duas vezes.

O ponto que estes testes protegem e o efeito colateral. Emitir nao cria so uma
linha: conclui a matricula e encerra o acesso ao modulo. Se a emissao virasse
nao-idempotente, ou se um caminho conseguisse emitir sem aprovacao, o estrago
seria academico, e nao tecnico.
"""

import pytest
from django.db import connection

from audit.models import AuditEvent, AuditLog
from certificates.models import Certificate, CertificateStatus
from certificates.services import (
    TentativaNaoAprovada,
    issue_certificate,
    pode_emitir,
    revoke_certificate,
)
from courses.models import Enrollment, EnrollmentStatus
from exams.models import AttemptStatus, GradingStatus

pytestmark = pytest.mark.django_db


def matricula_de(tentativa):
    return Enrollment.objects.get(
        student=tentativa.student, module=tentativa.exam.module
    )


# ---------------------------------------------------------------------------
# Quem pode receber
# ---------------------------------------------------------------------------


def test_aprovada_emite(tentativa_aprovada, admin_user):
    assert pode_emitir(tentativa_aprovada) is True

    certificado, criado = issue_certificate(tentativa_aprovada, actor=admin_user)

    assert criado is True
    assert certificado.status == CertificateStatus.ACTIVE
    assert Certificate.objects.count() == 1


def test_reprovada_nao_emite(tentativa_reprovada, admin_user):
    assert pode_emitir(tentativa_reprovada) is False

    with pytest.raises(TentativaNaoAprovada):
        issue_certificate(tentativa_reprovada, actor=admin_user)

    assert Certificate.objects.count() == 0


def test_reprovada_nao_conclui_a_matricula(tentativa_reprovada, admin_user):
    """
    Reprovar nao encerra o modulo.

    Deixar a matricula ativa e o que permite o reset administrativo e uma nova
    tentativa autorizada mais adiante.
    """
    with pytest.raises(TentativaNaoAprovada):
        issue_certificate(tentativa_reprovada, actor=admin_user)

    matricula = matricula_de(tentativa_reprovada)
    assert matricula.status == EnrollmentStatus.ACTIVE
    assert matricula.access_enabled is True


def test_aguardando_avaliador_nao_emite(tentativa, admin_user):
    from certificates.tests.conftest import responder_tudo
    from exams.services import submit_attempt

    responder_tudo(tentativa, certo=True)
    enviada = submit_attempt(tentativa)

    assert enviada.grading_status == GradingStatus.AWAITING_REVIEW
    with pytest.raises(TentativaNaoAprovada):
        issue_certificate(enviada, actor=admin_user)
    assert Certificate.objects.count() == 0


def test_anulada_nao_emite(tentativa_aprovada, admin_user):
    # status e reset_at andam juntos: a constraint tentativa_anulacao_coerente
    # recusa RESET sem data de anulacao. Gravar so o status produzia uma
    # tentativa que se diz anulada sem dizer quando — e a constraint da Etapa 7
    # passou a barrar isso no banco.
    from django.utils import timezone

    tentativa_aprovada.status = AttemptStatus.RESET
    tentativa_aprovada.reset_at = timezone.now()
    tentativa_aprovada.save(update_fields=["status", "reset_at"])

    assert pode_emitir(tentativa_aprovada) is False
    with pytest.raises(TentativaNaoAprovada):
        issue_certificate(tentativa_aprovada, actor=admin_user)


# ---------------------------------------------------------------------------
# Efeito na matricula
# ---------------------------------------------------------------------------


def test_emitir_conclui_a_matricula_e_encerra_o_acesso(
    tentativa_aprovada, admin_user
):
    antes = matricula_de(tentativa_aprovada)
    assert antes.status == EnrollmentStatus.ACTIVE
    assert antes.access_enabled is True

    issue_certificate(tentativa_aprovada, actor=admin_user)

    depois = matricula_de(tentativa_aprovada)
    assert depois.status == EnrollmentStatus.COMPLETED
    assert depois.access_enabled is False
    assert depois.libera_acesso is False


def test_o_modulo_concluido_sai_das_matriculas_liberadas(
    tentativa_aprovada, admin_user
):
    issue_certificate(tentativa_aprovada, actor=admin_user)

    liberadas = Enrollment.objects.liberadas().filter(
        student=tentativa_aprovada.student
    )
    assert liberadas.count() == 0


def test_outro_modulo_ativo_continua_funcionando(
    tentativa_aprovada, admin_user, outro_modulo
):
    """
    Concluir um modulo nao pode encerrar o curso inteiro.

    Um aluno costuma cursar varios modulos ao mesmo tempo; fechar todos porque
    um terminou seria o pior tipo de bug — silencioso e so percebido pelo
    aluno que ficou sem acesso.
    """
    from courses.services import create_enrollment

    outra = create_enrollment(student=tentativa_aprovada.student, module=outro_modulo)

    issue_certificate(tentativa_aprovada, actor=admin_user)

    outra.refresh_from_db()
    assert outra.status == EnrollmentStatus.ACTIVE
    assert outra.access_enabled is True
    assert outra.libera_acesso is True


def test_o_usuario_continua_ativo(tentativa_aprovada, admin_user):
    """
    Concluir modulo nao desativa a conta.

    O aluno precisa continuar entrando para baixar o certificado de novo,
    consultar resultados e cursar outros modulos.
    """
    issue_certificate(tentativa_aprovada, actor=admin_user)

    aluno = tentativa_aprovada.student
    aluno.refresh_from_db()
    assert aluno.is_active is True


def test_emite_mesmo_sem_matricula(tentativa_aprovada, admin_user):
    """
    O certificado atesta o que aconteceu, nao o vinculo de hoje.

    Se a matricula foi removida entre a prova e a emissao, o documento
    continua valido — o aluno realmente fez e passou naquela avaliacao.
    """
    matricula_de(tentativa_aprovada).delete()

    certificado, criado = issue_certificate(tentativa_aprovada, actor=admin_user)
    assert criado is True
    assert certificado.status == CertificateStatus.ACTIVE


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------


def test_emitir_duas_vezes_devolve_o_mesmo_documento(tentativa_aprovada, admin_user):
    primeiro, criado_1 = issue_certificate(tentativa_aprovada, actor=admin_user)
    segundo, criado_2 = issue_certificate(tentativa_aprovada, actor=admin_user)

    assert criado_1 is True
    assert criado_2 is False
    assert primeiro.pk == segundo.pk
    assert primeiro.verification_code == segundo.verification_code
    assert Certificate.objects.count() == 1


def test_o_segundo_clique_nao_gera_segundo_evento(tentativa_aprovada, admin_user):
    issue_certificate(tentativa_aprovada, actor=admin_user)
    issue_certificate(tentativa_aprovada, actor=admin_user)

    eventos = AuditLog.objects.filter(event=AuditEvent.CERTIFICATE_ISSUED)
    assert eventos.count() == 1


def test_o_segundo_clique_nao_reconclui_a_matricula(tentativa_aprovada, admin_user):
    issue_certificate(tentativa_aprovada, actor=admin_user)
    issue_certificate(tentativa_aprovada, actor=admin_user)

    concluidas = AuditLog.objects.filter(event=AuditEvent.ENROLLMENT_COMPLETED)
    assert concluidas.count() == 1


# ---------------------------------------------------------------------------
# Concorrencia real
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_dois_pedidos_simultaneos_produzem_um_unico_certificado(
    tentativa_aprovada, admin_user
):
    """
    Duas threads, PostgreSQL de verdade, sem transacao de teste por cima.

    E o cenario do duplo toque no celular: duas requisicoes entram juntas.
    Sem select_for_update as duas leriam "nao existe" ao mesmo tempo e a
    segunda quebraria com IntegrityError na cara do aluno. Com o lock, a
    segunda espera, encontra o documento pronto e o devolve.
    """
    import threading

    resultados = []
    erros = []

    def emitir():
        try:
            resultados.append(issue_certificate(tentativa_aprovada, actor=admin_user))
        except Exception as erro:  # pragma: no cover - so aparece se regredir
            erros.append(erro)
        finally:
            connection.close()

    linhas = [threading.Thread(target=emitir) for _ in range(2)]
    for linha in linhas:
        linha.start()
    for linha in linhas:
        linha.join(timeout=30)

    assert not erros, "emissao concorrente falhou: {}".format(erros)
    assert Certificate.objects.count() == 1
    assert len({c.pk for c, _ in resultados}) == 1
    assert sum(1 for _, criado in resultados if criado) == 1
    assert AuditLog.objects.filter(event=AuditEvent.CERTIFICATE_ISSUED).count() == 1


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------


def test_a_emissao_e_auditada_sem_dado_sensivel(tentativa_aprovada, admin_user):
    certificado, _ = issue_certificate(tentativa_aprovada, actor=admin_user)

    evento = AuditLog.objects.get(event=AuditEvent.CERTIFICATE_ISSUED)
    assert evento.entity_type == "Certificate"
    assert evento.entity_id == str(certificado.pk)
    assert evento.student_id == tentativa_aprovada.student_id

    trilha = str(evento.metadata)
    assert tentativa_aprovada.student.email not in trilha
    assert str(certificado.verification_code) not in trilha
    for proibido in ("resposta", "gabarito", "senha", "password", "pdf"):
        assert proibido not in trilha.lower()


def test_download_nao_gera_evento(certificado, admin_user):
    """
    Decisao deliberada: baixar nao entra na trilha.

    Um certificado carrega QR Code e pode ser aberto por leitor, robo ou
    pre-visualizador de link. Auditar cada acesso encheria a trilha de ruido e
    esconderia os eventos que importam.
    """
    from certificates.pdf import render_certificate_pdf

    antes = AuditLog.objects.count()
    render_certificate_pdf(certificado)
    assert AuditLog.objects.count() == antes


# ---------------------------------------------------------------------------
# Revogacao
# ---------------------------------------------------------------------------


def test_revogar_preserva_o_documento(certificado, admin_user):
    revogado, mudou = revoke_certificate(
        certificado, actor=admin_user, motivo="Erro administrativo."
    )

    assert mudou is True
    assert revogado.status == CertificateStatus.REVOKED
    assert revogado.revoked_at is not None
    assert revogado.revoked_by == admin_user
    assert revogado.revocation_reason == "Erro administrativo."
    # Nada foi apagado: o codigo continua consultavel.
    assert Certificate.objects.filter(pk=certificado.pk).exists()
    assert revogado.verification_code == certificado.verification_code


def test_revogar_duas_vezes_e_idempotente(certificado, admin_user):
    revoke_certificate(certificado, actor=admin_user, motivo="Primeira.")
    _, mudou = revoke_certificate(certificado, actor=admin_user, motivo="Segunda.")

    assert mudou is False
    assert AuditLog.objects.filter(event=AuditEvent.CERTIFICATE_REVOKED).count() == 1

    certificado.refresh_from_db()
    assert certificado.revocation_reason == "Primeira."


def test_revogar_nao_reativa_a_matricula(certificado, tentativa_aprovada, admin_user):
    """
    Revogar e revogar. Reativar aluno e outra decisao.

    A revogacao pode vir de fraude, de erro administrativo ou de correcao
    documental — e cada uma pede um encaminhamento academico diferente.
    """
    revoke_certificate(certificado, actor=admin_user, motivo="Fraude apurada.")

    matricula = matricula_de(tentativa_aprovada)
    assert matricula.status == EnrollmentStatus.COMPLETED
    assert matricula.access_enabled is False
