"""Validacao do arquivo de comprovante (Fase 2E). Sem banco: so o arquivo."""

import hashlib

import pytest

from common.exceptions import DomainError
from ministers.arquivo_comprovante import (
    CONTEUDO_INVALIDO,
    FORMATO_NAO_ACEITO,
    conferir_comprovante,
    nome_para_exibicao,
)
from ministers.models import TAMANHO_MAXIMO_DO_COMPROVANTE
from ministers.tests.conftest import (
    arquivo_enviado,
    imagem_de_teste,
    pdf_de_teste,
    png_declarando,
)


def erro_de(arquivo):
    with pytest.raises(DomainError) as erro:
        conferir_comprovante(arquivo)
    return str(erro.value)


# ---------------------------------------------------------------------------
# Validos
# ---------------------------------------------------------------------------


def test_pdf_valido():
    conteudo = pdf_de_teste()
    conferido = conferir_comprovante(arquivo_enviado(conteudo, "comprovante.pdf"))
    assert conferido.mime_type == "application/pdf"
    assert conferido.tamanho == len(conteudo)
    assert conferido.sha256 == hashlib.sha256(conteudo).hexdigest()
    assert conferido.nome_original == "comprovante.pdf"


@pytest.mark.parametrize("nome", ["print.png", "PRINT.PNG"])
def test_png_valido(nome):
    conferido = conferir_comprovante(arquivo_enviado(imagem_de_teste("PNG"), nome))
    assert conferido.mime_type == "image/png"


@pytest.mark.parametrize("nome", ["foto.jpg", "foto.jpeg", "FOTO.JPG"])
def test_jpeg_valido(nome):
    conferido = conferir_comprovante(arquivo_enviado(imagem_de_teste("JPEG"), nome))
    assert conferido.mime_type == "image/jpeg"


def test_tipo_gravado_e_o_do_conteudo_e_nao_o_da_extensao():
    # Print salvo como .jpg mas que e PNG: aceito (as duas sao imagem), e o
    # tipo registrado e o verdadeiro.
    conferido = conferir_comprovante(arquivo_enviado(imagem_de_teste("PNG"), "print.jpg", "image/jpeg"))
    assert conferido.mime_type == "image/png"


@pytest.mark.parametrize("declarado", ["application/octet-stream", ""])
def test_tipo_declarado_generico_e_aceito_porque_o_conteudo_decide(declarado):
    conferido = conferir_comprovante(arquivo_enviado(pdf_de_teste(), "comprovante.pdf", declarado))
    assert conferido.mime_type == "application/pdf"


def test_deixa_o_arquivo_no_inicio():
    arquivo = arquivo_enviado(pdf_de_teste(), "comprovante.pdf")
    conferir_comprovante(arquivo)
    assert arquivo.read(5) == b"%PDF-"


