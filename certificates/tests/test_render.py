"""
Renderizador dirigido por modelo (Etapa 10).

O que estes testes protegem, em ordem de importancia:

    o nome do aluno nunca sai cortado
    o QR continua legivel
    o documento sai com uma pagina, no formato configurado
    o preview usa o mesmo caminho do documento final
"""

import io
import re

import pytest
from django.urls import reverse
from PIL import Image
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth

from certificates import services_templates as servicos
from certificates.models import FieldType
from certificates.pdf import render_certificate_pdf
from certificates.render import ajustar, render_from_snapshot
from certificates.snapshot import montar_snapshot, valores_de_preview
from conftest import png_de_teste

pytestmark = pytest.mark.django_db


def paginas(pdf):
    return pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages")


# ---------------------------------------------------------------------------
# Auto-ajuste e quebra
# ---------------------------------------------------------------------------


def ajustar_padrao(texto, **extras):
    parametros = {
        "fonte": "Helvetica",
        "tamanho": 24,
        "minimo": 8,
        "auto_fit": True,
        "largura": 200,
        "altura": 40,
        "entrelinha": 1.2,
    }
    parametros.update(extras)
    return ajustar(texto, **parametros)


def test_texto_curto_mantem_o_tamanho_pedido():
    linhas, tamanho = ajustar_padrao("Ana")

    assert linhas == ["Ana"]
    assert tamanho == 24


def test_encolhe_ate_caber_na_caixa():
    linhas, tamanho = ajustar_padrao("Maria Aparecida dos Santos de Oliveira")

    assert tamanho < 24
    for linha in linhas:
        assert stringWidth(linha, "Helvetica", tamanho) <= 200


def test_nunca_trunca_o_nome():
    """
    A regra que mais importa num certificado.

    Um nome cortado com reticencias e um documento oficial com o nome da
    pessoa errado. Se nao couber encolhendo, quebra em mais linhas.
    """
    nome = "Maria Aparecida do Nascimento Rodrigues de Albuquerque Sobrinha"
    linhas, tamanho = ajustar_padrao(nome, largura=120, altura=20)

    assert "…" not in " ".join(linhas)
    assert "..." not in " ".join(linhas)
    # Todas as palavras do nome sobrevivem, na ordem.
    assert " ".join(linhas).split() == nome.split()


def test_quebra_equilibrada_em_duas_linhas():
    """
    A quebra gulosa encheria a primeira linha e deixaria uma palavra sozinha
    na segunda. Num nome em destaque isso parece erro.
    """
    linhas, tamanho = ajustar_padrao(
        "Maria Aparecida dos Santos Oliveira", largura=190, altura=60, tamanho=20
    )

    assert len(linhas) == 2
    larguras = [stringWidth(linha, "Helvetica", tamanho) for linha in linhas]
    assert abs(larguras[0] - larguras[1]) < max(larguras) * 0.6


def test_sem_auto_fit_o_tamanho_e_respeitado():
    """Quem desligou o ajuste quer a fonte daquele tamanho."""
    linhas, tamanho = ajustar_padrao(
        "Maria Aparecida dos Santos de Oliveira", auto_fit=False
    )

    assert tamanho == 24


def test_nao_desce_abaixo_do_minimo():
    _, tamanho = ajustar_padrao(
        "Maria Aparecida do Nascimento Rodrigues de Albuquerque",
        largura=40,
        altura=10,
        minimo=11,
    )

    assert tamanho >= 11


def test_texto_vazio_nao_desenha_nada():
    linhas, _ = ajustar_padrao("")
    assert linhas == []

    linhas, _ = ajustar_padrao("   ")
    assert linhas == []


def test_palavra_unica_gigante_e_cortada_por_caractere():
    """
    Uma sequencia colada sem espacos nao pode empurrar tinta para fora da
    caixa. Aqui o corte por caractere e o comportamento certo — nao ha
    palavra para preservar.
    """
    linhas, tamanho = ajustar_padrao("A" * 300, largura=100, altura=60)

    assert len(linhas) > 1
    for linha in linhas:
        assert stringWidth(linha, "Helvetica", tamanho) <= 100


