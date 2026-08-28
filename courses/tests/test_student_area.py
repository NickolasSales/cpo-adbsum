"""Testes do painel do aluno e do controle de IDOR no detalhe do modulo."""

import pytest

from courses import services
from courses.models import Enrollment, EnrollmentStatus, Module

pytestmark = pytest.mark.django_db

URL_LOGIN = "/login/"
URL_PAINEL_ALUNO = "/aluno/"

TEXTO_ESTADO_VAZIO = "Voce ainda nao possui modulos disponiveis"

# Teto de consultas do painel do aluno. Ver o teste de N+1 no fim do arquivo.
TETO_CONSULTAS_PAINEL = 6


def url_modulo(pk):
    return "/aluno/modulos/{}/".format(pk)


# ---------------------------------------------------------------------------
# Painel do aluno: o que aparece
# ---------------------------------------------------------------------------


def test_painel_mostra_modulo_com_matricula_liberada(
    student_client_logado, modulo, matricula
):
    resposta = student_client_logado.get(URL_PAINEL_ALUNO)

    assert resposta.status_code == 200
    assert list(resposta.context["modulos"]) == [modulo]

    conteudo = resposta.content.decode()
    assert "Modulo 1" in conteudo
    assert url_modulo(modulo.pk) in conteudo


def test_painel_sem_matricula_mostra_estado_vazio(student_client_logado, modulo):
    resposta = student_client_logado.get(URL_PAINEL_ALUNO)

    assert resposta.status_code == 200
    assert list(resposta.context["modulos"]) == []

    conteudo = resposta.content.decode()
    assert TEXTO_ESTADO_VAZIO in conteudo
    assert "Modulo 1" not in conteudo


def test_matricula_inativa_esconde_o_modulo_do_painel(
    student_client_logado, modulo, matricula
):
    # access_enabled continua True de proposito: o teste precisa provar que a
    # situacao academica sozinha ja tira o modulo da lista.
    matricula.status = EnrollmentStatus.INACTIVE
    matricula.save(update_fields=["status"])

    resposta = student_client_logado.get(URL_PAINEL_ALUNO)

    assert list(resposta.context["modulos"]) == []
    assert TEXTO_ESTADO_VAZIO in resposta.content.decode()


def test_matricula_concluida_esconde_o_modulo_do_painel(
    student_client_logado, modulo, matricula
):
    services.complete_enrollment(matricula)

    resposta = student_client_logado.get(URL_PAINEL_ALUNO)

    assert list(resposta.context["modulos"]) == []
    assert TEXTO_ESTADO_VAZIO in resposta.content.decode()


def test_acesso_bloqueado_esconde_o_modulo_do_painel(
    student_client_logado, modulo, matricula
):
    services.block_enrollment_access(matricula)

    matricula.refresh_from_db()
    # O bloqueio e operacional: a matricula segue ACTIVE, so o acesso cai.
    assert matricula.status == EnrollmentStatus.ACTIVE

    resposta = student_client_logado.get(URL_PAINEL_ALUNO)

    assert list(resposta.context["modulos"]) == []
    assert TEXTO_ESTADO_VAZIO in resposta.content.decode()


def test_modulo_inativo_nao_aparece_no_painel(student_client_logado, modulo, matricula):
    services.disable_module(modulo)

    resposta = student_client_logado.get(URL_PAINEL_ALUNO)

    matricula.refresh_from_db()
    # Desativar o modulo nao mexe na matricula; apenas para de dar acesso.
    assert matricula.status == EnrollmentStatus.ACTIVE
    assert matricula.access_enabled is True

    assert list(resposta.context["modulos"]) == []
    assert TEXTO_ESTADO_VAZIO in resposta.content.decode()


def test_modulo_de_outro_aluno_nao_aparece_no_painel(
    student_client_logado, modulo, matricula, outro_student, outro_modulo
):
    services.create_enrollment(student=outro_student, module=outro_modulo)

    resposta = student_client_logado.get(URL_PAINEL_ALUNO)

    assert list(resposta.context["modulos"]) == [modulo]
    assert "Modulo 2" not in resposta.content.decode()


# ---------------------------------------------------------------------------
# Detalhe do modulo: acesso legitimo
# ---------------------------------------------------------------------------


