"""Telas administrativas de modulos e matriculas."""

import pytest

from courses.models import Enrollment, EnrollmentStatus, Module
from courses.services import create_enrollment
from students.services import create_student

pytestmark = pytest.mark.django_db

URL_MODULOS = "/admin-panel/modulos/"
URL_MODULO_NOVO = "/admin-panel/modulos/novo/"
URL_MATRICULAS = "/admin-panel/matriculas/"
URL_MATRICULA_NOVA = "/admin-panel/matriculas/nova/"


def url_modulo(modulo, sufixo=""):
    return "/admin-panel/modulos/{}/{}".format(modulo.pk, sufixo)


def url_matricula(matricula, acao):
    return "/admin-panel/matriculas/{}/{}/".format(matricula.pk, acao)


# ---------------------------------------------------------------------------
# Modulos
# ---------------------------------------------------------------------------


def test_telas_de_modulo_respondem_para_admin(admin_client_logado, modulo):
    for url in (URL_MODULOS, URL_MODULO_NOVO, url_modulo(modulo), url_modulo(modulo, "editar/")):
        assert admin_client_logado.get(url).status_code == 200, url


def test_criacao_de_modulo_normaliza_o_codigo(admin_client_logado):
    resposta = admin_client_logado.post(
        URL_MODULO_NOVO,
        {"name": "Modulo 5", "code": "  mod5  ", "description": "", "order": 5, "is_active": "on"},
    )
    assert resposta.status_code == 302

    modulo = Module.objects.get(name="Modulo 5")
    assert modulo.code == "MOD5"
    assert modulo.is_active is True
    assert resposta.url == url_modulo(modulo)


def test_criacao_com_codigo_duplicado_e_recusada(admin_client_logado, modulo):
    antes = Module.objects.count()

    resposta = admin_client_logado.post(
        URL_MODULO_NOVO,
        {"name": "Outro Nome", "code": modulo.code.lower(), "order": 3, "is_active": "on"},
    )
    assert resposta.status_code == 200
    assert Module.objects.count() == antes


def test_criacao_sem_marcar_ativo_grava_modulo_inativo(admin_client_logado):
    admin_client_logado.post(
        URL_MODULO_NOVO, {"name": "Rascunho", "code": "MOD77", "order": 0}
    )
    assert Module.objects.get(code="MOD77").is_active is False


def test_edicao_de_modulo(admin_client_logado, modulo):
    resposta = admin_client_logado.post(
        url_modulo(modulo, "editar/"),
        {"name": "Modulo Um", "code": "MOD1", "description": "nova", "order": 7, "is_active": "on"},
    )
    assert resposta.status_code == 302

    modulo.refresh_from_db()
    assert modulo.name == "Modulo Um"
    assert modulo.order == 7
    assert modulo.description == "nova"


def test_ativar_e_desativar_modulo(admin_client_logado, modulo):
    admin_client_logado.post(url_modulo(modulo, "desativar/"))
    modulo.refresh_from_db()
    assert modulo.is_active is False

    admin_client_logado.post(url_modulo(modulo, "ativar/"))
    modulo.refresh_from_db()
    assert modulo.is_active is True


def test_desativar_modulo_preserva_as_matriculas(admin_client_logado, modulo, matricula):
    admin_client_logado.post(url_modulo(modulo, "desativar/"))
    assert Enrollment.objects.filter(pk=matricula.pk).exists()


@pytest.mark.parametrize("acao", ["ativar/", "desativar/"])
def test_acoes_de_modulo_recusam_get(admin_client_logado, modulo, acao):
    assert admin_client_logado.get(url_modulo(modulo, acao)).status_code == 405