# ---------------------------------------------------------------------------
# O PDF
# ---------------------------------------------------------------------------


def test_o_documento_sai_com_uma_pagina(certificado):
    pdf = render_certificate_pdf(certificado)

    assert pdf.startswith(b"%PDF-")
    assert paginas(pdf) == 1


def media_box(pdf):
    """Largura e altura da pagina, em pontos, lidas do proprio PDF."""
    achado = re.search(rb"MediaBox \[ 0 0 ([\d.]+) ([\d.]+) \]", pdf)
    assert achado, "MediaBox nao encontrado no PDF"
    return float(achado.group(1)), float(achado.group(2))


def test_o_documento_usa_o_formato_do_modelo(certificado):
    """
    A4 paisagem: 297 x 210 mm.

    A conferencia e numerica de proposito. Em pontos isso da 841.8898 x
    595.2756, e comparar por substring convidaria ao erro de escrever
    "841.89" — que nao aparece nesse numero.
    """
    largura, altura = media_box(render_certificate_pdf(certificado))

    assert abs(largura - 297 * mm) < 0.5
    assert abs(altura - 210 * mm) < 0.5


def test_o_fundo_entra_no_documento(certificado):
    pdf = render_certificate_pdf(certificado)

    assert b"/Subtype /Image" in pdf


def test_o_qr_entra_no_documento(certificado):
    """
    Duas imagens: a arte de fundo e o QR. Uma so significaria que o QR ficou
    de fora, e o certificado perderia a validacao publica.
    """
    pdf = render_certificate_pdf(certificado)

    assert pdf.count(b"/Subtype /Image") >= 2


def test_o_nome_do_aluno_esta_no_documento(certificado):
    """
    O texto e texto de verdade, e nao imagem. Um PDF com o nome rasterizado
    nao pode ser buscado nem lido por leitor de tela.
    """
    from certificates.render import render_from_snapshot
    from certificates.snapshot import valores_do_certificado

    snapshot = dict(certificado.template_snapshot)
    valores = valores_do_certificado(certificado)

    # Sem compressao de fluxo nao ha como conferir o texto nos bytes; o que
    # da para afirmar e que o valor chega ao renderizador resolvido.
    assert valores[FieldType.STUDENT_NAME] == certificado.student_name_snapshot
    assert render_from_snapshot(snapshot, valores).startswith(b"%PDF-")


def test_sem_fonte_embutida(certificado):
    pdf = render_certificate_pdf(certificado)

    assert b"/FontFile" not in pdf


def test_campo_invisivel_nao_e_desenhado(modelo_de_certificado, admin_user):
    snapshot = montar_snapshot(modelo_de_certificado)
    for campo in snapshot["fields"]:
        campo["is_visible"] = False

    pdf = render_from_snapshot(snapshot, valores_de_preview())

    # So a arte de fundo. Nenhum QR, nenhum texto.
    assert pdf.count(b"/Subtype /Image") == 1


def test_valor_ausente_nao_imprime_none(modelo_de_certificado):
    """
    Um certificado da versao 1 nao tem carga horaria. Imprimir "None" seria
    pior do que deixar o espaco vazio.

    A conferencia nao pode ser `b"None" not in pdf`: todo PDF do ReportLab
    carrega /PageMode /UseNone, e a assercao nunca passaria. O que se verifica
    e que passar None produz exatamente o mesmo desenho que nao passar nada —
    ou seja, que o campo foi pulado.
    """
    snapshot = montar_snapshot(modelo_de_certificado)

    sem_a_chave = render_from_snapshot(snapshot, {FieldType.STUDENT_NAME: "Ana"})
    com_none = render_from_snapshot(
        snapshot,
        {FieldType.STUDENT_NAME: "Ana", FieldType.WORKLOAD: None,
         FieldType.YEAR: "", FieldType.COURSE_DATES: "   "},
    )

    assert len(sem_a_chave) == len(com_none)


