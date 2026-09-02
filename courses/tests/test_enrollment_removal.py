"""
Revogacao, exclusao e restauracao de matriculas (Etapa 9).

Tres perguntas atravessam o arquivo:

    1  o sistema distingue uma matricula que nunca produziu nada de uma que
       produziu historico academico?
    2  revogar tira o acesso de verdade, e nao apenas da lista?
    3  revogar a matricula deixa o certificado em paz?

A terceira e a mais delicada. Encerrar o vinculo academico e revogar o
documento que a instituicao assinou sao dois atos, e confundi-los faria uma
decisao administrativa de rotina invalidar um certificado em silencio.
"""

import pytest
from django.urls import reverse

from audit.models import AuditEvent, AuditLog
from common.exceptions import DomainError
from courses import services
from courses.models import Enrollment, EnrollmentStatus

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# revoke_enrollment
# ---------------------------------------------------------------------------


def test_revogar_encerra_vinculo_e_acesso(matricula, admin_user):
    revogada = services.revoke_enrollment(
        matricula, actor=admin_user, reason="Aluno transferido de turma."
    )

    assert revogada.status == EnrollmentStatus.REVOKED
    assert revogada.access_enabled is False
    assert revogada.revoked_at is not None
    assert revogada.revoked_by == admin_user
    assert revogada.revocation_reason == "Aluno transferido de turma."
    assert revogada.libera_acesso is False


def test_revogar_exige_motivo(matricula, admin_user):
    for vazio in ("", "   ", None):
        with pytest.raises(DomainError):
            services.revoke_enrollment(matricula, actor=admin_user, reason=vazio)

    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.ACTIVE


def test_revogar_recusa_motivo_longo_demais(matricula, admin_user):
    from common.texto import LIMITE_DO_MOTIVO

    with pytest.raises(DomainError):
        services.revoke_enrollment(
            matricula, actor=admin_user, reason="x" * (LIMITE_DO_MOTIVO + 1)
        )


def test_revogar_duas_vezes_levanta_conflito(matricula, admin_user):
    services.revoke_enrollment(matricula, actor=admin_user, reason="Transferido.")

    with pytest.raises(services.MatriculaJaRevogada):
        services.revoke_enrollment(matricula, actor=admin_user, reason="De novo.")

    assert (
        AuditLog.objects.filter(event=AuditEvent.ENROLLMENT_REVOKED).count() == 1
    )


def test_auditoria_da_revogacao_nao_repete_o_motivo(matricula, admin_user):
    services.revoke_enrollment(
        matricula, actor=admin_user, reason="Motivo bastante especifico."
    )

    evento = AuditLog.objects.filter(event=AuditEvent.ENROLLMENT_REVOKED).first()
    assert evento is not None
    assert evento.metadata == {"module_code": matricula.module.code}
    assert "Motivo bastante especifico." not in str(evento.metadata)


def test_revogada_perde_o_modulo_na_area_do_aluno(matricula, admin_user):
    aluno = matricula.student
    assert list(services.modulos_do_aluno(aluno)) == [matricula.module]

    services.revoke_enrollment(matricula, actor=admin_user, reason="Transferido.")

    assert list(services.modulos_do_aluno(aluno)) == []
    assert services.matricula_liberada_ou_none(aluno, matricula.module_id) is None


def test_revogada_nao_inicia_tentativa(prova_aberta, matricula, admin_user):
    from exams.services import SemAcessoAProva, start_attempt

    services.revoke_enrollment(matricula, actor=admin_user, reason="Transferido.")

    with pytest.raises(SemAcessoAProva):
        start_attempt(matricula.student, prova_aberta)


def test_revogada_perde_a_prova_pela_url(prova_aberta, matricula, admin_user):
    """404 na pratica: prova_visivel_ou_none passa a devolver None."""
    from exams.services import prova_visivel_ou_none

    assert prova_visivel_ou_none(matricula.student, prova_aberta.pk) is not None

    services.revoke_enrollment(matricula, actor=admin_user, reason="Transferido.")

    assert prova_visivel_ou_none(matricula.student, prova_aberta.pk) is None