def test_filtro_e_busca_de_modulos(admin_client_logado, modulo, modulo_inativo):
    ativos = list(admin_client_logado.get(URL_MODULOS, {"situacao": "ativos"}).context["modulos"])
    assert modulo in ativos
    assert modulo_inativo not in ativos

    inativos = list(
        admin_client_logado.get(URL_MODULOS, {"situacao": "inativos"}).context["modulos"]
    )
    assert modulo_inativo in inativos

    por_codigo = list(admin_client_logado.get(URL_MODULOS, {"q": "MOD9"}).context["modulos"])
    assert por_codigo == [modulo_inativo]


def test_lista_de_modulos_conta_matriculados(admin_client_logado, modulo, matricula):
    resposta = admin_client_logado.get(URL_MODULOS)
    encontrado = next(m for m in resposta.context["modulos"] if m.pk == modulo.pk)
    assert encontrado.total_matriculados == 1


# ---------------------------------------------------------------------------
# Matriculas
# ---------------------------------------------------------------------------


def test_telas_de_matricula_respondem_para_admin(admin_client_logado):
    for url in (URL_MATRICULAS, URL_MATRICULA_NOVA):
        assert admin_client_logado.get(url).status_code == 200, url


def test_criacao_de_matricula(admin_client_logado, student_user, modulo):
    resposta = admin_client_logado.post(
        URL_MATRICULA_NOVA,
        {"student": student_user.pk, "module": modulo.pk, "notes": "turma A"},
    )
    assert resposta.status_code == 302

    criada = Enrollment.objects.get(student=student_user, module=modulo)
    assert criada.status == EnrollmentStatus.ACTIVE
    assert criada.access_enabled is True
    assert criada.notes == "turma A"


def test_matricula_duplicada_nao_cria_segunda_linha(
    admin_client_logado, student_user, modulo, matricula
):
    resposta = admin_client_logado.post(
        URL_MATRICULA_NOVA, {"student": student_user.pk, "module": modulo.pk, "notes": ""}
    )
    assert resposta.status_code == 200
    assert Enrollment.objects.filter(student=student_user, module=modulo).count() == 1


def test_nao_e_possivel_matricular_um_admin(admin_client_logado, admin_user, modulo):
    """
    O queryset do formulario ja exclui administradores; o que importa e que
    nenhuma matricula seja criada, qualquer que seja o caminho da recusa.
    """
    resposta = admin_client_logado.post(
        URL_MATRICULA_NOVA, {"student": admin_user.pk, "module": modulo.pk, "notes": ""}
    )
    assert resposta.status_code == 200
    assert Enrollment.objects.filter(student=admin_user).count() == 0


def test_nao_e_possivel_matricular_em_modulo_inativo(
    admin_client_logado, student_user, modulo_inativo
):
    resposta = admin_client_logado.post(
        URL_MATRICULA_NOVA,
        {"student": student_user.pk, "module": modulo_inativo.pk, "notes": ""},
    )
    assert resposta.status_code == 200
    assert Enrollment.objects.filter(module=modulo_inativo).count() == 0


def test_bloquear_e_liberar_acesso_da_matricula(admin_client_logado, matricula):
    admin_client_logado.post(url_matricula(matricula, "bloquear"))
    matricula.refresh_from_db()
    assert matricula.access_enabled is False
    # Bloqueio operacional nao muda a situacao academica.
    assert matricula.status == EnrollmentStatus.ACTIVE

    admin_client_logado.post(url_matricula(matricula, "liberar"))
    matricula.refresh_from_db()
    assert matricula.access_enabled is True


def test_desativar_matricula_preserva_a_linha(admin_client_logado, matricula):
    admin_client_logado.post(url_matricula(matricula, "desativar"))

    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.INACTIVE
    assert matricula.access_enabled is False
    assert Enrollment.objects.filter(pk=matricula.pk).exists()


def test_reativar_matricula(admin_client_logado, matricula):
    admin_client_logado.post(url_matricula(matricula, "desativar"))
    admin_client_logado.post(url_matricula(matricula, "reativar"))

    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.ACTIVE
    assert matricula.access_enabled is True


