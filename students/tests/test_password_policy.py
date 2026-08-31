"""
A nova politica de senha do aluno.

Mudanca de negocio da Etapa 5: quem define a senha do aluno e o administrador,
e o aluno nao a altera. Isso troca o modelo de "senha inicial padrao + troca
obrigatoria" por "senha definida na criacao + reset administrativo".

O que precisa continuar verdadeiro depois da mudanca
----------------------------------------------------
    a senha nunca aparece em HTML, log, auditoria ou mensagem
    a senha e sempre gravada com set_password, nunca atribuida direto
    o aluno recebe 403 ao tentar a tela de troca
    um aluno antigo com must_change_password=True nao fica preso em redirect
    resetar derruba as sessoes abertas com a senha anterior

O ultimo e o que exige teste de verdade, e nao leitura de codigo: ele depende
de o hash da senha entrar no calculo da chave de sessao do Django, o que e
comportamento do framework e nao do nosso codigo.
"""

import pytest
from django.urls import reverse

from audit.models import AuditEvent, AuditLog
from common.exceptions import DomainError
from students import services

pytestmark = pytest.mark.django_db

SENHA_BOA = "Prova#Segura2026"
SENHA_NOVA = "Outra#Segura2026"


# ---------------------------------------------------------------------------
# Criacao com senha
# ---------------------------------------------------------------------------


def test_criacao_grava_a_senha_com_hash(db, admin_user):
    aluno = services.create_student(
        full_name="Antonio Melo",
        email="antonio@escola.test",
        password=SENHA_BOA,
        actor=admin_user,
    )

    # Nunca em claro. Se alguem trocar set_password por atribuicao direta,
    # este teste falha imediatamente.
    assert aluno.password != SENHA_BOA
    assert aluno.password.startswith("pbkdf2_")
    assert aluno.check_password(SENHA_BOA)


def test_aluno_criado_nao_precisa_trocar_a_senha(db, admin_user):
    """
    A regra antiga deixa de existir.

    Obrigar a troca prenderia o aluno num formulario que ele nao tem permissao
    de enviar — a TrocarSenhaView agora responde 403 para STUDENT.
    """
    aluno = services.create_student(
        full_name="Antonio Melo",
        email="antonio@escola.test",
        password=SENHA_BOA,
        actor=admin_user,
    )

    assert aluno.must_change_password is False


def test_senha_fraca_e_recusada(db, admin_user):
    """Os validadores do Django valem tambem para a senha que o admin define."""
    with pytest.raises(DomainError):
        services.create_student(
            full_name="Antonio Melo",
            email="antonio@escola.test",
            password="123",
            actor=admin_user,
        )


def test_a_senha_nao_entra_na_auditoria(db, admin_user):
    services.create_student(
        full_name="Antonio Melo",
        email="antonio@escola.test",
        password=SENHA_BOA,
        actor=admin_user,
    )

    trilha = " ".join(
        str(linha)
        for linha in AuditLog.objects.values_list("metadata", flat=True)
    )
    assert SENHA_BOA not in trilha


def test_importacao_continua_usando_a_senha_padrao(db, admin_user, settings):
    """
    Sem password, cai na senha padrao do ambiente.

    Nao ha como digitar uma senha diferente para cada linha de uma planilha de
    duzentos alunos, entao a importacao mantem o comportamento anterior — mas
    ja sem a troca obrigatoria, e o administrador pode resetar depois.
    """
    aluno = services.create_student(
        full_name="Antonio Melo",
        email="antonio@escola.test",
        actor=admin_user,
    )

    assert aluno.check_password(settings.DEFAULT_STUDENT_PASSWORD)
    assert aluno.must_change_password is False


# ---------------------------------------------------------------------------
# Reset administrativo
# ---------------------------------------------------------------------------


def test_reset_troca_a_senha(db, student_user, admin_user):
    services.reset_student_password(
        student_user, new_password=SENHA_NOVA, actor=admin_user
    )

    student_user.refresh_from_db()
    assert student_user.check_password(SENHA_NOVA)


def test_a_senha_antiga_para_de_funcionar(db, admin_user, default_student_password):
    aluno = services.create_student(
        full_name="Antonio Melo",
        email="antonio@escola.test",
        password=SENHA_BOA,
        actor=admin_user,
    )

    services.reset_student_password(
        aluno, new_password=SENHA_NOVA, actor=admin_user
    )

    aluno.refresh_from_db()
    assert not aluno.check_password(SENHA_BOA)
    assert aluno.check_password(SENHA_NOVA)


def test_reset_recusa_senha_fraca(db, student_user, admin_user):
    antigo = student_user.password

    with pytest.raises(DomainError):
        services.reset_student_password(
            student_user, new_password="123", actor=admin_user
        )

    student_user.refresh_from_db()
    assert student_user.password == antigo


def test_reset_recusa_administrador(db, admin_user):
    """
    O ADMIN tem fluxo proprio.

    Se esta tela aceitasse administradores, um admin poderia trocar a senha de
    outro e assumir a conta dele sem deixar rastro de invasao.
    """
    with pytest.raises(DomainError):
        services.reset_student_password(
            admin_user, new_password=SENHA_NOVA, actor=admin_user
        )


