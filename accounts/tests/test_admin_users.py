"""
Contas administrativas: criacao, edicao, bloqueio e senha.

A maior parte destes testes e sobre privilegio. Um administrador criado com
`is_superuser=True` por descuido ignora todo o sistema de permissoes do
Django — inclusive as verificacoes que ainda nao foram escritas —, e o
formulario que aceitasse esse campo seria escalonamento de privilegio por
POST.

As duas protecoes de disponibilidade (nao bloquear a si mesmo, nao bloquear o
ultimo ativo) sao exercitadas contra o SERVICO e contra a VIEW: esconder o
botao no template nao impede um POST.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts import services
from accounts.models import UserRole
from audit.models import AuditEvent, AuditLog
from common.exceptions import DomainError

User = get_user_model()
pytestmark = pytest.mark.django_db

SENHA_BOA = "Reforma1517!Genebra"


def url(nome, *args):
    return reverse("admin_panel:{}".format(nome), args=args)


@pytest.fixture
def outro_admin(db):
    return services.create_admin_user(
        full_name="Paulo Supervisor",
        email="paulo.supervisor@exemplo.test",
        password=SENHA_BOA,
    )


# ---------------------------------------------------------------------------
# Criacao
# ---------------------------------------------------------------------------


def test_cria_administrador_com_papel_e_sem_privilegio_tecnico(admin_user):
    novo = services.create_admin_user(
        full_name="Ana Secretaria",
        email="ana@exemplo.test",
        password=SENHA_BOA,
        actor=admin_user,
    )

    assert novo.role == UserRole.ADMIN
    assert novo.is_active is True
    assert novo.is_staff is False
    assert novo.is_superuser is False
    assert novo.must_change_password is False


def test_a_senha_e_gravada_com_hash(admin_user):
    novo = services.create_admin_user(
        full_name="Ana Secretaria",
        email="ana@exemplo.test",
        password=SENHA_BOA,
        actor=admin_user,
    )

    gravado = User.objects.get(pk=novo.pk)
    assert gravado.check_password(SENHA_BOA) is True
    assert SENHA_BOA not in gravado.password
    assert gravado.password.startswith("pbkdf2_")


def test_email_duplicado_e_recusado(admin_user):
    with pytest.raises(DomainError):
        services.create_admin_user(
            full_name="Copia",
            email=admin_user.email,
            password=SENHA_BOA,
            actor=admin_user,
        )


def test_email_duplicado_ignorando_caixa(admin_user):
    with pytest.raises(DomainError):
        services.create_admin_user(
            full_name="Copia",
            email=admin_user.email.upper(),
            password=SENHA_BOA,
            actor=admin_user,
        )


def test_senha_fraca_e_recusada(admin_user):
    with pytest.raises(DomainError):
        services.create_admin_user(
            full_name="Ana",
            email="ana@exemplo.test",
            password="1234",
            actor=admin_user,
        )
    assert not User.objects.filter(email="ana@exemplo.test").exists()


def test_nome_vazio_e_recusado(admin_user):
    with pytest.raises(DomainError):
        services.create_admin_user(
            full_name="   ", email="ana@exemplo.test", password=SENHA_BOA
        )


def test_a_criacao_e_auditada_sem_a_senha(admin_user):
    novo = services.create_admin_user(
        full_name="Ana",
        email="ana@exemplo.test",
        password=SENHA_BOA,
        actor=admin_user,
    )

    evento = AuditLog.objects.get(event=AuditEvent.ADMIN_USER_CREATED)
    assert evento.actor_id == admin_user.pk
    assert evento.entity_id == str(novo.pk)
    assert SENHA_BOA not in str(evento.metadata)


# ---------------------------------------------------------------------------
# Mass assignment
# ---------------------------------------------------------------------------


def test_post_com_is_superuser_nao_concede_privilegio(admin_client_logado):
    """
    O campo nem existe no formulario.

    Um POST forjado chega a uma view que nunca le esse nome e a um servico que
    grava o valor literal False.
    """
    admin_client_logado.post(
        url("admin_user_create"),
        {
            "full_name": "Ana Invasora",
            "email": "ana@exemplo.test",
            "password1": SENHA_BOA,
            "password2": SENHA_BOA,
            "is_superuser": "true",
            "is_staff": "true",
            "role": "SUPERADMIN",
            "must_change_password": "true",
        },
    )

    nova = User.objects.get(email="ana@exemplo.test")
    assert nova.is_superuser is False
    assert nova.is_staff is False
    assert nova.role == UserRole.ADMIN
    assert nova.must_change_password is False


def test_editar_nao_aceita_campos_privilegiados(admin_client_logado, outro_admin):
    admin_client_logado.post(
        url("admin_user_update", outro_admin.pk),
        {
            "full_name": "Paulo Editado",
            "email": outro_admin.email,
            "is_superuser": "true",
            "is_staff": "true",
            "is_active": "false",
        },
    )

    outro_admin.refresh_from_db()
    assert outro_admin.full_name == "Paulo Editado"
    assert outro_admin.is_superuser is False
    assert outro_admin.is_staff is False
    assert outro_admin.is_active is True


# ---------------------------------------------------------------------------
# Edicao
# ---------------------------------------------------------------------------


def test_editar_altera_nome_e_email(admin_user, outro_admin):
    services.update_admin_user(
        outro_admin,
        full_name="Paulo Corrigido",
        email="paulo.novo@exemplo.test",
        actor=admin_user,
    )

    outro_admin.refresh_from_db()
    assert outro_admin.full_name == "Paulo Corrigido"
    assert outro_admin.email == "paulo.novo@exemplo.test"


def test_a_edicao_audita_so_os_nomes_dos_campos(admin_user, outro_admin):
    services.update_admin_user(
        outro_admin,
        full_name="Paulo Corrigido",
        email=outro_admin.email,
        actor=admin_user,
    )

    evento = AuditLog.objects.get(event=AuditEvent.ADMIN_USER_UPDATED)
    assert evento.metadata == {"changed_fields": ["full_name"]}
    # O valor em si nao vai para a trilha.
    assert "Paulo Corrigido" not in str(evento.metadata)


def test_edicao_sem_mudanca_nao_gera_evento(admin_user, outro_admin):
    services.update_admin_user(
        outro_admin,
        full_name=outro_admin.full_name,
        email=outro_admin.email,
        actor=admin_user,
    )

    assert AuditLog.objects.filter(event=AuditEvent.ADMIN_USER_UPDATED).count() == 0


def test_nao_edita_aluno_por_esta_rota(admin_user, student_user):
    with pytest.raises(DomainError):
        services.update_admin_user(
            student_user, full_name="X", email="x@exemplo.test", actor=admin_user
        )


# ---------------------------------------------------------------------------
# Bloqueio
# ---------------------------------------------------------------------------


def test_bloquear_desativa_a_conta(admin_user, outro_admin):
    services.block_admin_user(outro_admin, actor=admin_user)

    outro_admin.refresh_from_db()
    assert outro_admin.is_active is False
    assert AuditLog.objects.filter(event=AuditEvent.ADMIN_USER_BLOCKED).count() == 1


def test_bloquear_derruba_a_sessao(client, admin_user, outro_admin):
    """
    O ModelBackend.get_user do Django devolve None para usuario inativo, entao
    a sessao aberta passa a resolver como anonima no request seguinte.
    """
    client.force_login(outro_admin)
    assert client.get(url("admin_user_list")).status_code == 200

    services.block_admin_user(outro_admin, actor=admin_user)

    resposta = client.get(url("admin_user_list"))
    assert resposta.status_code == 302
    assert "/login/" in resposta["Location"]


def test_nao_bloqueia_a_si_mesmo_no_servico(admin_user):
    with pytest.raises(DomainError) as erro:
        services.block_admin_user(admin_user, actor=admin_user)

    assert "propria conta" in str(erro.value)
    admin_user.refresh_from_db()
    assert admin_user.is_active is True


def test_nao_bloqueia_a_si_mesmo_pela_view(admin_client_logado, admin_user):
    """A recusa vive no servico: esconder o botao nao impede o POST."""
    resposta = admin_client_logado.post(url("admin_user_block", admin_user.pk))

    admin_user.refresh_from_db()
    assert admin_user.is_active is True
    assert resposta.status_code == 302


def test_o_ultimo_administrador_ativo_nao_pode_ser_bloqueado(admin_user, outro_admin):
    """
    Sem esta protecao o sistema ficaria sem ninguem para destrancar qualquer
    um — e a unica saida seria linha de comando no servidor.
    """
    services.block_admin_user(outro_admin, actor=admin_user)

    # Agora so admin_user esta ativo. Um segundo administrador tenta bloquea-lo.
    terceiro = services.create_admin_user(
        full_name="Terceiro", email="terceiro@exemplo.test", password=SENHA_BOA
    )
    services.block_admin_user(terceiro, actor=admin_user)

    assert services.administradores_ativos().count() == 1
    with pytest.raises(DomainError) as erro:
        services.block_admin_user(admin_user, actor=terceiro)

    assert "unica conta administrativa ativa" in str(erro.value)
    admin_user.refresh_from_db()
    assert admin_user.is_active is True


def test_desbloquear_devolve_o_acesso(admin_user, outro_admin):
    services.block_admin_user(outro_admin, actor=admin_user)
    services.unblock_admin_user(outro_admin, actor=admin_user)

    outro_admin.refresh_from_db()
    assert outro_admin.is_active is True
    assert AuditLog.objects.filter(event=AuditEvent.ADMIN_USER_UNBLOCKED).count() == 1


def test_bloquear_duas_vezes_nao_duplica_evento(admin_user, outro_admin):
    services.block_admin_user(outro_admin, actor=admin_user)
    services.block_admin_user(outro_admin, actor=admin_user)

    assert AuditLog.objects.filter(event=AuditEvent.ADMIN_USER_BLOCKED).count() == 1


def test_nao_existe_exclusao_de_administrador():
    """
    Apagar a linha quebraria a leitura da trilha, onde o autor de cada acao
    aponta para este usuario. A conta e ativa ou bloqueada.
    """
    from django.urls import NoReverseMatch

    with pytest.raises(NoReverseMatch):
        reverse("admin_panel:admin_user_delete", args=[1])


# ---------------------------------------------------------------------------
# Senha
# ---------------------------------------------------------------------------


def test_reset_troca_a_senha(admin_user, outro_admin):
    nova = "Wittenberg1483!Lutero"
    services.reset_admin_password(outro_admin, new_password=nova, actor=admin_user)

    outro_admin.refresh_from_db()
    assert outro_admin.check_password(nova) is True
    assert outro_admin.check_password(SENHA_BOA) is False


def test_reset_derruba_as_sessoes_antigas(client, admin_user, outro_admin):
    client.force_login(outro_admin)
    assert client.get(url("admin_user_list")).status_code == 200

    services.reset_admin_password(
        outro_admin, new_password="Wittenberg1483!Lutero", actor=admin_user
    )

    resposta = client.get(url("admin_user_list"))
    assert resposta.status_code == 302
    assert "/login/" in resposta["Location"]


def test_a_senha_nova_funciona_no_login(client, admin_user, outro_admin):
    nova = "Wittenberg1483!Lutero"
    services.reset_admin_password(outro_admin, new_password=nova, actor=admin_user)

    assert client.login(username=outro_admin.email, password=nova) is True


def test_o_reset_nao_leva_senha_nem_hash_para_a_trilha(admin_user, outro_admin):
    nova = "Wittenberg1483!Lutero"
    services.reset_admin_password(outro_admin, new_password=nova, actor=admin_user)

    evento = AuditLog.objects.get(event=AuditEvent.ADMIN_PASSWORD_RESET)
    trilha = str(evento.metadata)

    assert nova not in trilha
    assert "pbkdf2" not in trilha
    assert str(len(nova)) not in trilha
    # A chave e "redefinida", e nao "password_reset": o sanitizador da
    # auditoria descarta por substring qualquer chave contendo "password", e
    # o evento perderia o proprio conteudo.
    assert evento.metadata == {"redefinida": True}


def test_reset_de_senha_fraca_e_recusado(admin_user, outro_admin):
    with pytest.raises(DomainError):
        services.reset_admin_password(
            outro_admin, new_password="1234", actor=admin_user
        )

    outro_admin.refresh_from_db()
    assert outro_admin.check_password(SENHA_BOA) is True


def test_nao_reseta_senha_de_aluno_por_esta_rota(admin_user, student_user):
    with pytest.raises(DomainError):
        services.reset_admin_password(
            student_user, new_password=SENHA_BOA, actor=admin_user
        )


# ---------------------------------------------------------------------------
# Acesso e metodo
# ---------------------------------------------------------------------------


ROTAS_GET = [
    ("admin_user_list", ()),
    ("admin_user_create", ()),
]


@pytest.mark.parametrize("nome,args", ROTAS_GET)
def test_aluno_recebe_403(student_client_logado, nome, args):
    assert student_client_logado.get(url(nome, *args)).status_code == 403


@pytest.mark.parametrize("nome,args", ROTAS_GET)
def test_anonimo_vai_para_o_login(client, nome, args):
    resposta = client.get(url(nome, *args))
    assert resposta.status_code == 302
    assert "/login/" in resposta["Location"]


def test_aluno_nao_bloqueia_administrador(student_client_logado, outro_admin):
    resposta = student_client_logado.post(url("admin_user_block", outro_admin.pk))

    assert resposta.status_code == 403
    outro_admin.refresh_from_db()
    assert outro_admin.is_active is True


def test_bloquear_por_get_responde_405(admin_client_logado, outro_admin):
    assert admin_client_logado.get(url("admin_user_block", outro_admin.pk)).status_code == 405

    outro_admin.refresh_from_db()
    assert outro_admin.is_active is True


def test_desbloquear_por_get_responde_405(admin_client_logado, outro_admin):
    assert (
        admin_client_logado.get(url("admin_user_unblock", outro_admin.pk)).status_code
        == 405
    )


def test_id_de_aluno_numa_rota_de_administrador_responde_404(
    admin_client_logado, student_user
):
    """
    O filtro por papel e o que impede a confusao entre as duas familias de
    conta. Um aluno nunca deve ser alcancavel por /administradores/<id>/.
    """
    assert admin_client_logado.get(url("admin_user_detail", student_user.pk)).status_code == 404
    assert (
        admin_client_logado.post(url("admin_user_block", student_user.pk)).status_code
        == 404
    )


def test_id_inexistente_responde_404(admin_client_logado):
    assert admin_client_logado.get(url("admin_user_detail", 999999)).status_code == 404


# ---------------------------------------------------------------------------
# XSS
# ---------------------------------------------------------------------------


def test_nome_com_script_e_escapado(admin_client_logado, admin_user):
    script = "<script>alert(1)</script>"
    services.create_admin_user(
        full_name=script, email="xss@exemplo.test", password=SENHA_BOA
    )

    corpo = admin_client_logado.get(url("admin_user_list")).content.decode("utf-8")

    assert script not in corpo
    assert "&lt;script&gt;" in corpo


# ---------------------------------------------------------------------------
# Superuser tecnico
# ---------------------------------------------------------------------------


def test_superuser_aparece_com_etiqueta_de_tecnico(admin_client_logado, db):
    tecnico = User.objects.create_superuser(
        email="tecnico@exemplo.test", full_name="Conta Tecnica", password=SENHA_BOA
    )

    corpo = admin_client_logado.get(url("admin_user_list")).content.decode("utf-8")
    assert "Tecnico" in corpo

    corpo = admin_client_logado.get(
        url("admin_user_detail", tecnico.pk)
    ).content.decode("utf-8")
    assert "Administrador tecnico" in corpo


def test_editar_superuser_nao_remove_o_privilegio_tecnico(admin_client_logado, db):
    tecnico = User.objects.create_superuser(
        email="tecnico@exemplo.test", full_name="Conta Tecnica", password=SENHA_BOA
    )

    admin_client_logado.post(
        url("admin_user_update", tecnico.pk),
        {
            "full_name": "Conta Tecnica Renomeada",
            "email": tecnico.email,
            "is_superuser": "false",
            "is_staff": "false",
        },
    )

    tecnico.refresh_from_db()
    assert tecnico.is_superuser is True
    assert tecnico.is_staff is True
    assert tecnico.full_name == "Conta Tecnica Renomeada"