def test_concluir_matricula(admin_client_logado, matricula):
    admin_client_logado.post(url_matricula(matricula, "concluir"))
    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.COMPLETED


@pytest.mark.parametrize(
    "acao", ["bloquear", "liberar", "desativar", "reativar", "concluir"]
)
def test_acoes_de_matricula_recusam_get(admin_client_logado, matricula, acao):
    assert admin_client_logado.get(url_matricula(matricula, acao)).status_code == 405


def test_filtros_da_lista_de_matriculas(
    admin_client_logado, student_user, outro_student, modulo, outro_modulo
):
    uma = create_enrollment(student=student_user, module=modulo)
    outra = create_enrollment(student=outro_student, module=outro_modulo)

    por_modulo = list(
        admin_client_logado.get(URL_MATRICULAS, {"modulo": modulo.pk}).context["matriculas"]
    )
    assert por_modulo == [uma]

    por_busca = list(
        admin_client_logado.get(URL_MATRICULAS, {"q": "Maria"}).context["matriculas"]
    )
    assert por_busca == [outra]

    admin_client_logado.post(url_matricula(uma, "bloquear"))
    bloqueadas = list(
        admin_client_logado.get(URL_MATRICULAS, {"acesso": "bloqueado"}).context["matriculas"]
    )
    assert bloqueadas == [uma]

    admin_client_logado.post(url_matricula(outra, "desativar"))
    inativas = list(
        admin_client_logado.get(
            URL_MATRICULAS, {"situacao": EnrollmentStatus.INACTIVE}
        ).context["matriculas"]
    )
    assert inativas == [outra]


def test_paginacao_das_matriculas(admin_client_logado, modulo):
    for indice in range(30):
        aluno = create_student(
            full_name="Aluno {:02d}".format(indice),
            email="mat{:02d}@exemplo.test".format(indice),
        )
        create_enrollment(student=aluno, module=modulo)

    resposta = admin_client_logado.get(URL_MATRICULAS)
    assert len(resposta.context["matriculas"]) == 25
    assert resposta.context["page_obj"].paginator.count == 30


def test_lista_de_matriculas_nao_faz_n_mais_um(
    admin_client_logado, modulo, django_assert_max_num_queries
):
    """O select_related traz aluno e modulo na mesma consulta."""
    for indice in range(10):
        aluno = create_student(
            full_name="Aluno {:02d}".format(indice),
            email="perf{:02d}@exemplo.test".format(indice),
        )
        create_enrollment(student=aluno, module=modulo)

    with django_assert_max_num_queries(10):
        admin_client_logado.get(URL_MATRICULAS)


# ---------------------------------------------------------------------------
# Autorizacao
# ---------------------------------------------------------------------------


def test_aluno_nao_acessa_telas_de_modulo_e_matricula(
    student_client_logado, modulo, matricula
):
    urls = [
        URL_MODULOS,
        URL_MODULO_NOVO,
        url_modulo(modulo),
        url_modulo(modulo, "editar/"),
        URL_MATRICULAS,
        URL_MATRICULA_NOVA,
    ]
    for url in urls:
        assert student_client_logado.get(url).status_code == 403, url


def test_aluno_nao_consegue_executar_acoes(student_client_logado, modulo, matricula):
    assert student_client_logado.post(url_modulo(modulo, "desativar/")).status_code == 403
    modulo.refresh_from_db()
    assert modulo.is_active is True

    assert student_client_logado.post(url_matricula(matricula, "bloquear")).status_code == 403
    matricula.refresh_from_db()
    assert matricula.access_enabled is True


def test_anonimo_e_redirecionado(client, modulo):
    for url in (URL_MODULOS, URL_MODULO_NOVO, url_modulo(modulo), URL_MATRICULAS):
        resposta = client.get(url)
        assert resposta.status_code == 302, url
        assert "/login/" in resposta.url, url
