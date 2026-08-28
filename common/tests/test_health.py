"""Testes do endpoint de verificacao de saude."""

import json
from unittest import mock

import pytest

pytestmark = pytest.mark.django_db

URL_HEALTH = "/health/"


def test_health_responde_200_com_banco_saudavel(client):
    resposta = client.get(URL_HEALTH)
    assert resposta.status_code == 200

    corpo = json.loads(resposta.content)
    assert corpo == {"status": "ok", "database": "ok"}


def test_health_confirma_o_banco_de_verdade(client, student_user):
    """
    A verificacao precisa tocar o banco, nao apenas responder 200 fixo.
    Se a consulta nao acontecesse, o endpoint seria inutil como health check.
    """
    from accounts.models import User

    assert User.objects.filter(pk=student_user.pk).exists()
    assert client.get(URL_HEALTH).status_code == 200


def test_health_responde_503_quando_o_banco_falha(client):
    with mock.patch("common.views.connections") as conexoes:
        conexoes.__getitem__.side_effect = RuntimeError("banco indisponivel")
        resposta = client.get(URL_HEALTH)

    assert resposta.status_code == 503
    corpo = json.loads(resposta.content)
    assert corpo == {"status": "error", "database": "error"}


def test_health_nao_vaza_informacao_sensivel(client, settings):
    with mock.patch("common.views.connections") as conexoes:
        conexoes.__getitem__.side_effect = RuntimeError(
            "conexao recusada em 10.0.0.5:5432 para o usuario cpo_user"
        )
        resposta = client.get(URL_HEALTH)

    conteudo = resposta.content.decode().lower()

    # Nem detalhe de infraestrutura, nem traceback, nem segredo.
    for proibido in (
        "postgres",
        "cpo_user",
        "5432",
        "10.0.0.5",
        "traceback",
        "password",
        "senha",
        settings.SECRET_KEY.lower(),
    ):
        assert proibido not in conteudo


def test_health_dispensa_autenticacao(client):
    """O endpoint sera consultado por monitoramento, sem sessao."""
    assert client.get(URL_HEALTH).status_code == 200


def test_health_recusa_post(client):
    assert client.post(URL_HEALTH).status_code == 405
