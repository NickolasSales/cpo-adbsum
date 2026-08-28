"""Telas administrativas de alunos."""

import pytest

from accounts.models import User, UserRole
from students.models import StudentProfile, StudentSource
from students.services import create_student

pytestmark = pytest.mark.django_db

URL_LISTA = "/admin-panel/alunos/"
URL_NOVO = "/admin-panel/alunos/novo/"


def url_detalhe(aluno):
    return "/admin-panel/alunos/{}/".format(aluno.pk)


def url_editar(aluno):
    return "/admin-panel/alunos/{}/editar/".format(aluno.pk)


def url_bloquear(aluno):
    return "/admin-panel/alunos/{}/bloquear/".format(aluno.pk)


def url_desbloquear(aluno):
    return "/admin-panel/alunos/{}/desbloquear/".format(aluno.pk)


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def test_telas_de_leitura_respondem_para_admin(admin_client_logado, student_user):
    for url in (URL_LISTA, URL_NOVO, url_detalhe(student_user), url_editar(student_user)):
        assert admin_client_logado.get(url).status_code == 200, url


def test_detalhe_mostra_as_matriculas_do_aluno(
    admin_client_logado, student_user, matricula
):
    resposta = admin_client_logado.get(url_detalhe(student_user))
    assert list(resposta.context["matriculas"]) == [matricula]
    assert matricula.module.code in resposta.content.decode()


def test_detalhe_de_um_admin_nao_existe(admin_client_logado, admin_user):
    """A tela de alunos so alcanca quem tem papel STUDENT."""
    assert admin_client_logado.get(url_detalhe(admin_user)).status_code == 404


# ---------------------------------------------------------------------------
# Criacao
# ---------------------------------------------------------------------------


def test_criacao_grava_aluno_completo(admin_client_logado, senha_padrao):
    resposta = admin_client_logado.post(
        URL_NOVO,
        {
            "full_name": "Ana Beatriz",
            "email": "Ana.Beatriz@Exemplo.TEST",
            "notes": "Turma da manha",
        },
    )
    assert resposta.status_code == 302

    aluno = User.objects.get(email="ana.beatriz@exemplo.test")
    assert aluno.role == UserRole.STUDENT
    assert aluno.is_active is True
    assert aluno.must_change_password is True
    assert aluno.check_password(senha_padrao) is True

    perfil = StudentProfile.objects.get(user=aluno)
    assert perfil.source == StudentSource.MANUAL
    assert perfil.notes == "Turma da manha"

    assert resposta.url == url_detalhe(aluno)


def test_criacao_com_email_existente_nao_duplica(admin_client_logado, student_user):
    antes = User.objects.count()

    resposta = admin_client_logado.post(
        URL_NOVO, {"full_name": "Outro Joao", "email": student_user.email, "notes": ""}
    )
    assert resposta.status_code == 200
    assert User.objects.count() == antes


def test_criacao_com_email_de_admin_e_recusada(admin_client_logado, admin_user):
    antes = User.objects.count()

    resposta = admin_client_logado.post(
        URL_NOVO, {"full_name": "Tentativa", "email": admin_user.email, "notes": ""}
    )
    assert resposta.status_code == 200
    assert User.objects.count() == antes

    admin_user.refresh_from_db()
    assert admin_user.role == UserRole.ADMIN


def test_criacao_sem_senha_padrao_configurada_e_recusada(admin_client_logado, settings):
    settings.DEFAULT_STUDENT_PASSWORD = ""
    antes = User.objects.count()

    resposta = admin_client_logado.post(
        URL_NOVO, {"full_name": "Sem Senha", "email": "sem.senha@exemplo.test"}
    )
    assert resposta.status_code == 200
    assert User.objects.count() == antes


# ---------------------------------------------------------------------------
# Mass assignment
# ---------------------------------------------------------------------------


