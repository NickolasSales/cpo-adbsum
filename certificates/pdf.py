"""
Desenho do certificado em PDF e o QR Code que aponta para a validacao.

Por que ReportLab, e nao um renderizador de HTML
------------------------------------------------
WeasyPrint e afins produzem um resultado bonito a partir de CSS, mas custam
Pango, Cairo e GDK-Pixbuf no servidor — biblioteca de sistema, nao wheel de
Python. Numa t3.small que hoje nao tem nada disso instalado, e num ambiente de
desenvolvimento Windows, isso significa dois caminhos de instalacao diferentes
e frageis para um documento de uma pagina com dez linhas de texto.

ReportLab desenha direto no PDF, instala por wheel nos dois sistemas, e as
fontes que usamos (Helvetica e Times) sao as Type 1 padrao embutidas no
formato: nenhum arquivo de fonte precisa existir no servidor.

Tudo aqui trata dado como texto. Nome de aluno nunca e interpretado como
marcacao, nem no PDF nem no nome do arquivo.
"""

import io
import re
import unicodedata

import qrcode
from django.conf import settings
from django.urls import reverse
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

LARGURA, ALTURA = landscape(A4)

# Margem interna util. O texto nunca escreve fora desta faixa.
MARGEM = 25 * mm
LARGURA_UTIL = LARGURA - 2 * MARGEM

PRETO = (0.08, 0.08, 0.10)
CINZA = (0.42, 0.42, 0.46)
DOURADO = (0.62, 0.48, 0.16)


# ---------------------------------------------------------------------------
# Endereco publico
# ---------------------------------------------------------------------------


def url_de_validacao(certificado):
    """
    Endereco absoluto da pagina publica de validacao.

    Montado com SITE_URL + reverse, nunca com string escrita a mao. Este e o
    valor que vai impresso no QR Code de um documento em papel: se ele apontar
    para um IP, para localhost ou para http, o certificado nasce quebrado e
    nao ha como corrigir o que ja foi impresso.
    """
    caminho = reverse(
        "certificates:validate",
        kwargs={"verification_code": str(certificado.verification_code)},
    )
    return "{}{}".format(settings.SITE_URL.rstrip("/"), caminho)


# ---------------------------------------------------------------------------
# Nome do arquivo
# ---------------------------------------------------------------------------

SEGUROS = re.compile(r"[^A-Za-z0-9]+")


def nome_de_arquivo_seguro(*partes):
    """
    Nome de arquivo montado por lista branca.

    O valor vai para o cabecalho Content-Disposition. Cabecalho HTTP e
    delimitado por CRLF, entao um nome contendo quebra de linha permitiria
    injetar cabecalhos na resposta. Filtrar caracteres proibidos e uma corrida
    que se perde; aqui so passa o que esta explicitamente permitido.
    """
    limpas = []
    for parte in partes:
        texto = unicodedata.normalize("NFKD", str(parte or ""))
        texto = texto.encode("ascii", "ignore").decode("ascii")
        texto = SEGUROS.sub("-", texto).strip("-").lower()
        if texto:
            limpas.append(texto[:60])
    if not limpas:
        limpas = ["certificado"]
    return "{}.pdf".format("-".join(limpas))


# ---------------------------------------------------------------------------
# Desenho
# ---------------------------------------------------------------------------


def _fonte_que_cabe(texto, fonte, tamanho_inicial, largura_maxima, minimo=10):
    """
    Maior tamanho de fonte em que o texto ainda cabe na largura dada.

    Nome proprio longo e comum, e um certificado com o nome cortado nao
    serve. Reduzir ate caber e melhor do que quebrar em duas linhas o campo
    que o documento apresenta como uma unica afirmacao.
    """
    tamanho = tamanho_inicial
    while tamanho > minimo and stringWidth(texto, fonte, tamanho) > largura_maxima:
        tamanho -= 1
    return tamanho


