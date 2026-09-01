"""
As telas: validacao publica, area do aluno e painel administrativo.

A maior parte destes testes e sobre o que NAO aparece e sobre quem NAO entra.
Um certificado e o unico objeto do sistema com uma pagina sem autenticacao, e
o unico que vira arquivo para download — as duas superficies onde um vazamento
custa mais caro.
"""

import uuid

import pytest
from django.urls import reverse

from audit.models import AuditEvent, AuditLog
from certificates.models import (
    VERSAO_ATUAL_DO_MODELO,
    Certificate,
    CertificateStatus,
)
from certificates.services import revoke_certificate

pytestmark = pytest.mark.django_db


def url_publica(certificado):
    return reverse(
        "certificates:validate",
        kwargs={"verification_code": certificado.verification_code},
    )


# ---------------------------------------------------------------------------
# Validacao publica
# ---------------------------------------------------------------------------


def test_certificado_valido_responde_200_e_diz_que_vale(client, certificado):
    resposta = client.get(url_publica(certificado))
    corpo = resposta.content.decode("utf-8")

    assert resposta.status_code == 200
    assert "Certificado valido" in corpo
    assert certificado.student_name_snapshot in corpo
    # A pagina mostra o nome do modulo COMO ELE SAI NO DOCUMENTO. A partir da
    # Etapa 8 esse e o nome por extenso, e nao o nome interno curto: quem
    # confere um papel precisa reconhecer o que esta lendo nele.
    assert certificado.modulo_impresso in corpo
    assert str(certificado.verification_code) in corpo


def test_a_validacao_nao_exige_login(client, certificado):
    """
    Sem sessao, sem redirecionamento para o login.

    Quem confere um certificado normalmente nao tem conta no sistema.
    """
    resposta = client.get(url_publica(certificado))

    assert resposta.status_code == 200
    assert "/login/" not in resposta.get("Location", "")


def test_certificado_revogado_diz_que_foi_revogado(client, certificado, admin_user):
    revoke_certificate(certificado, actor=admin_user, motivo="Erro administrativo.")

    resposta = client.get(url_publica(certificado))
    corpo = resposta.content.decode("utf-8")

    assert resposta.status_code == 200
    assert "Certificado revogado" in corpo
    assert "Certificado valido" not in corpo


def test_o_revogado_ainda_identifica_o_documento(client, certificado, admin_user):
    """
    Quem esta com o papel na mao precisa saber que e AQUELE que caiu.

    Uma pagina generica de "invalido" deixaria a duvida de ter digitado errado
    o codigo.
    """
    revoke_certificate(certificado, actor=admin_user, motivo="Fraude.")

    corpo = client.get(url_publica(certificado)).content.decode("utf-8")

    assert certificado.student_name_snapshot in corpo
    # A pagina mostra o nome do modulo COMO ELE SAI NO DOCUMENTO. A partir da
    # Etapa 8 esse e o nome por extenso, e nao o nome interno curto: quem
    # confere um papel precisa reconhecer o que esta lendo nele.
    assert certificado.modulo_impresso in corpo
    assert str(certificado.verification_code) in corpo


def test_o_motivo_da_revogacao_nao_e_publico(client, certificado, admin_user):
    """
    O motivo e nota administrativa interna.

    "Fraude apurada em 12/03" numa pagina publica seria acusacao exposta a
    qualquer pessoa com o codigo.
    """
    revoke_certificate(
        certificado, actor=admin_user, motivo="Fraude apurada pela secretaria."
    )

    corpo = client.get(url_publica(certificado)).content.decode("utf-8")

    assert "Fraude apurada" not in corpo


def test_codigo_inexistente_responde_404(client):
    endereco = reverse(
        "certificates:validate", kwargs={"verification_code": uuid.uuid4()}
    )

    assert client.get(endereco).status_code == 404