def test_reset_registra_evento_sem_a_senha(db, student_user, admin_user):
    services.reset_student_password(
        student_user, new_password=SENHA_NOVA, actor=admin_user
    )

    evento = AuditLog.objects.filter(
        event=AuditEvent.STUDENT_PASSWORD_RESET
    ).first()

    assert evento is not None
    assert evento.actor_id == admin_user.pk
    assert evento.student_id == student_user.pk
    # A chave nao contem "password" de proposito: o sanitizador da trilha
    # descarta por substring qualquer chave que contenha essa palavra, e essa
    # regra grosseira e justamente o que protege a trilha inteira. O nome do
    # evento ja diz o que aconteceu.
    assert evento.metadata == {"redefinida": True}

    # Nem a senha, nem o hash, nem o comprimento.
    texto = str(evento.metadata)
    assert SENHA_NOVA not in texto
    assert str(len(SENHA_NOVA)) not in texto


def test_reset_limpa_a_troca_obrigatoria(db, student_com_troca_pendente, admin_user):
    services.reset_student_password(
        student_com_troca_pendente, new_password=SENHA_NOVA, actor=admin_user
    )

    student_com_troca_pendente.refresh_from_db()
    assert student_com_troca_pendente.must_change_password is False


# ---------------------------------------------------------------------------
# Sessoes antigas caem
# ---------------------------------------------------------------------------


def test_a_sessao_aberta_cai_depois_do_reset(client, db, admin_user):
    """
    O teste que nao da para substituir por leitura de codigo.

    A chave de sessao do Django deriva do hash da senha
    (AbstractBaseUser.get_session_auth_hash). Trocar a senha muda o hash, e o
    SessionAuthenticationMiddleware invalida a sessao no request seguinte.

    Aqui isso e exercitado de ponta a ponta: o aluno entra, navega, o
    administrador reseta a senha, e o proximo request dele ja nao esta
    autenticado. Nao ha sistema paralelo de sessao — e comportamento do
    framework, e o teste confirma que ele continua ligado.
    """
    aluno = services.create_student(
        full_name="Antonio Melo",
        email="antonio@escola.test",
        password=SENHA_BOA,
        actor=admin_user,
    )

    entrou = client.login(email=aluno.email, password=SENHA_BOA)
    assert entrou is True

    painel = reverse("student:dashboard")
    assert client.get(painel).status_code == 200

    services.reset_student_password(
        aluno, new_password=SENHA_NOVA, actor=admin_user
    )

    # O request seguinte nao continua autenticado: cai no login.
    resposta = client.get(painel)
    assert resposta.status_code == 302
    assert reverse("accounts:login") in resposta["Location"]


def test_a_senha_nova_autentica(client, db, admin_user):
    aluno = services.create_student(
        full_name="Antonio Melo",
        email="antonio@escola.test",
        password=SENHA_BOA,
        actor=admin_user,
    )
    services.reset_student_password(
        aluno, new_password=SENHA_NOVA, actor=admin_user
    )

    assert client.login(email=aluno.email, password=SENHA_NOVA) is True


# ---------------------------------------------------------------------------
# O aluno nao altera a propria senha
# ---------------------------------------------------------------------------


def test_aluno_recebe_403_na_tela_de_troca(student_client_logado):
    """
    403, e nao 404.

    A rota existe e o aluno sabe que existe — e o mesmo endereco que ele usava
    antes. O que mudou foi a permissao, e e isso que a resposta precisa dizer.
    """
    resposta = student_client_logado.get(reverse("accounts:change_password"))
    assert resposta.status_code == 403


def test_aluno_nao_consegue_trocar_por_post(student_client_logado, student_user):
    antigo = student_user.password

    resposta = student_client_logado.post(
        reverse("accounts:change_password"),
        {
            "old_password": "qualquer",
            "new_password1": SENHA_NOVA,
            "new_password2": SENHA_NOVA,
        },
    )

    assert resposta.status_code == 403
    student_user.refresh_from_db()
    assert student_user.password == antigo


def test_a_area_do_aluno_nao_oferece_o_link(student_client_logado):
    corpo = student_client_logado.get(reverse("student:dashboard")).content.decode(
        "utf-8"
    )
    assert "Alterar senha" not in corpo
    assert reverse("accounts:change_password") not in corpo


def test_administrador_continua_trocando_a_propria_senha(admin_client_logado):
    """A rota nao foi desligada: ela apenas deixou de valer para o aluno."""
    resposta = admin_client_logado.get(reverse("accounts:change_password"))
    assert resposta.status_code == 200


# ---------------------------------------------------------------------------
# Aluno antigo nao fica preso
# ---------------------------------------------------------------------------


def test_aluno_antigo_com_flag_ligada_nao_entra_em_redirect_infinito(
    client, student_com_troca_pendente, default_student_password
):
    """
    O risco concreto da mudanca.

    Alunos criados antes da Etapa 5 tem must_change_password=True. Se o
    middleware continuasse redirecionando por causa dessa flag, eles seriam
    mandados para uma tela que agora responde 403 — e voltariam para la a cada
    request. Um loop sem saida, em producao, para quem ja existia.

    A flag continua no modelo e continua valendo para ADMIN; o que ela nao faz
    mais e comandar o fluxo do STUDENT.
    """
    client.force_login(student_com_troca_pendente)

    resposta = client.get(reverse("student:dashboard"))

    assert resposta.status_code == 200


def test_administrador_com_a_flag_ainda_e_redirecionado(client, admin_user):
    """O outro lado: para o ADMIN a regra antiga continua de pe."""
    admin_user.must_change_password = True
    admin_user.save(update_fields=["must_change_password"])

    client.force_login(admin_user)
    resposta = client.get(reverse("admin_panel:dashboard"))

    assert resposta.status_code == 302
    assert reverse("accounts:change_password") in resposta["Location"]