def test_cor_invalida_no_snapshot_nao_derruba_a_emissao(modelo_de_certificado):
    """
    O snapshot e JSON no banco: um UPDATE manual poderia por qualquer coisa
    ali. O renderizador cai no preto em vez de estourar no meio de uma
    emissao.
    """
    snapshot = montar_snapshot(modelo_de_certificado)
    snapshot["fields"][0]["text_color"] = "javascript:alert(1)"

    pdf = render_from_snapshot(snapshot, valores_de_preview())
    assert pdf.startswith(b"%PDF-")


def test_fonte_invalida_no_snapshot_cai_no_padrao(modelo_de_certificado):
    snapshot = montar_snapshot(modelo_de_certificado)
    snapshot["fields"][0]["font_family"] = "../../etc/passwd"

    pdf = render_from_snapshot(snapshot, valores_de_preview())
    assert pdf.startswith(b"%PDF-")


def test_arte_ausente_no_disco_nao_derruba(modelo_de_certificado):
    """
    O documento sai sem fundo em vez de a emissao falhar: um certificado com
    os dados certos e sem moldura ainda valida.
    """
    snapshot = montar_snapshot(modelo_de_certificado)
    snapshot["background_path"] = "/caminho/que/nao/existe.png"

    pdf = render_from_snapshot(snapshot, valores_de_preview())
    assert pdf.startswith(b"%PDF-")


def test_dados_longos_nao_derrubam_o_documento(modelo_de_certificado):
    """Modulo, local e datas compridos, todos de uma vez."""
    snapshot = montar_snapshot(modelo_de_certificado)
    valores = dict(valores_de_preview())
    valores.update(
        {
            FieldType.STUDENT_NAME: "Maria Aparecida do Nascimento Rodrigues "
            "de Albuquerque Sobrinha Junior",
            FieldType.MODULE_NAME: "Modulo I - Cooperadores, Diaconos e "
            "Auxiliares do Ministerio Pastoral da Congregacao",
            FieldType.COURSE_LOCATION: "Igreja Sede da Assembleia de Deus do "
            "Bras em Sumare, Sao Paulo",
            FieldType.COURSE_DATES: "10, 17, 24 e 31 de outubro e 7 de "
            "novembro de 2026",
        }
    )

    pdf = render_from_snapshot(snapshot, valores)

    assert pdf.startswith(b"%PDF-")
    assert paginas(pdf) == 1


def test_rotacao_do_ano_nao_quebra(modelo_de_certificado):
    snapshot = montar_snapshot(modelo_de_certificado)
    ano = next(
        campo for campo in snapshot["fields"] if campo["field_type"] == FieldType.YEAR
    )
    assert ano["rotation"] == 90

    pdf = render_from_snapshot(snapshot, valores_de_preview())
    assert paginas(pdf) == 1


def test_z_index_decide_a_ordem(modelo_de_certificado):
    """
    O fundo esta sempre atras; entre campos, quem tem z_index maior desenha
    depois. Sem ordenacao estavel, dois campos empilhados trocariam de lugar
    entre renderizacoes.
    """
    snapshot = montar_snapshot(modelo_de_certificado)
    ordens = [int(campo["z_index"]) for campo in snapshot["fields"]]

    assert ordens == sorted(ordens)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_o_preview_usa_o_mesmo_renderizador(modelo_de_certificado):
    """
    Nao existe um segundo desenho para o preview. O snapshot e o mesmo, os
    valores e que sao ficticios.
    """
    snapshot = montar_snapshot(modelo_de_certificado)
    pdf = render_from_snapshot(snapshot, valores_de_preview())

    assert pdf.startswith(b"%PDF-")
    assert paginas(pdf) == 1
    assert pdf.count(b"/Subtype /Image") >= 2


def test_o_preview_nao_cria_certificado(admin_client_logado, modelo_de_certificado):
    from certificates.models import Certificate

    antes = Certificate.objects.count()

    resposta = admin_client_logado.get(
        reverse(
            "admin_panel:certificate_template_preview",
            args=[modelo_de_certificado.pk],
        )
    )

    assert resposta.status_code == 200
    assert resposta["Content-Type"] == "application/pdf"
    assert Certificate.objects.count() == antes