def test_revogar_nao_toca_no_certificado(tentativa, admin_user):
    """
    Certificado ACTIVE com matricula REVOKED e um estado legitimo.

    O aluno concluiu o modulo e tem o documento; a instituicao encerrou o
    vinculo depois. Revogar o documento e outro ato, com outro fluxo.
    """
    from certificates.models import Certificate, CertificateStatus

    from exams.services import expire_attempt

    expire_attempt(tentativa)
    certificado = Certificate.objects.create(
        attempt=tentativa,
        student_name_snapshot=tentativa.student.full_name,
        module_name_snapshot=tentativa.exam.module.name,
        exam_title_snapshot=tentativa.exam.title,
        institution_name_snapshot="CPO AD Bras Sumare",
    )

    matricula = Enrollment.objects.get(
        student=tentativa.student, module=tentativa.exam.module
    )
    services.revoke_enrollment(matricula, actor=admin_user, reason="Transferido.")

    certificado.refresh_from_db()
    assert certificado.status == CertificateStatus.ACTIVE
    assert not AuditLog.objects.filter(
        event=AuditEvent.CERTIFICATE_REVOKED
    ).exists()


def test_revogar_preserva_as_tentativas(tentativa, admin_user):
    from exams.models import ExamAttempt

    matricula = Enrollment.objects.get(
        student=tentativa.student, module=tentativa.exam.module
    )
    services.revoke_enrollment(matricula, actor=admin_user, reason="Transferido.")

    assert ExamAttempt.objects.filter(pk=tentativa.pk).exists()


def test_reativar_generico_nao_desfaz_revogacao(matricula, admin_user):
    """
    A recusa e o ponto central.

    Se "Reativar" aceitasse REVOKED, ele desfaria um ato formal sem motivo,
    sem trilha propria e sem a checagem de certificado ativo — e um aluno ja
    certificado voltaria ao curso por um clique de rotina.
    """
    services.revoke_enrollment(matricula, actor=admin_user, reason="Transferido.")

    with pytest.raises(DomainError) as erro:
        services.reactivate_enrollment(matricula, actor=admin_user)

    assert "restaurar" in str(erro.value).lower()
    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.REVOKED


def test_matricular_de_novo_orienta_para_restaurar(matricula, admin_user):
    services.revoke_enrollment(matricula, actor=admin_user, reason="Transferido.")

    with pytest.raises(DomainError) as erro:
        services.create_enrollment(
            student=matricula.student, module=matricula.module
        )

    assert "restaurar matricula" in str(erro.value)


# ---------------------------------------------------------------------------
# can_delete_enrollment / delete_enrollment
# ---------------------------------------------------------------------------


def test_matricula_sem_historico_pode_ser_excluida(matricula, admin_user):
    assert services.can_delete_enrollment(matricula) == []

    pk = matricula.pk
    services.delete_enrollment(matricula, actor=admin_user)

    assert not Enrollment.objects.filter(pk=pk).exists()


def test_matricula_com_tentativa_nao_pode_ser_excluida(tentativa, admin_user):
    matricula = Enrollment.objects.get(
        student=tentativa.student, module=tentativa.exam.module
    )

    assert services.can_delete_enrollment(matricula)

    with pytest.raises(DomainError):
        services.delete_enrollment(matricula, actor=admin_user)

    assert Enrollment.objects.filter(pk=matricula.pk).exists()


def test_tentativa_anulada_ainda_impede_a_exclusao(tentativa, admin_user):
    """RESET nao apaga o registro de que o aluno esteve ali."""
    from exams.services import reset_attempt

    reset_attempt(tentativa, actor=admin_user, reason="Queda de energia.")

    matricula = Enrollment.objects.get(
        student=tentativa.student, module=tentativa.exam.module
    )
    assert services.can_delete_enrollment(matricula)


def test_historico_de_outro_modulo_nao_impede(
    tentativa, outro_modulo, admin_user
):
    """
    O criterio e por aluno E modulo.

    Uma tentativa no Modulo 1 nao pode bloquear a exclusao da matricula do
    mesmo aluno no Modulo 2 — sao vinculos distintos.
    """
    outra = services.create_enrollment(
        student=tentativa.student, module=outro_modulo
    )

    assert services.can_delete_enrollment(outra) == []
    services.delete_enrollment(outra, actor=admin_user)

    assert not Enrollment.objects.filter(pk=outra.pk).exists()


def test_exclusao_registra_auditoria_antes_do_delete(matricula, admin_user):
    pk = matricula.pk
    codigo = matricula.module.code

    services.delete_enrollment(matricula, actor=admin_user)

    evento = AuditLog.objects.filter(
        event=AuditEvent.ENROLLMENT_DELETED, entity_id=str(pk)
    ).first()
    assert evento is not None
    assert evento.metadata["module_code"] == codigo
    assert evento.metadata["previous_status"] == EnrollmentStatus.ACTIVE


def test_exclusao_recusada_nao_deixa_evento(tentativa, admin_user):
    matricula = Enrollment.objects.get(
        student=tentativa.student, module=tentativa.exam.module
    )

    with pytest.raises(DomainError):
        services.delete_enrollment(matricula, actor=admin_user)

    assert not AuditLog.objects.filter(
        event=AuditEvent.ENROLLMENT_DELETED
    ).exists()


