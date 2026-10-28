"""Fixtures do Controle de Ministros."""

import datetime

import pytest

from ministers import services
from ministers.models import MinisterialTitle

# Senha inicial padrao dos ministros nos testes. Distinta e reconhecivel,
# porque varios testes verificam que ela NUNCA aparece em tela, log ou trilha.
SENHA_PADRAO_MINISTRO = "Inicial#Ministro2026"

# Senha individual de um ministro.
SENHA_MINISTRO = "Pastor#Seguro2026"
SENHA_MINISTRO_NOVA = "Trocada#Ministro27"

URL_LOGIN = "/ministros/login/"
URL_LOGOUT = "/ministros/logout/"
URL_PAINEL = "/ministros/"
URL_PERFIL = "/ministros/perfil/"
URL_TROCAR_SENHA = "/ministros/alterar-senha/"


@pytest.fixture(autouse=True)
def default_minister_password(settings):
    settings.DEFAULT_MINISTER_PASSWORD = SENHA_PADRAO_MINISTRO
    return SENHA_PADRAO_MINISTRO


def dados_de_ministro(**extra):
    dados = {
        "full_name": "Rodrigo Exemplo Xavier",
        "conamad_registration": "17899",
        "field_registration": "7764",
        "ministerial_title": MinisterialTitle.PASTOR,
        "position": "Pastor Presidente",
        "congregation": "Sede",
        "consecration_year": 2002,
        "birth_date": datetime.date(1970, 5, 10),
        "sex": "M",
        "notes": "",
    }
    dados.update(extra)
    return dados


@pytest.fixture
def ministro(db):
    return services.create_minister(dados=dados_de_ministro())


@pytest.fixture
def ministro_com_acesso(db):
    return services.create_minister(dados=dados_de_ministro(), password=SENHA_MINISTRO)


@pytest.fixture
def outro_ministro(db):
    return services.create_minister(
        dados=dados_de_ministro(
            full_name="Bruna Exemplo Souza",
            conamad_registration="132927",
            field_registration="",
            ministerial_title=MinisterialTitle.EVANGELISTA,
            position="",
            congregation="Jardim Exemplo",
            sex="F",
        ),
        password="Evangelista#2026x",
    )


def entrar_no_portal(client, identificador="17899", senha=SENHA_MINISTRO):
    return client.post(URL_LOGIN, {"identificador": identificador, "senha": senha})


@pytest.fixture
def ministro_client_logado(client, ministro_com_acesso):
    resposta = entrar_no_portal(client)
    assert resposta.status_code == 302
    return client


# Configuracao de pagamento FICTICIA (Fase 2D). Nenhum dado de recebimento
# real da instituicao entra em teste: dominio example.com, reservado para
# documentacao, e um favorecido que diz que e teste.
PIX_DE_TESTE = {
    "pix_key_type": "EMAIL",
    "pix_key": "pix.teste@example.com",
    "pix_receiver_name": "AD Brás Sumaré Teste",
    "pix_receiver_city": "Sumaré",
    "payment_instructions": "Envie o comprovante para a secretaria.",
    "is_active": True,
}


@pytest.fixture
def configuracao_pix(db):
    from ministers import cobranca

    configuracao, _ = cobranca.salvar_configuracao(dict(PIX_DE_TESTE))
    return configuracao