def test_criacao_ignora_campos_de_privilegio(admin_client_logado):
    """
    Um POST forjado nao pode promover ninguem.

    O formulario declara apenas full_name, email e notes, e o servico nao
    aceita outros campos. Mesmo que o navegador envie role, is_staff e
    is_superuser, eles precisam ser descartados.
    """
    admin_client_logado.post(
        URL_NOVO,
        {
            "full_name": "Tentativa Escalada",
            "email": "escalada@exemplo.test",
            "notes": "",
            "role": UserRole.ADMIN,
            "is_staff": "on",
            "is_superuser": "on",
            "is_active": "on",
            "must_change_password": "",
        },
    )

    aluno = User.objects.get(email="escalada@exemplo.test")
    assert aluno.role == UserRole.STUDENT
    assert aluno.is_staff is False
    assert aluno.is_superuser is False
    assert aluno.must_change_password is True


def test_edicao_ignora_campos_de_privilegio(admin_client_logado, student_user):
    admin_client_logado.post(
        url_editar(student_user),
        {
            "full_name": student_user.full_name,
            "email": student_user.email,
            "notes": "",
            "role": UserRole.ADMIN,
            "is_staff": "on",
            "is_superuser": "on",
        },
    )

    student_user.refresh_from_db()
    assert student_user.role == UserRole.STUDENT
    assert student_user.is_staff is False
    assert student_user.is_superuser is False


# ---------------------------------------------------------------------------
# Edicao
# ---------------------------------------------------------------------------


def test_edicao_altera_nome_email_e_observacoes(admin_client_logado, student_user):
    resposta = admin_client_logado.post(
        url_editar(student_user),
        {
            "full_name": "Joao da Silva Junior",
            "email": "joao.novo@exemplo.test",
            "notes": "Mudou de turma",
        },
    )
    assert resposta.status_code == 302

    student_user.refresh_from_db()
    assert student_user.full_name == "Joao da Silva Junior"
    assert student_user.email == "joao.novo@exemplo.test"
    assert student_user.student_profile.notes == "Mudou de turma"


def test_edicao_com_email_de_outro_aluno_e_recusada(
    admin_client_logado, student_user, outro_student
):
    email_original = student_user.email

    resposta = admin_client_logado.post(
        url_editar(student_user),
        {"full_name": student_user.full_name, "email": outro_student.email, "notes": ""},
    )
    assert resposta.status_code == 200

    student_user.refresh_from_db()
    assert student_user.email == email_original


# ---------------------------------------------------------------------------
# Bloqueio e desbloqueio
# ---------------------------------------------------------------------------


def test_bloqueio_e_desbloqueio(admin_client_logado, student_user):
    admin_client_logado.post(url_bloquear(student_user))
    student_user.refresh_from_db()
    assert student_user.is_active is False

    admin_client_logado.post(url_desbloquear(student_user))
    student_user.refresh_from_db()
    assert student_user.is_active is True


def test_desbloqueio_nao_altera_a_senha(admin_client_logado, student_user):
    hash_antes = student_user.password
    admin_client_logado.post(url_bloquear(student_user))
    admin_client_logado.post(url_desbloquear(student_user))

    student_user.refresh_from_db()
    assert student_user.password == hash_antes


@pytest.mark.parametrize("acao", [url_bloquear, url_desbloquear])
def test_acoes_recusam_get(admin_client_logado, student_user, acao):
    """
    Alterar estado por GET permitiria disparar a acao com um link ou uma tag
    de imagem hospedada em outro site.
    """
    assert admin_client_logado.get(acao(student_user)).status_code == 405


def test_bloqueio_volta_para_a_url_de_origem(admin_client_logado, student_user):
    resposta = admin_client_logado.post(
        url_bloquear(student_user), {"proximo": "/admin-panel/alunos/?situacao=ativos"}
    )
    assert resposta.status_code == 302
    assert resposta.url == "/admin-panel/alunos/?situacao=ativos"