# ---------------------------------------------------------------------------
# restore_revoked_enrollment
# ---------------------------------------------------------------------------


def test_restaurar_devolve_ativa_e_com_acesso(matricula, admin_user):
    services.revoke_enrollment(matricula, actor=admin_user, reason="Transferido.")
    restaurada = services.restore_revoked_enrollment(matricula, actor=admin_user)

    assert restaurada.status == EnrollmentStatus.ACTIVE
    assert restaurada.access_enabled is True
    assert restaurada.revoked_at is None
    assert restaurada.revoked_by is None
    assert restaurada.revocation_reason == ""
    assert AuditLog.objects.filter(
        event=AuditEvent.ENROLLMENT_RESTORED
    ).count() == 1


def test_restaurar_o_que_nao_esta_revogado_levanta_conflito(matricula, admin_user):
    with pytest.raises(services.MatriculaNaoRevogada):
        services.restore_revoked_enrollment(matricula, actor=admin_user)


def test_restaurar_recusa_com_certificado_ativo(tentativa, admin_user):
    """
    O documento afirma que o aluno concluiu.

    Devolve-lo ao curso contradiria o que a instituicao ja assinou. O caminho,
    se for mesmo o caso, e revogar o certificado primeiro.
    """
    from certificates.models import Certificate

    from exams.services import expire_attempt

    expire_attempt(tentativa)
    Certificate.objects.create(
        attempt=tentativa,
        student_name_snapshot=tentativa.student.full_name,
        module_name_snapshot=tentativa.exam.module.name,
        exam_title_snapshot=tentativa.exam.title,
        institution_name_snapshot="CPO AD Bras Sumare",
    )

    matricula = Enrollment.objects.get(
        student=tentativa.student, module=tentativa.exam.module
    )
    services.revoke_enrollment(matricula, actor=admin_user, reason="Transferido.")

    with pytest.raises(DomainError) as erro:
        services.restore_revoked_enrollment(matricula, actor=admin_user)

    assert "certificado ativo" in str(erro.value)
    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.REVOKED


def test_restaurar_aceita_com_certificado_revogado(tentativa, admin_user):
    """Certificado revogado nao afirma mais nada, e nao bloqueia."""
    from django.utils import timezone

    from certificates.models import Certificate, CertificateStatus

    from exams.services import expire_attempt

    expire_attempt(tentativa)
    Certificate.objects.create(
        attempt=tentativa,
        student_name_snapshot=tentativa.student.full_name,
        module_name_snapshot=tentativa.exam.module.name,
        exam_title_snapshot=tentativa.exam.title,
        institution_name_snapshot="CPO AD Bras Sumare",
        status=CertificateStatus.REVOKED,
        # revoked_at anda junto com o status por constraint desde a Etapa 6.
        revoked_at=timezone.now(),
    )

    matricula = Enrollment.objects.get(
        student=tentativa.student, module=tentativa.exam.module
    )
    services.revoke_enrollment(matricula, actor=admin_user, reason="Transferido.")
    restaurada = services.restore_revoked_enrollment(matricula, actor=admin_user)

    assert restaurada.status == EnrollmentStatus.ACTIVE


def test_restaurar_recusa_com_modulo_inativo(matricula, admin_user):
    services.revoke_enrollment(matricula, actor=admin_user, reason="Transferido.")
    services.disable_module(matricula.module, actor=admin_user)

    with pytest.raises(DomainError) as erro:
        services.restore_revoked_enrollment(matricula, actor=admin_user)

    assert "inativo" in str(erro.value)


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


def test_banco_recusa_revogada_sem_data(matricula):
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Enrollment.objects.filter(pk=matricula.pk).update(
                status=EnrollmentStatus.REVOKED, access_enabled=False
            )


def test_banco_recusa_revogada_com_acesso_liberado(matricula):
    """
    A regra vive em tres camadas, e esta e a que sobrevive a um UPDATE direto.

    liberadas() e revoke_enrollment ja garantem, mas os dois sao aplicacao.
    """
    from django.db import IntegrityError, transaction
    from django.utils import timezone

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Enrollment.objects.filter(pk=matricula.pk).update(
                status=EnrollmentStatus.REVOKED,
                access_enabled=True,
                revoked_at=timezone.now(),
            )


def test_banco_recusa_data_de_revogacao_sem_revogacao(matricula):
    from django.db import IntegrityError, transaction
    from django.utils import timezone

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Enrollment.objects.filter(pk=matricula.pk).update(
                revoked_at=timezone.now()
            )


