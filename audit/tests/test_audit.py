"""Testes da trilha de auditoria."""

import pytest

from audit.models import AuditEvent, AuditLog
from audit.services import VALOR_OCULTADO, record, sanitizar_metadata

pytestmark = pytest.mark.django_db

URL_LOGIN = "/login/"
URL_TROCAR_SENHA = "/alterar-senha/"


def fazer_login(client, email, senha):
    return client.post(URL_LOGIN, {"username": email, "password": senha})


# ---------------------------------------------------------------------------
# Eventos de autenticacao
# ---------------------------------------------------------------------------


def test_login_com_sucesso_gera_registro(client, student_user, senha):
    fazer_login(client, student_user.email, senha)

    log = AuditLog.objects.get(event=AuditEvent.LOGIN_SUCCESS)
    assert log.actor_id == student_user.pk
    assert log.student_id == student_user.pk
    assert log.entity_type == "User"
    assert log.entity_id == str(student_user.pk)
    assert log.ip_address is not None


def test_login_de_admin_nao_marca_campo_aluno(client, admin_user, senha):
    fazer_login(client, admin_user.email, senha)

    log = AuditLog.objects.get(event=AuditEvent.LOGIN_SUCCESS)
    assert log.actor_id == admin_user.pk
    assert log.student_id is None


def test_falha_de_login_gera_registro(client, student_user):
    fazer_login(client, student_user.email, "senha-errada")

    log = AuditLog.objects.get(event=AuditEvent.LOGIN_FAILED)
    assert log.actor_id is None
    assert log.metadata["email"] == student_user.email


def test_falha_de_login_com_email_inexistente_tambem_registra(client):
    fazer_login(client, "ninguem@exemplo.test", "qualquer")
    assert AuditLog.objects.filter(event=AuditEvent.LOGIN_FAILED).count() == 1


def test_troca_de_senha_gera_registro(
    client, student_com_troca_pendente, senha, senha_nova
):
    fazer_login(client, student_com_troca_pendente.email, senha)
    client.post(
        URL_TROCAR_SENHA,
        {
            "old_password": senha,
            "new_password1": senha_nova,
            "new_password2": senha_nova,
        },
    )

    log = AuditLog.objects.get(event=AuditEvent.PASSWORD_CHANGED)
    assert log.actor_id == student_com_troca_pendente.pk
    assert log.entity_type == "User"


# ---------------------------------------------------------------------------
# Nenhum segredo pode chegar ao banco
# ---------------------------------------------------------------------------


def test_registro_de_falha_nao_contem_a_senha_tentada(client, student_user):
    senha_tentada = "MinhaSenhaSecretaTentada123"
    fazer_login(client, student_user.email, senha_tentada)

    log = AuditLog.objects.get(event=AuditEvent.LOGIN_FAILED)
    conteudo = str(log.metadata)
    assert senha_tentada not in conteudo
    assert "password" not in conteudo.lower()


def test_nenhum_registro_de_autenticacao_carrega_senha(
    client, student_com_troca_pendente, senha, senha_nova
):
    fazer_login(client, student_com_troca_pendente.email, "errada-de-proposito")
    fazer_login(client, student_com_troca_pendente.email, senha)
    client.post(
        URL_TROCAR_SENHA,
        {
            "old_password": senha,
            "new_password1": senha_nova,
            "new_password2": senha_nova,
        },
    )

    for log in AuditLog.objects.all():
        conteudo = str(log.metadata)
        assert senha not in conteudo
        assert senha_nova not in conteudo
        assert "errada-de-proposito" not in conteudo


@pytest.mark.parametrize(
    "chave",
    [
        "password",
        "new_password1",
        "senha",
        "SENHA_ATUAL",
        "token",
        "access_token",
        "secret",
        "SECRET_KEY",
        "authorization",
        "csrfmiddlewaretoken",
        "sessionid",
        "cookie",
        "api_key",
        "credential",
    ],
)
def test_sanitizacao_remove_chaves_sensiveis(chave):
    limpo = sanitizar_metadata({chave: "valor-secreto"})
    assert limpo[chave] == VALOR_OCULTADO


def test_sanitizacao_e_recursiva():
    sujo = {
        "nivel1": {"password": "abc", "ok": "visivel"},
        "lista": [{"token": "xyz"}, {"nome": "Maria"}],
    }
    limpo = sanitizar_metadata(sujo)

    assert limpo["nivel1"]["password"] == VALOR_OCULTADO
    assert limpo["nivel1"]["ok"] == "visivel"
    assert limpo["lista"][0]["token"] == VALOR_OCULTADO
    assert limpo["lista"][1]["nome"] == "Maria"


def test_sanitizacao_preserva_o_que_nao_e_sensivel():
    limpo = sanitizar_metadata({"email": "a@b.test", "role": "STUDENT", "n": 3})
    assert limpo == {"email": "a@b.test", "role": "STUDENT", "n": 3}


def test_servico_sanitiza_antes_de_persistir(student_user):
    record(
        AuditEvent.LOGIN_SUCCESS,
        actor=student_user,
        metadata={"password": "nunca-deveria-chegar-aqui", "email": "a@b.test"},
    )
    log = AuditLog.objects.latest("id")
    assert log.metadata["password"] == VALOR_OCULTADO
    assert log.metadata["email"] == "a@b.test"


# ---------------------------------------------------------------------------
# Somente insercao
# ---------------------------------------------------------------------------


def test_registro_existente_nao_pode_ser_alterado(student_user):
    log = record(AuditEvent.LOGIN_SUCCESS, actor=student_user)
    log.event = AuditEvent.LOGIN_FAILED

    with pytest.raises(ValueError):
        log.save()


def test_registro_nao_pode_ser_excluido(student_user):
    log = record(AuditEvent.LOGIN_SUCCESS, actor=student_user)

    with pytest.raises(ValueError):
        log.delete()


def test_falha_ao_auditar_nao_derruba_a_operacao(student_user, monkeypatch):
    """
    Auditoria e importante, mas nao pode ser ponto unico de falha: se a
    gravacao do log quebrar, a operacao de negocio precisa seguir.
    """
    def explodir(*args, **kwargs):
        raise RuntimeError("banco fora do ar")

    monkeypatch.setattr(AuditLog.objects, "create", explodir)

    assert record(AuditEvent.LOGIN_SUCCESS, actor=student_user) is None