def test_detalhe_do_modulo_com_matricula_liberada(
    student_client_logado, modulo, matricula
):
    resposta = student_client_logado.get(url_modulo(modulo.pk))

    assert resposta.status_code == 200
    assert resposta.context["modulo"] == modulo
    assert resposta.context["matricula"] == matricula

    conteudo = resposta.content.decode()
    assert "Modulo 1" in conteudo
    assert "MOD1" in conteudo
    assert "Primeiro modulo" in conteudo


# ---------------------------------------------------------------------------
# Detalhe do modulo: IDOR
# ---------------------------------------------------------------------------


def test_modulo_sem_matricula_devolve_404(student_client_logado, outro_modulo):
    """
    404 e nao 403: um 403 confirmaria que o modulo daquele id existe, o que ja
    e informacao util para quem esta sondando ids na URL.
    """
    resposta = student_client_logado.get(url_modulo(outro_modulo.pk))

    assert resposta.status_code == 404


def test_modulo_de_outro_aluno_devolve_404(
    student_client_logado, outro_student, outro_modulo
):
    matricula_alheia = services.create_enrollment(
        student=outro_student, module=outro_modulo
    )

    resposta = student_client_logado.get(url_modulo(outro_modulo.pk))

    assert resposta.status_code == 404
    # A matricula do colega segue intacta: o 404 e do solicitante, nao um
    # efeito colateral no banco.
    matricula_alheia.refresh_from_db()
    assert matricula_alheia.status == EnrollmentStatus.ACTIVE
    assert matricula_alheia.access_enabled is True


def test_modulo_com_acesso_bloqueado_devolve_404(
    student_client_logado, modulo, matricula
):
    services.block_enrollment_access(matricula)

    resposta = student_client_logado.get(url_modulo(modulo.pk))

    assert resposta.status_code == 404


def test_modulo_com_matricula_inativa_devolve_404(
    student_client_logado, modulo, matricula
):
    matricula.status = EnrollmentStatus.INACTIVE
    matricula.save(update_fields=["status"])

    resposta = student_client_logado.get(url_modulo(modulo.pk))

    assert resposta.status_code == 404


def test_modulo_inativo_devolve_404(student_client_logado, modulo, matricula):
    services.disable_module(modulo)

    resposta = student_client_logado.get(url_modulo(modulo.pk))

    assert resposta.status_code == 404


def test_modulo_inexistente_devolve_404(student_client_logado):
    resposta = student_client_logado.get(url_modulo(999999))

    assert resposta.status_code == 404


# ---------------------------------------------------------------------------
# Autorizacao por papel
# ---------------------------------------------------------------------------


def test_admin_nao_acessa_o_painel_do_aluno(admin_client_logado):
    resposta = admin_client_logado.get(URL_PAINEL_ALUNO)

    assert resposta.status_code == 403


def test_admin_nao_acessa_o_detalhe_de_modulo_do_aluno(
    admin_client_logado, modulo, matricula
):
    """
    Papel errado da 403 mesmo com o modulo liberado para algum aluno: a
    barreira de papel roda antes de qualquer consulta de matricula.
    """
    resposta = admin_client_logado.get(url_modulo(modulo.pk))

    assert resposta.status_code == 403


def test_anonimo_e_redirecionado_do_painel_para_o_login(client):
    resposta = client.get(URL_PAINEL_ALUNO)

    assert resposta.status_code == 302
    assert URL_LOGIN in resposta.url


def test_anonimo_e_redirecionado_do_detalhe_para_o_login(client, modulo, matricula):
    resposta = client.get(url_modulo(modulo.pk))

    assert resposta.status_code == 302
    assert URL_LOGIN in resposta.url


# ---------------------------------------------------------------------------
# Desempenho
# ---------------------------------------------------------------------------


def test_painel_nao_consulta_uma_vez_por_modulo(
    student_client_logado, student_user, django_assert_num_queries
):
    """
    Teto de consultas do painel.

    O objetivo e travar o N+1: com cinco modulos matriculados o painel precisa
    continuar resolvendo a lista em uma unica consulta. Se alguem trocar o
    queryset por um laco que busca modulo a modulo, o numero estoura o teto e
    o teste quebra.
    """
    for indice in range(1, 6):
        modulo_perf = Module.objects.create(
            name="Modulo {}".format(indice),
            code="PERF{}".format(indice),
            order=indice,
        )
        Enrollment.objects.create(student=student_user, module=modulo_perf)

    with django_assert_num_queries(TETO_CONSULTAS_PAINEL):
        resposta = student_client_logado.get(URL_PAINEL_ALUNO)

    assert resposta.status_code == 200
    assert len(resposta.context["modulos"]) == 5
