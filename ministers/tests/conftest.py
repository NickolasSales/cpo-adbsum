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


# ---------------------------------------------------------------------------
# Comprovantes (Fase 2E): arquivos de teste gerados na hora. Nenhum
# comprovante real entra no repositorio.
# ---------------------------------------------------------------------------


def pdf_de_teste(texto="Comprovante de teste"):
    """Um PDF de verdade, de uma pagina, gerado pelo ReportLab."""
    import io

    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, invariant=1)
    c.drawString(72, 720, texto)
    c.showPage()
    c.save()
    return buffer.getvalue()


def imagem_de_teste(formato="PNG", tamanho=(390, 844), cor=(240, 248, 255)):
    """Um print de celular: PNG ou JPEG de verdade."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", tamanho, cor).save(buffer, formato)
    return buffer.getvalue()


def png_declarando(largura, altura):
    """
    PNG minusculo cujo cabecalho DECLARA largura x altura. E a forma da
    bomba de descompressao: poucos bytes que prometem gigabytes de pixels.
    """
    import struct
    import zlib

    def bloco(tipo, dados):
        corpo = tipo + dados
        return struct.pack(">I", len(dados)) + corpo + struct.pack(">I", zlib.crc32(corpo) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", largura, altura, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + bloco(b"IHDR", ihdr)
        + bloco(b"IDAT", zlib.compress(b"\x00" * 64))
        + bloco(b"IEND", b"")
    )


def arquivo_enviado(conteudo, nome="comprovante.pdf", tipo=None):
    from django.core.files.uploadedfile import SimpleUploadedFile

    if tipo is None:
        tipo = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
            nome.rsplit(".", 1)[-1].lower(), "application/octet-stream"
        )
    return SimpleUploadedFile(nome, conteudo, content_type=tipo)
