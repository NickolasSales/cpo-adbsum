"""Fixtures compartilhadas pela suite."""

import pytest

from accounts.models import User, UserRole

# Senhas usadas nos testes. Precisam passar pelos validadores do Django:
# ao menos 8 caracteres, nao puramente numericas, nao comuns e sem
# semelhanca com o e-mail ou o nome do usuario.
SENHA_VALIDA = "Prova#Segura2026"
SENHA_NOVA = "Trocada#Segura2027"

# Senha inicial dos alunos durante os testes. Nunca deve aparecer em
# template, log ou trilha de auditoria; varios testes verificam exatamente
# isso, entao o valor precisa ser distinto e reconhecivel.
SENHA_PADRAO_ALUNO = "Inicial#Aluno2026"


@pytest.fixture
def senha():
    return SENHA_VALIDA


@pytest.fixture
def senha_nova():
    return SENHA_NOVA


@pytest.fixture
def senha_padrao():
    return SENHA_PADRAO_ALUNO


@pytest.fixture(autouse=True)
def default_student_password(settings):
    """
    Deixa a senha inicial padrao configurada em toda a suite.

    E o estado normal de operacao do sistema. Os testes que precisam do
    cenario oposto sobrescrevem settings.DEFAULT_STUDENT_PASSWORD com string
    vazia; a fixture settings do pytest-django restaura o valor no fim de
    cada teste.
    """
    settings.DEFAULT_STUDENT_PASSWORD = SENHA_PADRAO_ALUNO
    return SENHA_PADRAO_ALUNO


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        email="coordenacao@exemplo.test",
        full_name="Carla Coordenadora",
        password=SENHA_VALIDA,
        role=UserRole.ADMIN,
    )


def _aluno_pronto_para_login(full_name, email):
    """
    Aluno com perfil e senha conhecida, pronto para autenticar.

    Passa pelo servico de criacao, para que o StudentProfile exista como na
    aplicacao real, e depois troca a senha e baixa a flag de troca
    obrigatoria. Sem isso os testes de autenticacao da Etapa 1, que usam
    SENHA_VALIDA e esperam entrar direto no painel, deixariam de valer.
    """
    from students.services import create_student

    user = create_student(full_name=full_name, email=email)
    user.set_password(SENHA_VALIDA)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])
    return user


@pytest.fixture
def student_user(db):
    return _aluno_pronto_para_login("Joao da Silva", "joao.aluno@exemplo.test")


@pytest.fixture
def outro_student(db):
    return _aluno_pronto_para_login("Maria Oliveira", "maria.aluna@exemplo.test")


@pytest.fixture
def student_com_troca_pendente(db):
    return User.objects.create_user(
        email="maria.pendente@exemplo.test",
        full_name="Maria Souza",
        password=SENHA_VALIDA,
        role=UserRole.STUDENT,
        must_change_password=True,
    )


# ---------------------------------------------------------------------------
# Clientes ja autenticados
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client_logado(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def student_client_logado(client, student_user):
    client.force_login(student_user)
    return client


# ---------------------------------------------------------------------------
# Modulos e matriculas
# ---------------------------------------------------------------------------


@pytest.fixture
def modulo(db):
    from courses.models import Module

    return Module.objects.create(
        name="Modulo 1", code="MOD1", description="Primeiro modulo", order=1
    )


@pytest.fixture
def outro_modulo(db):
    from courses.models import Module

    return Module.objects.create(name="Modulo 2", code="MOD2", order=2)


@pytest.fixture
def modulo_inativo(db):
    from courses.models import Module

    return Module.objects.create(
        name="Modulo Arquivado", code="MOD9", order=9, is_active=False
    )


@pytest.fixture
def matricula(db, student_user, modulo):
    from courses.services import create_enrollment

    return create_enrollment(student=student_user, module=modulo)