# ---------------------------------------------------------------------------
# Busca, filtro e paginacao
# ---------------------------------------------------------------------------


def test_busca_por_nome(admin_client_logado, student_user, outro_student):
    resposta = admin_client_logado.get(URL_LISTA, {"q": "Maria"})
    encontrados = list(resposta.context["alunos"])
    assert outro_student in encontrados
    assert student_user not in encontrados


def test_busca_por_email(admin_client_logado, student_user, outro_student):
    resposta = admin_client_logado.get(URL_LISTA, {"q": "joao.aluno@"})
    encontrados = list(resposta.context["alunos"])
    assert student_user in encontrados
    assert outro_student not in encontrados


def test_filtro_por_situacao(admin_client_logado, student_user, outro_student):
    outro_student.is_active = False
    outro_student.save(update_fields=["is_active"])

    ativos = list(admin_client_logado.get(URL_LISTA, {"situacao": "ativos"}).context["alunos"])
    assert student_user in ativos
    assert outro_student not in ativos

    bloqueados = list(
        admin_client_logado.get(URL_LISTA, {"situacao": "bloqueados"}).context["alunos"]
    )
    assert outro_student in bloqueados
    assert student_user not in bloqueados


def test_lista_conta_os_modulos_do_aluno(admin_client_logado, student_user, matricula):
    resposta = admin_client_logado.get(URL_LISTA)
    aluno = next(a for a in resposta.context["alunos"] if a.pk == student_user.pk)
    assert aluno.total_modulos == 1


def test_paginacao(admin_client_logado):
    for indice in range(30):
        create_student(
            full_name="Aluno {:02d}".format(indice),
            email="aluno{:02d}@exemplo.test".format(indice),
        )

    resposta = admin_client_logado.get(URL_LISTA)
    assert len(resposta.context["alunos"]) == 25
    assert resposta.context["page_obj"].paginator.num_pages == 2
    assert resposta.context["page_obj"].paginator.count == 30

    segunda = admin_client_logado.get(URL_LISTA, {"page": 2})
    assert len(segunda.context["alunos"]) == 5


def test_lista_nao_executa_uma_consulta_por_aluno(
    admin_client_logado, django_assert_max_num_queries
):
    """
    Protege contra N+1.

    A contagem de modulos vem por annotate e a origem por select_related, de
    modo que dobrar o numero de alunos nao pode dobrar o numero de consultas.
    """
    for indice in range(10):
        create_student(
            full_name="Aluno {:02d}".format(indice),
            email="perf{:02d}@exemplo.test".format(indice),
        )

    with django_assert_max_num_queries(10):
        admin_client_logado.get(URL_LISTA)


# ---------------------------------------------------------------------------
# Autorizacao
# ---------------------------------------------------------------------------


def test_aluno_nao_acessa_nenhuma_tela_de_alunos(student_client_logado, student_user):
    for url in (URL_LISTA, URL_NOVO, url_detalhe(student_user), url_editar(student_user)):
        assert student_client_logado.get(url).status_code == 403, url


def test_aluno_nao_consegue_bloquear_ninguem(
    student_client_logado, student_user, outro_student
):
    resposta = student_client_logado.post(url_bloquear(outro_student))
    assert resposta.status_code == 403

    outro_student.refresh_from_db()
    assert outro_student.is_active is True


def test_anonimo_e_redirecionado_para_o_login(client, student_user):
    for url in (URL_LISTA, URL_NOVO, url_detalhe(student_user)):
        resposta = client.get(url)
        assert resposta.status_code == 302, url
        assert "/login/" in resposta.url, url


def test_senha_padrao_nunca_aparece_nas_telas(
    admin_client_logado, student_user, senha_padrao
):
    for url in (URL_LISTA, URL_NOVO, url_detalhe(student_user), url_editar(student_user)):
        conteudo = admin_client_logado.get(url).content.decode()
        assert senha_padrao not in conteudo, url