# ---------------------------------------------------------------------------
# Views: POST, CSRF, ADMIN, IDOR
# ---------------------------------------------------------------------------


ROTAS_DE_ESCRITA = [
    "admin_panel:enrollment_revoke",
    "admin_panel:enrollment_delete",
    "admin_panel:enrollment_restore",
]


@pytest.mark.parametrize("rota", ROTAS_DE_ESCRITA)
def test_get_nas_rotas_de_escrita_devolve_405(
    admin_client_logado, matricula, rota
):
    resposta = admin_client_logado.get(reverse(rota, args=[matricula.pk]))
    assert resposta.status_code == 405


@pytest.mark.parametrize("rota", ROTAS_DE_ESCRITA)
def test_aluno_nao_acessa_as_rotas_de_escrita(
    student_client_logado, matricula, rota
):
    resposta = student_client_logado.post(reverse(rota, args=[matricula.pk]))
    assert resposta.status_code == 403


@pytest.mark.parametrize("rota", ROTAS_DE_ESCRITA)
def test_anonimo_e_mandado_para_o_login(client, matricula, rota):
    resposta = client.post(reverse(rota, args=[matricula.pk]))
    assert resposta.status_code == 302
    assert "/login/" in resposta["Location"]


def test_post_sem_csrf_e_recusado(admin_user, matricula):
    from django.test import Client

    cliente = Client(enforce_csrf_checks=True)
    cliente.force_login(admin_user)

    resposta = cliente.post(
        reverse("admin_panel:enrollment_revoke", args=[matricula.pk]),
        {"motivo": "Transferido."},
    )

    assert resposta.status_code == 403
    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.ACTIVE


def test_id_inexistente_devolve_404(admin_client_logado):
    resposta = admin_client_logado.post(
        reverse("admin_panel:enrollment_revoke", args=[999999]),
        {"motivo": "Transferido."},
    )
    assert resposta.status_code == 404


def test_post_forjado_em_matricula_com_historico_e_recusado(
    admin_client_logado, tentativa
):
    matricula = Enrollment.objects.get(
        student=tentativa.student, module=tentativa.exam.module
    )

    resposta = admin_client_logado.post(
        reverse("admin_panel:enrollment_delete", args=[matricula.pk])
    )

    assert resposta.status_code == 409
    assert Enrollment.objects.filter(pk=matricula.pk).exists()


def test_frontend_nao_escolhe_os_campos_de_revogacao(
    admin_client_logado, matricula, admin_user
):
    """
    Mass assignment: status, access_enabled, revoked_at e revoked_by vao no
    POST, e nenhum chega ao banco pelo valor enviado.
    """
    resposta = admin_client_logado.post(
        reverse("admin_panel:enrollment_revoke", args=[matricula.pk]),
        {
            "motivo": "Transferido.",
            "status": "ACTIVE",
            "access_enabled": "true",
            "revoked_at": "1999-01-01T00:00:00Z",
            "revoked_by": admin_user.pk + 500,
        },
    )
    assert resposta.status_code == 302

    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.REVOKED
    assert matricula.access_enabled is False
    assert matricula.revoked_at.year >= 2024
    assert matricula.revoked_by_id == admin_user.pk


def test_revogar_sem_motivo_pela_view_nao_revoga(admin_client_logado, matricula):
    resposta = admin_client_logado.post(
        reverse("admin_panel:enrollment_revoke", args=[matricula.pk]),
        {"motivo": "  "},
    )

    assert resposta.status_code == 302
    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.ACTIVE


def test_segunda_revogacao_pela_view_devolve_409(admin_client_logado, matricula):
    url = reverse("admin_panel:enrollment_revoke", args=[matricula.pk])
    dados = {"motivo": "Transferido."}

    assert admin_client_logado.post(url, dados).status_code == 302
    assert admin_client_logado.post(url, dados).status_code == 409


# ---------------------------------------------------------------------------
# Views: lista, filtro e matriz de acoes
# ---------------------------------------------------------------------------


def test_lista_esconde_revogadas_por_padrao(
    admin_client_logado, matricula, admin_user
):
    services.revoke_enrollment(matricula, actor=admin_user, reason="Transferido.")

    resposta = admin_client_logado.get(reverse("admin_panel:enrollment_list"))
    conteudo = resposta.content.decode()

    assert matricula.student.full_name not in conteudo