# ---------------------------------------------------------------------------
# Invalidos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "nome, conteudo, tipo",
    [
        ("comprovante.txt", b"pago", "text/plain"),
        ("comprovante.html", b"<html><script>alert(1)</script></html>", "text/html"),
        ("comprovante.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "image/svg+xml"),
        ("comprovante.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/msword"),
        ("comprovante.docx", b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("comprovante.zip", b"PK\x03\x04", "application/zip"),
        ("comprovante.exe", b"MZ\x90\x00", "application/x-msdownload"),
        ("comprovante", b"%PDF-1.4", "application/pdf"),
    ],
)
def test_formato_nao_aceito_pelo_nome(nome, conteudo, tipo):
    assert erro_de(arquivo_enviado(conteudo, nome, tipo)) == FORMATO_NAO_ACEITO


@pytest.mark.parametrize("declarado", ["text/html", "image/svg+xml", "application/x-msdownload"])
def test_tipo_declarado_perigoso_e_recusado_mesmo_com_conteudo_valido(declarado):
    assert erro_de(arquivo_enviado(pdf_de_teste(), "comprovante.pdf", declarado)) == FORMATO_NAO_ACEITO


@pytest.mark.parametrize(
    "nome, conteudo",
    [
        ("comprovante.jpg", b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200),  # executavel renomeado
        ("comprovante.png", b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200),
        ("comprovante.pdf", b"PK\x03\x04\x14\x00\x00\x00" + b"\x00" * 200),  # zip renomeado
        ("comprovante.jpg", b"PK\x03\x04\x14\x00\x00\x00" + b"\x00" * 200),
        ("comprovante.pdf", b"<html><body>pago</body></html>"),
        ("comprovante.png", b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'),
        ("comprovante.pdf", b"pago em 10/10/2026"),
    ],
)
def test_conteudo_que_nao_e_o_que_o_nome_diz(nome, conteudo):
    assert erro_de(arquivo_enviado(conteudo, nome)) == CONTEUDO_INVALIDO


def test_pdf_chamado_de_imagem_e_imagem_chamada_de_pdf():
    assert erro_de(arquivo_enviado(pdf_de_teste(), "comprovante.jpg")) == CONTEUDO_INVALIDO
    assert erro_de(arquivo_enviado(imagem_de_teste("PNG"), "comprovante.pdf")) == CONTEUDO_INVALIDO


def test_pdf_truncado():
    inteiro = pdf_de_teste()
    mensagem = erro_de(arquivo_enviado(inteiro[: len(inteiro) // 2], "comprovante.pdf"))
    assert "incompleto" in mensagem


def test_pdf_so_com_cabecalho():
    assert erro_de(arquivo_enviado(b"%PDF-1.7\n%%EOF\n", "comprovante.pdf")) == CONTEUDO_INVALIDO


def test_pdf_com_versao_invalida():
    conteudo = pdf_de_teste().replace(b"%PDF-1.", b"%PDF-9.", 1)
    assert erro_de(arquivo_enviado(conteudo, "comprovante.pdf")) == CONTEUDO_INVALIDO


def test_imagem_corrompida():
    inteira = imagem_de_teste("PNG", tamanho=(400, 400))
    mensagem = erro_de(arquivo_enviado(inteira[:80] + b"\x00" * 50 + inteira[130:], "print.png"))
    assert mensagem in (CONTEUDO_INVALIDO, "A imagem está corrompida ou incompleta. Envie novamente.")


def test_vazio():
    assert erro_de(arquivo_enviado(b"", "comprovante.pdf")) == "O arquivo enviado está vazio."


def test_nenhum_arquivo():
    assert erro_de(None) == "Selecione o comprovante."


# ---------------------------------------------------------------------------
# Tamanho
# ---------------------------------------------------------------------------


def test_exatamente_5_mb_passa_pelo_tamanho():
    base = pdf_de_teste()
    # Preenche com comentario antes do fim: continua um PDF valido.
    meio = b"%" + b"x" * (TAMANHO_MAXIMO_DO_COMPROVANTE - len(base) - 2) + b"\n"
    corpo = base.replace(b"%%EOF", meio + b"%%EOF")
    assert len(corpo) == TAMANHO_MAXIMO_DO_COMPROVANTE
    assert conferir_comprovante(arquivo_enviado(corpo, "comprovante.pdf")).tamanho == len(corpo)


def test_acima_de_5_mb_e_recusado_antes_de_ler_o_conteudo():
    corpo = b"%PDF-1.4\n" + b"0" * TAMANHO_MAXIMO_DO_COMPROVANTE
    assert erro_de(arquivo_enviado(corpo, "comprovante.pdf")) == "O arquivo tem 5,0 MB. O limite é 5 MB."


def test_8_mb():
    corpo = b"%PDF-1.4\n" + b"0" * (8 * 1024 * 1024)
    assert erro_de(arquivo_enviado(corpo, "comprovante.pdf")) == "O arquivo tem 8,0 MB. O limite é 5 MB."


# ---------------------------------------------------------------------------
# Bomba de descompressao
# ---------------------------------------------------------------------------


def test_bomba_de_descompressao_e_recusada():
    bomba = png_declarando(40000, 40000)
    assert len(bomba) < 200
    mensagem = erro_de(arquivo_enviado(bomba, "print.png"))
    assert "pixels" in mensagem


def test_area_acima_do_limite_e_recusada_pelo_cabecalho():
    # 7100 x 7100 = 50,4 megapixels: abaixo do aviso do Pillow, acima do nosso.
    mensagem = erro_de(arquivo_enviado(png_declarando(7100, 7100), "print.png"))
    assert "7100x7100 pixels" in mensagem


def test_lado_acima_do_limite_e_recusado():
    mensagem = erro_de(arquivo_enviado(png_declarando(13000, 10), "print.png"))
    assert "13000x10 pixels" in mensagem


def test_foto_de_celular_grande_passa():
    # 4000 x 3000 (12 MP), o tamanho comum de uma foto de celular.
    conferido = conferir_comprovante(
        arquivo_enviado(imagem_de_teste("JPEG", tamanho=(4000, 3000)), "foto.jpg")
    )
    assert conferido.mime_type == "image/jpeg"


# ---------------------------------------------------------------------------
# Nome original e SHA-256
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "enviado, exibido",
    [
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("..\\..\\Windows\\system32\\x.pdf", "x.pdf"),
        ("/var/www/html/a.pdf", "a.pdf"),
        ("comprovante\x00.exe.pdf", "comprovante.exe.pdf"),
        ("compro‮vante.pdf", "comprovante.pdf"),
        ("  Comprovante   Outubro .pdf ", "Comprovante Outubro .pdf"),
        ("", "comprovante"),
        ("..", "comprovante"),
    ],
)
def test_nome_original_vira_texto_seguro(enviado, exibido):
    assert nome_para_exibicao(enviado) == exibido


def test_nome_longo_e_cortado_preservando_a_extensao():
    nome = nome_para_exibicao("a" * 300 + ".pdf")
    assert len(nome) == 120 and nome.endswith(".pdf")


def test_conteudo_identico_tem_sha_identico_e_nome_nao_importa():
    conteudo = pdf_de_teste("mesmo")
    a = conferir_comprovante(arquivo_enviado(conteudo, "a.pdf"))
    b = conferir_comprovante(arquivo_enviado(conteudo, "outro nome.pdf"))
    c = conferir_comprovante(arquivo_enviado(pdf_de_teste("outro"), "a.pdf"))
    assert a.sha256 == b.sha256 != c.sha256
    assert len(a.sha256) == 64
