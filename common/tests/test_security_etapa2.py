"""
Seguranca transversal das telas da Etapa 2.

Estes testes nao verificam funcionalidade: verificam que nenhuma rota nova
escapou das regras de acesso. Sao propositalmente exaustivos, porque o modo
mais provavel de um furo aparecer e alguem acrescentar uma view e esquecer o
mixin de papel.
"""

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


@pytest.fixture
def rotas(student_user, modulo, matricula):
    """Todas as rotas administrativas introduzidas na Etapa 2."""
    aluno, mod, mat = student_user.pk, modulo.pk, matricula.pk
    return {
        "leitura": [
            "/admin-panel/",
            "/admin-panel/alunos/",
            "/admin-panel/alunos/novo/",
            "/admin-panel/alunos/{}/".format(aluno),
            "/admin-panel/alunos/{}/editar/".format(aluno),
            "/admin-panel/alunos/importar/",
            "/admin-panel/alunos/importar/preview/",
            "/admin-panel/modulos/",
            "/admin-panel/modulos/novo/",
            "/admin-panel/modulos/{}/".format(mod),
            "/admin-panel/modulos/{}/editar/".format(mod),
            "/admin-panel/matriculas/",
            "/admin-panel/matriculas/nova/",
        ],
        "acao": [
            "/admin-panel/alunos/{}/bloquear/".format(aluno),
            "/admin-panel/alunos/{}/desbloquear/".format(aluno),
            "/admin-panel/alunos/importar/confirmar/",
            "/admin-panel/alunos/importar/cancelar/",
            "/admin-panel/modulos/{}/ativar/".format(mod),
            "/admin-panel/modulos/{}/desativar/".format(mod),
            "/admin-panel/matriculas/{}/bloquear/".format(mat),
            "/admin-panel/matriculas/{}/liberar/".format(mat),
            "/admin-panel/matriculas/{}/desativar/".format(mat),
            "/admin-panel/matriculas/{}/reativar/".format(mat),
            "/admin-panel/matriculas/{}/concluir/".format(mat),
        ],
    }


# ---------------------------------------------------------------------------
# Segregacao de papel
# ---------------------------------------------------------------------------


def test_admin_alcanca_todas_as_telas_de_leitura(admin_client_logado, rotas):
    """
    Contraprova.

    Sem isto, os testes de bloqueio abaixo passariam mesmo que as rotas nao
    existissem.
    """
    for url in rotas["leitura"]:
        assert admin_client_logado.get(url).status_code in (200, 302), url


def test_aluno_e_barrado_em_todas_as_telas_administrativas(
    student_client_logado, rotas
):
    for url in rotas["leitura"]:
        assert student_client_logado.get(url).status_code == 403, url


def test_aluno_e_barrado_em_todas_as_acoes_administrativas(
    student_client_logado, rotas
):
    for url in rotas["acao"]:
        assert student_client_logado.post(url).status_code == 403, url


def test_anonimo_e_redirecionado_em_todas_as_telas(client, rotas):
    for url in rotas["leitura"]:
        resposta = client.get(url)
        assert resposta.status_code == 302, url
        assert "/login/" in resposta.url, url


def test_anonimo_e_redirecionado_em_todas_as_acoes(client, rotas):
    for url in rotas["acao"]:
        resposta = client.post(url)
        assert resposta.status_code == 302, url
        assert "/login/" in resposta.url, url


def test_admin_nao_entra_na_area_do_aluno(admin_client_logado, modulo):
    assert admin_client_logado.get("/aluno/").status_code == 403
    assert admin_client_logado.get("/aluno/modulos/{}/".format(modulo.pk)).status_code == 403


# ---------------------------------------------------------------------------
# Metodo HTTP
# ---------------------------------------------------------------------------


def test_nenhuma_acao_aceita_get(admin_client_logado, rotas):
    """
    Alteracao de estado por GET seria disparavel por um link ou por uma tag
    de imagem hospedada em outro site.
    """
    for url in rotas["acao"]:
        assert admin_client_logado.get(url).status_code == 405, url


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


def test_post_sem_token_csrf_e_recusado(admin_user, student_user):
    cliente = Client(enforce_csrf_checks=True)
    cliente.force_login(admin_user)

    resposta = cliente.post("/admin-panel/alunos/{}/bloquear/".format(student_user.pk))
    assert resposta.status_code == 403

    student_user.refresh_from_db()
    assert student_user.is_active is True


def test_post_com_token_csrf_e_aceito(admin_user, student_user):
    cliente = Client(enforce_csrf_checks=True)
    cliente.force_login(admin_user)

    pagina = cliente.get("/admin-panel/alunos/")
    token = pagina.context["csrf_token"]

    resposta = cliente.post(
        "/admin-panel/alunos/{}/bloquear/".format(student_user.pk),
        {"csrfmiddlewaretoken": token},
    )
    assert resposta.status_code == 302

    student_user.refresh_from_db()
    assert student_user.is_active is False


# ---------------------------------------------------------------------------
# Bloqueio de aluno com sessao aberta
# ---------------------------------------------------------------------------


def test_bloquear_aluno_derruba_a_sessao_ja_aberta(client, student_user, matricula):
    """
    Garantia central do requisito de bloqueio.

    Marcar is_active=False basta: o ModelBackend do Django devolve None em
    get_user(), entao a sessao existente resolve como anonima na requisicao
    seguinte. Este teste fixa esse comportamento para que ninguem o perca
    trocando o backend de autenticacao sem perceber.
    """
    client.force_login(student_user)
    assert client.get("/aluno/").status_code == 200

    student_user.is_active = False
    student_user.save(update_fields=["is_active"])

    resposta = client.get("/aluno/")
    assert resposta.status_code != 200
    assert resposta.status_code == 302
    assert "/login/" in resposta.url


def test_aluno_bloqueado_tambem_perde_o_modulo(client, student_user, modulo, matricula):
    client.force_login(student_user)
    url = "/aluno/modulos/{}/".format(modulo.pk)
    assert client.get(url).status_code == 200

    student_user.is_active = False
    student_user.save(update_fields=["is_active"])

    assert client.get(url).status_code != 200


# ---------------------------------------------------------------------------
# IDOR
# ---------------------------------------------------------------------------


def test_aluno_nao_alcanca_a_ficha_de_ninguem(
    student_client_logado, student_user, outro_student
):
    for alvo in (student_user, outro_student):
        url = "/admin-panel/alunos/{}/".format(alvo.pk)
        assert student_client_logado.get(url).status_code == 403, url


def test_aluno_nao_ve_modulo_de_outro_aluno(
    client, student_user, outro_student, outro_modulo
):
    from courses.services import create_enrollment

    create_enrollment(student=outro_student, module=outro_modulo)

    client.force_login(student_user)
    resposta = client.get("/aluno/modulos/{}/".format(outro_modulo.pk))
    # 404, e nao 403: um 403 confirmaria que o modulo existe.
    assert resposta.status_code == 404


def test_aluno_nao_consegue_agir_sobre_a_propria_matricula(
    student_client_logado, matricula
):
    """As acoes de matricula sao administrativas; o aluno nao as alcanca."""
    for acao in ("bloquear", "liberar", "desativar", "reativar", "concluir"):
        url = "/admin-panel/matriculas/{}/{}/".format(matricula.pk, acao)
        assert student_client_logado.post(url).status_code == 403, url

    matricula.refresh_from_db()
    assert matricula.access_enabled is True
    assert matricula.status == "ACTIVE"