def test_filtro_revogadas_mostra_aluno_modulo_data_autor_e_motivo(
    admin_client_logado, matricula, admin_user
):
    services.revoke_enrollment(
        matricula, actor=admin_user, reason="Aluno transferido de turma."
    )

    resposta = admin_client_logado.get(
        reverse("admin_panel:enrollment_list"), {"situacao": "REVOKED"}
    )
    conteudo = resposta.content.decode()

    assert matricula.student.full_name in conteudo
    assert matricula.module.code in conteudo
    assert admin_user.full_name in conteudo
    assert "Aluno transferido de turma." in conteudo
    assert "Revogada" in conteudo


def test_filtro_todas_mostra_revogada_e_ativa(
    admin_client_logado, matricula, outro_modulo, admin_user
):
    outra = services.create_enrollment(
        student=matricula.student, module=outro_modulo
    )
    services.revoke_enrollment(matricula, actor=admin_user, reason="Transferido.")

    resposta = admin_client_logado.get(
        reverse("admin_panel:enrollment_list"), {"situacao": "todas"}
    )
    conteudo = resposta.content.decode()

    assert matricula.module.code in conteudo
    assert outra.module.code in conteudo


def test_concluida_nao_oferece_liberar_acesso_nem_reativar(
    admin_client_logado, matricula, admin_user
):
    """
    A inconsistencia que a Etapa 9 corrige.

    Antes, uma matricula "Concluida / Bloqueado" oferecia "Liberar acesso" e
    "Reativar" lado a lado — duas acoes que juntas nao descrevem nenhuma
    intencao administrativa real.
    """
    services.complete_enrollment(
        matricula, encerrar_acesso=True, actor=admin_user
    )

    resposta = admin_client_logado.get(reverse("admin_panel:enrollment_list"))
    conteudo = resposta.content.decode()

    assert "Concluida" in conteudo
    assert "Liberar acesso" not in conteudo
    assert "Reativar" not in conteudo
    assert "Ver historico" in conteudo
    assert reverse(
        "admin_panel:enrollment_revoke_confirm", args=[matricula.pk]
    ) in conteudo


def test_revogada_nao_oferece_reativar_generico(
    admin_client_logado, matricula, admin_user
):
    services.revoke_enrollment(matricula, actor=admin_user, reason="Transferido.")

    resposta = admin_client_logado.get(
        reverse("admin_panel:enrollment_list"), {"situacao": "REVOKED"}
    )
    conteudo = resposta.content.decode()

    assert "Restaurar matricula" in conteudo
    assert reverse(
        "admin_panel:enrollment_reactivate", args=[matricula.pk]
    ) not in conteudo


def test_lista_oferece_excluir_sem_historico(admin_client_logado, matricula):
    resposta = admin_client_logado.get(reverse("admin_panel:enrollment_list"))
    conteudo = resposta.content.decode()

    assert reverse(
        "admin_panel:enrollment_delete_confirm", args=[matricula.pk]
    ) in conteudo


def test_lista_nao_oferece_excluir_com_historico(admin_client_logado, tentativa):
    matricula = Enrollment.objects.get(
        student=tentativa.student, module=tentativa.exam.module
    )

    resposta = admin_client_logado.get(reverse("admin_panel:enrollment_list"))
    conteudo = resposta.content.decode()

    assert reverse(
        "admin_panel:enrollment_delete_confirm", args=[matricula.pk]
    ) not in conteudo
    assert reverse(
        "admin_panel:enrollment_revoke_confirm", args=[matricula.pk]
    ) in conteudo


def test_sem_historico_academico_e_falso_sem_a_anotacao(matricula):
    """Fail-closed: sem a anotacao, a tela nao oferece a exclusao."""
    crua = Enrollment.objects.get(pk=matricula.pk)
    assert crua.sem_historico_academico is False


def test_confirmacao_de_revogacao_avisa_sobre_o_certificado(
    admin_client_logado, matricula
):
    resposta = admin_client_logado.get(
        reverse("admin_panel:enrollment_revoke_confirm", args=[matricula.pk])
    )
    conteudo = resposta.content.decode()

    assert resposta.status_code == 200
    assert 'name="motivo"' in conteudo
    assert "NAO e revogado" in conteudo


def test_confirmacao_de_exclusao_esconde_o_formulario_com_historico(
    admin_client_logado, tentativa
):
    matricula = Enrollment.objects.get(
        student=tentativa.student, module=tentativa.exam.module
    )

    resposta = admin_client_logado.get(
        reverse("admin_panel:enrollment_delete_confirm", args=[matricula.pk])
    )
    conteudo = resposta.content.decode()

    assert "nao esta disponivel" in conteudo
    assert reverse(
        "admin_panel:enrollment_delete", args=[matricula.pk]
    ) not in conteudo
