"""
PDF e QR Code.

O limite deste arquivo, dito com todas as letras
------------------------------------------------
Nao existe leitor de QR nem extrator de texto de PDF nesta suite, e nao vou
adicionar um so para testar. O que da para verificar sem eles cobre o que
realmente quebra:

    o arquivo e um PDF valido, de uma pagina, nao vazio
    a URL que ALIMENTA o QR aponta para o endereco publico correto
    o nome do arquivo nao carrega nada que possa injetar cabecalho

O desenho — se o nome ficou centralizado, se a moldura esta bonita — e
inspecao visual, e nao teste automatizado.
"""

import pytest

from certificates.pdf import (
    nome_de_arquivo_seguro,
    render_certificate_pdf,
    url_de_validacao,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# O arquivo
# ---------------------------------------------------------------------------


def test_gera_um_pdf_valido(certificado):
    dados = render_certificate_pdf(certificado)

    assert dados[:5] == b"%PDF-"
    assert dados.rstrip().endswith(b"%%EOF")
    assert len(dados) > 1000


def test_tem_exatamente_uma_pagina(certificado):
    """
    Uma pagina, sempre.

    Um certificado que vira duas paginas por causa de um nome longo imprime
    uma folha em branco atras — e quem recebe fica sem saber se falta algo.
    """
    dados = render_certificate_pdf(certificado)

    # /Type /Pages e o no da arvore; /Type /Page sao as folhas.
    paginas = dados.count(b"/Type /Page") - dados.count(b"/Type /Pages")
    assert paginas == 1


def test_o_qr_entra_no_arquivo(certificado):
    """A imagem do QR vira um XObject de imagem dentro do PDF."""
    dados = render_certificate_pdf(certificado)

    assert b"/Subtype /Image" in dados


def test_nome_muito_longo_nao_quebra_a_geracao(certificado):
    # Nao grava: o campo tem 150 caracteres e o teste quer medir o
    # renderizador, nao o limite da coluna.
    certificado.student_name_snapshot = "Maria " + "da Conceicao " * 10

    dados = render_certificate_pdf(certificado)
    paginas = dados.count(b"/Type /Page") - dados.count(b"/Type /Pages")

    assert dados[:5] == b"%PDF-"
    assert paginas == 1


def test_acentuacao_nao_quebra_a_geracao(certificado):
    certificado.student_name_snapshot = "Joao Conceicao de Assuncao"
    certificado.module_name_snapshot = "Introducao a Educacao Crista"

    dados = render_certificate_pdf(certificado)
    assert dados[:5] == b"%PDF-"


def test_nao_depende_de_fonte_instalada_no_servidor(certificado):
    """
    Somente as Type 1 padrao do formato.

    Uma fonte embutida apareceria como FontFile no PDF. Sem isso, o mesmo
    arquivo e gerado igual no Windows do desenvolvimento e na EC2, sem
    instalar nada no servidor.
    """
    dados = render_certificate_pdf(certificado)

    assert b"/FontFile" not in dados
    assert b"/Helvetica" in dados


# ---------------------------------------------------------------------------
# O endereco impresso no QR
# ---------------------------------------------------------------------------


def test_a_url_do_qr_usa_o_site_url_configurado(certificado, settings):
    settings.SITE_URL = "https://cpoadsum.nexeeo.com"

    endereco = url_de_validacao(certificado)

    assert endereco == "https://cpoadsum.nexeeo.com/certificados/validar/{}/".format(
        certificado.verification_code
    )


def test_a_url_do_qr_nunca_aponta_para_ip_ou_localhost(certificado, settings):
    """
    O caso que nao tem conserto.

    Um certificado impresso com QR apontando para 127.0.0.1 ou para o IP da
    maquina nasce inutil, e refazer significa reimprimir e redistribuir todos
    os documentos ja entregues.
    """
    settings.SITE_URL = "https://cpoadsum.nexeeo.com"
    endereco = url_de_validacao(certificado)

    assert endereco.startswith("https://")
    assert "127.0.0.1" not in endereco
    assert "localhost" not in endereco
    assert not endereco.startswith("http://")


def test_a_barra_final_do_site_url_nao_duplica(certificado, settings):
    settings.SITE_URL = "https://cpoadsum.nexeeo.com/"

    assert "//certificados" not in url_de_validacao(certificado)


# ---------------------------------------------------------------------------
# Nome do arquivo
# ---------------------------------------------------------------------------


def test_o_nome_do_arquivo_e_montado_por_lista_branca():
    nome = nome_de_arquivo_seguro("certificado", "Modulo 1", "Joao da Silva")

    assert nome == "certificado-modulo-1-joao-da-silva.pdf"


def test_quebra_de_linha_no_nome_nao_vaza_para_o_cabecalho():
    """
    Injecao de cabecalho pelo nome do aluno.

    Content-Disposition e delimitado por CRLF. Um nome contendo quebra de
    linha permitiria acrescentar cabecalhos arbitrarios a resposta.
    """
    nome = nome_de_arquivo_seguro(
        "certificado", "Modulo", 'Joao"\r\nSet-Cookie: admin=1'
    )

    assert "\r" not in nome
    assert "\n" not in nome
    assert '"' not in nome
    assert "Set-Cookie" not in nome
    assert ";" not in nome


def test_nome_sem_caractere_utilizavel_ainda_produz_arquivo():
    assert nome_de_arquivo_seguro("", "***", "///") == "certificado.pdf"


def test_o_nome_do_certificado_usa_os_snapshots(certificado):
    certificado.student_name_snapshot = "Joao da Silva"
    certificado.module_name_snapshot = "Modulo 1"

    assert certificado.nome_do_arquivo == "certificado-modulo-1-joao-da-silva.pdf"