def test_a_pagina_publica_nao_vaza_dado_algum_do_aluno(
    client, certificado, tentativa_aprovada
):
    """
    A lista do que nao pode aparecer, verificada uma a uma.

    Esta pagina e visivel para qualquer pessoa que tenha o codigo — inclusive
    para quem o encontrou numa foto de rede social.
    """
    corpo = client.get(url_publica(certificado)).content.decode("utf-8")
    aluno = tentativa_aprovada.student

    assert aluno.email not in corpo
    assert str(tentativa_aprovada.public_id) not in corpo
    assert "Nota" not in corpo
    assert "nota" not in corpo
    assert str(tentativa_aprovada.final_score) not in corpo
    assert str(aluno.pk) not in corpo.replace(
        str(certificado.verification_code), ""
    )
    for proibido in ("user-agent", "user_agent", "Tentativa n", "gabarito"):
        assert proibido not in corpo


def test_a_pagina_publica_nao_e_indexavel(client, certificado):
    corpo = client.get(url_publica(certificado)).content.decode("utf-8")

    assert 'name="robots"' in corpo
    assert "noindex" in corpo


# ---------------------------------------------------------------------------
# Area do aluno
# ---------------------------------------------------------------------------


def test_o_aluno_ve_o_proprio_certificado(student_client_logado, certificado):
    resposta = student_client_logado.get(reverse("student:certificate_list"))
    corpo = resposta.content.decode("utf-8")

    assert resposta.status_code == 200
    # A pagina mostra o nome do modulo COMO ELE SAI NO DOCUMENTO. A partir da
    # Etapa 8 esse e o nome por extenso, e nao o nome interno curto: quem
    # confere um papel precisa reconhecer o que esta lendo nele.
    assert certificado.modulo_impresso in corpo


def test_a_lista_continua_acessivel_com_o_modulo_concluido(
    student_client_logado, certificado
):
    """
    Perder o acesso ao modulo nao pode significar perder o documento.

    A emissao encerra o acesso academico; a lista de certificados nao depende
    da matricula.
    """
    from courses.models import Enrollment, EnrollmentStatus

    matricula = Enrollment.objects.get(student=certificado.attempt.student)
    assert matricula.status == EnrollmentStatus.COMPLETED

    resposta = student_client_logado.get(reverse("student:certificate_list"))
    assert resposta.status_code == 200


def test_o_aluno_baixa_o_proprio_pdf(student_client_logado, certificado):
    endereco = reverse(
        "student:certificate_download",
        kwargs={"verification_code": certificado.verification_code},
    )
    resposta = student_client_logado.get(endereco)

    assert resposta.status_code == 200
    assert resposta["Content-Type"] == "application/pdf"
    assert "attachment;" in resposta["Content-Disposition"]
    assert resposta.content[:5] == b"%PDF-"


def test_o_pdf_nao_fica_em_cache_compartilhado(student_client_logado, certificado):
    endereco = reverse(
        "student:certificate_download",
        kwargs={"verification_code": certificado.verification_code},
    )
    resposta = student_client_logado.get(endereco)

    assert "no-store" in resposta["Cache-Control"]
    assert "private" in resposta["Cache-Control"]


def test_certificado_revogado_nao_vira_pdf_para_o_aluno(
    student_client_logado, certificado, admin_user
):
    """
    409, e nao o arquivo.

    Entregar o PDF de um documento sem validade seria entregar algo que parece
    valido: a pessoa imprime e apresenta sem nunca conferir o QR.
    """
    revoke_certificate(certificado, actor=admin_user, motivo="Erro.")

    endereco = reverse(
        "student:certificate_download",
        kwargs={"verification_code": certificado.verification_code},
    )
    resposta = student_client_logado.get(endereco)

    assert resposta.status_code == 409
    assert resposta.content[:5] != b"%PDF-"


def test_certificado_de_outro_aluno_responde_404(client, certificado, outro_student):
    """
    404, e nao 403.

    403 confirmaria que aquele codigo existe — informacao que o dono de outro
    certificado nao precisa ter.
    """
    client.force_login(outro_student)

    endereco = reverse(
        "student:certificate_download",
        kwargs={"verification_code": certificado.verification_code},
    )
    assert client.get(endereco).status_code == 404


def test_emitir_e_somente_post(student_client_logado, tentativa_aprovada):
    endereco = reverse(
        "student:certificate_issue",
        kwargs={"public_id": tentativa_aprovada.public_id},
    )

    assert student_client_logado.get(endereco).status_code == 405
    assert Certificate.objects.count() == 0