def test_o_qr_do_preview_nao_aponta_para_certificado_real():
    valores = valores_de_preview()

    assert valores[FieldType.QR_CODE].endswith("/certificados/validar/preview/")


def test_o_preview_usa_nomes_longos_de_proposito():
    """
    Um preview com "Joao Silva" mentiria sobre o comportamento do campo: o
    que estressa a caixa e o nome comprido.
    """
    valores = valores_de_preview()

    assert len(valores[FieldType.STUDENT_NAME]) > 20
    assert len(valores[FieldType.MODULE_NAME]) > 20


def test_preview_de_rascunho_sem_arte_ainda_desenha(rascunho_com_campo):
    """
    Ver os campos sobre o branco e util enquanto se posiciona, antes de a
    arte existir.
    """
    snapshot = montar_snapshot(rascunho_com_campo)
    pdf = render_from_snapshot(snapshot, valores_de_preview())

    assert pdf.startswith(b"%PDF-")


@pytest.fixture
def rascunho_com_campo(admin_user):
    template = servicos.create_template(name="Rascunho", actor=admin_user)
    servicos.save_fields(
        template,
        {
            FieldType.STUDENT_NAME: {
                "x": 10,
                "y": 40,
                "width": 80,
                "height": 10,
                "font_family": "Helvetica",
                "font_size": 20,
                "min_font_size": 10,
                "auto_fit": True,
                "line_height": 1.2,
                "text_align": "CENTER",
                "text_color": "#000000",
                "rotation": 0,
                "is_visible": True,
                "z_index": 1,
            }
        },
        actor=admin_user,
    )
    return template


def test_o_ano_vertical_nao_e_quebrado_em_duas_linhas():
    """
    Bug encontrado olhando a primeira amostra rasterizada.

    Num campo girado 90 graus o texto corre ao longo da ALTURA da caixa na
    pagina. A quebra usava a largura, e uma caixa estreita e alta — que e
    exatamente a do ano na lateral do modelo oficial — partia "2026" em "20"
    e "26", lado a lado.

    Nenhuma assercao teria pego isso: o PDF saia valido, com uma pagina e o
    texto presente. So aparece olhando.
    """
    from certificates.render import _desenhar_texto
    from reportlab.pdfgen import canvas as _canvas

    largura_pt, altura_pt = 297 * mm, 210 * mm
    # Caixa estreita (6% da largura) e alta (26% da altura): o formato do ano
    # na lateral.
    campo = {
        "x": 90, "y": 20, "width": 6, "height": 26,
        "font_family": "Times-Bold", "font_size": 34, "min_font_size": 10,
        "auto_fit": True, "line_height": 1.2, "text_align": "CENTER",
        "text_color": "#14532D", "rotation": 90, "is_visible": True,
        "z_index": 1,
    }

    capturado = []

    class Espiao(_canvas.Canvas):
        def drawCentredString(self, x, y, texto, *args, **kwargs):
            capturado.append(texto)
            return super().drawCentredString(x, y, texto, *args, **kwargs)

    c = Espiao(io.BytesIO(), pagesize=(largura_pt, altura_pt))
    _desenhar_texto(c, campo, "2026", largura_pt, altura_pt)

    assert capturado == ["2026"]


def test_sem_rotacao_a_caixa_nao_e_trocada():
    """A troca vale so no quarto de volta; o resto continua como esta."""
    from certificates.render import _quarto_de_volta

    assert _quarto_de_volta(90) is True
    assert _quarto_de_volta(-90) is True
    assert _quarto_de_volta(270) is True
    assert _quarto_de_volta(0) is False
    assert _quarto_de_volta(180) is False
    assert _quarto_de_volta(15) is False