def _centralizado(c, texto, y, fonte, tamanho, cor=PRETO, largura_maxima=None):
    limite = largura_maxima or LARGURA_UTIL
    tamanho = _fonte_que_cabe(texto, fonte, tamanho, limite)
    c.setFillColorRGB(*cor)
    c.setFont(fonte, tamanho)
    c.drawCentredString(LARGURA / 2, y, texto)
    return tamanho


def _qr_code(conteudo, lado_px=600):
    imagem = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    imagem.add_data(conteudo)
    imagem.make(fit=True)
    png = imagem.make_image(fill_color="black", back_color="white").convert("RGB")
    buffer = io.BytesIO()
    png.save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)


def render_certificate_pdf(certificado):
    """
    Devolve os bytes do PDF de um certificado.

    Le exclusivamente os campos *_snapshot: o documento e o que foi emitido,
    nao o que o banco diz hoje. Nota nao aparece de proposito — o certificado
    atesta conclusao, e a nota pertence ao resultado academico.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(A4))
    c.setTitle("Certificado - {}".format(certificado.student_name_snapshot))
    c.setAuthor(certificado.institution_name_snapshot)
    c.setSubject("Certificado de conclusao")

    # Moldura discreta.
    c.setStrokeColorRGB(*DOURADO)
    c.setLineWidth(2)
    c.rect(12 * mm, 12 * mm, LARGURA - 24 * mm, ALTURA - 24 * mm)
    c.setLineWidth(0.5)
    c.rect(15 * mm, 15 * mm, LARGURA - 30 * mm, ALTURA - 30 * mm)

    _centralizado(c, "CERTIFICADO", ALTURA - 42 * mm, "Helvetica-Bold", 30, DOURADO)
    _centralizado(
        c,
        certificado.institution_name_snapshot,
        ALTURA - 55 * mm,
        "Helvetica",
        13,
        CINZA,
    )

    _centralizado(c, "Certificamos que", ALTURA - 76 * mm, "Times-Italic", 14, CINZA)
    _centralizado(
        c,
        certificado.student_name_snapshot,
        ALTURA - 92 * mm,
        "Times-Bold",
        28,
        PRETO,
        LARGURA_UTIL - 20 * mm,
    )

    _centralizado(c, "concluiu o modulo", ALTURA - 108 * mm, "Times-Italic", 14, CINZA)
    _centralizado(
        c,
        certificado.module_name_snapshot,
        ALTURA - 122 * mm,
        "Times-Bold",
        20,
        PRETO,
        LARGURA_UTIL - 20 * mm,
    )

    _centralizado(
        c,
        "Avaliacao: {}".format(certificado.exam_title_snapshot),
        ALTURA - 134 * mm,
        "Times-Roman",
        12,
        CINZA,
        LARGURA_UTIL - 20 * mm,
    )

    # --- rodape: data, codigo e QR ---------------------------------------
    emissao = certificado.issued_at.strftime("%d/%m/%Y")
    c.setFillColorRGB(*CINZA)
    c.setFont("Helvetica", 10)
    c.drawString(MARGEM, 34 * mm, "Emitido em {}".format(emissao))
    c.setFont("Helvetica", 8)
    c.drawString(MARGEM, 28 * mm, "Codigo de verificacao")
    c.setFillColorRGB(*PRETO)
    c.setFont("Courier", 10)
    c.drawString(MARGEM, 22 * mm, str(certificado.verification_code))

    endereco = url_de_validacao(certificado)
    lado = 30 * mm
    c.drawImage(
        _qr_code(endereco),
        LARGURA - MARGEM - lado,
        20 * mm,
        width=lado,
        height=lado,
        mask="auto",
    )
    c.setFillColorRGB(*CINZA)
    c.setFont("Helvetica", 7)
    c.drawRightString(
        LARGURA - MARGEM, 16 * mm, "Verifique a autenticidade em {}".format(endereco)
    )

    c.showPage()
    c.save()
    return buffer.getvalue()