def test_o_aluno_emite_pela_tela(student_client_logado, tentativa_aprovada):
    endereco = reverse(
        "student:certificate_issue",
        kwargs={"public_id": tentativa_aprovada.public_id},
    )
    resposta = student_client_logado.post(endereco)

    assert resposta.status_code == 302
    assert Certificate.objects.count() == 1


def test_emitir_tentativa_de_outro_aluno_responde_404(
    client, tentativa_aprovada, outro_student
):
    client.force_login(outro_student)

    endereco = reverse(
        "student:certificate_issue",
        kwargs={"public_id": tentativa_aprovada.public_id},
    )
    assert client.post(endereco).status_code == 404
    assert Certificate.objects.count() == 0


def test_o_navegador_nao_escolhe_nada_do_certificado(
    student_client_logado, tentativa_aprovada
):
    """
    Mass assignment.

    O POST manda status, codigo e nomes forjados. O servidor constroi tudo a
    partir da tentativa e ignora o corpo inteiro.
    """
    codigo_forjado = uuid.uuid4()
    endereco = reverse(
        "student:certificate_issue",
        kwargs={"public_id": tentativa_aprovada.public_id},
    )
    student_client_logado.post(
        endereco,
        {
            "status": CertificateStatus.REVOKED,
            "verification_code": str(codigo_forjado),
            "student_name_snapshot": "Outro Nome",
            "institution_name_snapshot": "Outra Instituicao",
            "template_version": 99,
        },
    )

    certificado = Certificate.objects.get()
    assert certificado.status == CertificateStatus.ACTIVE
    assert certificado.verification_code != codigo_forjado
    assert certificado.student_name_snapshot == tentativa_aprovada.student.full_name
    assert certificado.template_version == VERSAO_ATUAL_DO_MODELO


def test_emitir_reprovada_pela_tela_e_recusado(
    student_client_logado, tentativa_reprovada
):
    endereco = reverse(
        "student:certificate_issue",
        kwargs={"public_id": tentativa_reprovada.public_id},
    )
    resposta = student_client_logado.post(endereco, follow=True)

    assert Certificate.objects.count() == 0
    assert "aprovadas" in resposta.content.decode("utf-8")


# ---------------------------------------------------------------------------
# Painel administrativo
# ---------------------------------------------------------------------------


def test_a_lista_administrativa_exige_admin(student_client_logado, certificado):
    resposta = student_client_logado.get(reverse("admin_panel:certificate_list"))

    assert resposta.status_code == 403


def test_o_anonimo_vai_para_o_login(client, certificado):
    resposta = client.get(reverse("admin_panel:certificate_list"))

    assert resposta.status_code == 302
    assert "/login/" in resposta["Location"]


def test_o_admin_ve_a_lista(admin_client_logado, certificado):
    corpo = admin_client_logado.get(
        reverse("admin_panel:certificate_list")
    ).content.decode("utf-8")

    assert certificado.student_name_snapshot in corpo
    assert "Valido" in corpo


def test_a_busca_encontra_pelo_codigo(admin_client_logado, certificado):
    """
    E assim que um certificado chega ate a administracao: alguem liga com o
    papel na mao e le o codigo.
    """
    corpo = admin_client_logado.get(
        reverse("admin_panel:certificate_list"),
        {"q": str(certificado.verification_code)},
    ).content.decode("utf-8")

    assert certificado.student_name_snapshot in corpo


def test_busca_que_nao_e_uuid_nao_derruba_a_tela(admin_client_logado, certificado):
    resposta = admin_client_logado.get(
        reverse("admin_panel:certificate_list"), {"q": "nao-e-um-uuid"}
    )

    assert resposta.status_code == 200


def test_o_detalhe_mostra_a_url_publica(admin_client_logado, certificado, settings):
    settings.SITE_URL = "https://cpoadsum.nexeeo.com"

    corpo = admin_client_logado.get(
        reverse("admin_panel:certificate_detail", args=[certificado.pk])
    ).content.decode("utf-8")

    assert "https://cpoadsum.nexeeo.com/certificados/validar/" in corpo