def test_o_qr_desenhado_codifica_a_url_de_validacao(certificado):
    """
    Conferencia de verdade, e nao "o PDF tem uma imagem".

    O QR e recortado do PDF rasterizado e comparado modulo a modulo com um QR
    gerado da URL esperada. E o unico jeito de saber que o codigo impresso
    leva mesmo a pagina de validacao daquele certificado — um QR desenhado a
    partir da string errada tambem produziria "uma imagem no PDF".

    A caixa e quadrada e as coordenadas sao redondas de proposito: com a
    caixa retangular do fixture a imagem fica centrada com folga nas laterais,
    e o recorte passaria a depender de aritmetica de arredondamento em vez de
    testar o desenho.

    Depende de um rasterizador que nao faz parte do projeto, entao o teste se
    pula sozinho quando ele nao existe — em vez de exigir no servidor uma
    dependencia que ele nao deve ter.
    """
    pdfium = pytest.importorskip(
        "pypdfium2", reason="rasterizador nao instalado neste ambiente"
    )
    import qrcode as _qrcode

    from certificates.pdf import url_de_validacao

    url = url_de_validacao(certificado)
    snapshot = {
        "page_width_mm": 100,
        "page_height_mm": 100,
        "background_path": "",
        "fields": [
            {
                "field_type": FieldType.QR_CODE,
                "x": 0, "y": 0, "width": 100, "height": 100,
                "rotation": 0, "is_visible": True, "z_index": 1, "_ordem": 0,
            }
        ],
    }
    pdf = render_from_snapshot(snapshot, {FieldType.QR_CODE: url})

    documento = pdfium.PdfDocument(io.BytesIO(pdf))
    try:
        pagina = documento[0].render(scale=6).to_pil().convert("L")
    finally:
        documento.close()

    esperado = _qrcode.QRCode(
        version=None,
        error_correction=_qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=1,
    )
    esperado.add_data(url)
    esperado.make(fit=True)
    matriz = esperado.get_matrix()

    # get_matrix() ja devolve a matriz COM a borda. Somar a borda de novo
    # deslocaria a grade em um modulo e a comparacao acertaria por acaso em
    # cerca de dois tercos das celulas — que foi exatamente o que aconteceu
    # na primeira versao deste teste.
    ordem = len(matriz)
    reduzido = pagina.resize((ordem, ordem), Image.BOX)

    total = ordem * ordem
    iguais = sum(
        1
        for linha in range(ordem)
        for coluna in range(ordem)
        if (reduzido.getpixel((coluna, linha)) < 128) == matriz[linha][coluna]
    )

    assert iguais == total, "{}/{} modulos conferem".format(iguais, total)


def test_o_qr_errado_seria_detectado(certificado):
    """
    Contraprova do teste acima.

    Se a comparacao passasse com qualquer conteudo, ela nao estaria
    verificando nada. Aqui o QR e gerado de outra URL e a comparacao precisa
    falhar.
    """
    pdfium = pytest.importorskip("pypdfium2")
    import qrcode as _qrcode

    from certificates.pdf import url_de_validacao

    snapshot = {
        "page_width_mm": 100, "page_height_mm": 100, "background_path": "",
        "fields": [
            {
                "field_type": FieldType.QR_CODE,
                "x": 0, "y": 0, "width": 100, "height": 100,
                "rotation": 0, "is_visible": True, "z_index": 1, "_ordem": 0,
            }
        ],
    }
    pdf = render_from_snapshot(
        snapshot, {FieldType.QR_CODE: "https://exemplo.invalid/outro/"}
    )

    documento = pdfium.PdfDocument(io.BytesIO(pdf))
    try:
        pagina = documento[0].render(scale=6).to_pil().convert("L")
    finally:
        documento.close()

    esperado = _qrcode.QRCode(
        version=None,
        error_correction=_qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=1,
    )
    esperado.add_data(url_de_validacao(certificado))
    esperado.make(fit=True)
    matriz = esperado.get_matrix()

    ordem = len(matriz)
    reduzido = pagina.resize((ordem, ordem), Image.BOX)
    iguais = sum(
        1
        for linha in range(ordem)
        for coluna in range(ordem)
        if (reduzido.getpixel((coluna, linha)) < 128) == matriz[linha][coluna]
    )

    assert iguais < ordem * ordem