def test_detalhe_inexistente_responde_404(admin_client_logado):
    assert (
        admin_client_logado.get(
            reverse("admin_panel:certificate_detail", args=[999999])
        ).status_code
        == 404
    )


def test_revogar_e_somente_post(admin_client_logado, certificado):
    endereco = reverse("admin_panel:certificate_revoke", args=[certificado.pk])

    assert admin_client_logado.get(endereco).status_code == 405

    certificado.refresh_from_db()
    assert certificado.status == CertificateStatus.ACTIVE


def test_revogar_exige_motivo(admin_client_logado, certificado):
    endereco = reverse("admin_panel:certificate_revoke", args=[certificado.pk])
    admin_client_logado.post(endereco, {"motivo": "   "})

    certificado.refresh_from_db()
    assert certificado.status == CertificateStatus.ACTIVE
    assert AuditLog.objects.filter(event=AuditEvent.CERTIFICATE_REVOKED).count() == 0


def test_o_admin_revoga(admin_client_logado, certificado, admin_user):
    endereco = reverse("admin_panel:certificate_revoke", args=[certificado.pk])
    resposta = admin_client_logado.post(endereco, {"motivo": "Erro administrativo."})

    assert resposta.status_code == 302
    certificado.refresh_from_db()
    assert certificado.status == CertificateStatus.REVOKED
    assert certificado.revoked_by == admin_user


def test_aluno_nao_revoga(student_client_logado, certificado):
    endereco = reverse("admin_panel:certificate_revoke", args=[certificado.pk])
    resposta = student_client_logado.post(endereco, {"motivo": "quero"})

    assert resposta.status_code == 403
    certificado.refresh_from_db()
    assert certificado.status == CertificateStatus.ACTIVE


def test_o_admin_baixa_o_pdf(admin_client_logado, certificado):
    endereco = reverse(
        "admin_panel:certificate_download_admin", args=[certificado.pk]
    )
    resposta = admin_client_logado.get(endereco)

    assert resposta.status_code == 200
    assert resposta.content[:5] == b"%PDF-"


def test_o_admin_nao_baixa_pdf_de_revogado(
    admin_client_logado, certificado, admin_user
):
    revoke_certificate(certificado, actor=admin_user, motivo="Erro.")

    endereco = reverse(
        "admin_panel:certificate_download_admin", args=[certificado.pk]
    )
    resposta = admin_client_logado.get(endereco)

    assert resposta.status_code == 302
    assert resposta.content[:5] != b"%PDF-"


def test_o_detalhe_mostra_o_historico_da_revogacao(
    admin_client_logado, certificado, admin_user
):
    revoke_certificate(certificado, actor=admin_user, motivo="Erro na matricula.")

    corpo = admin_client_logado.get(
        reverse("admin_panel:certificate_detail", args=[certificado.pk])
    ).content.decode("utf-8")

    assert "Erro na matricula." in corpo
    assert admin_user.full_name in corpo


# ---------------------------------------------------------------------------
# XSS
# ---------------------------------------------------------------------------


SCRIPT = "<script>alert(1)</script>"


def test_nome_com_script_e_escapado_na_pagina_publica(client, certificado):
    certificado.student_name_snapshot = SCRIPT
    certificado.save(update_fields=["student_name_snapshot"])

    corpo = client.get(url_publica(certificado)).content.decode("utf-8")

    assert SCRIPT not in corpo
    assert "&lt;script&gt;" in corpo


def test_motivo_com_script_e_escapado_no_detalhe(
    admin_client_logado, certificado, admin_user
):
    revoke_certificate(certificado, actor=admin_user, motivo=SCRIPT)

    corpo = admin_client_logado.get(
        reverse("admin_panel:certificate_detail", args=[certificado.pk])
    ).content.decode("utf-8")

    assert SCRIPT not in corpo
    assert "&lt;script&gt;" in corpo


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------


def test_certificados_aparece_no_menu_administrativo(admin_client_logado):
    corpo = admin_client_logado.get(
        reverse("admin_panel:dashboard")
    ).content.decode("utf-8")

    # Uma vez na lateral, outra no offcanvas do celular.
    assert corpo.count(">Certificados</a>") == 2
    # E nao aparece mais como promessa de etapa futura.
    assert "Etapa 6" not in corpo
