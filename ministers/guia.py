"""
Guia da mensalidade em PDF (Fase 2D).

    MinisterFeeCharge (snapshot) -> render_guia() -> bytes do PDF

Le SO a cobranca: nome, matriculas, congregacao, cargo, valor, vencimento e
Pix vem dos campos *_snapshot e de pix_payload. Nada aqui consulta o
cadastro atual do ministro, a configuracao de pagamento ou a mensalidade —
por isso a guia de Outubro reconstruida daqui a um ano e a mesma guia.

Desenho
-------
Segue a guia usada pela secretaria ("Mensalidade_Ministro_Outubro_2026.pdf"):
papel carta, cabecalho e rodape do papel timbrado da AD Bras Sumare, titulo,
referencia, os cinco campos do ministro, a faixa do valor, o QR Code e o Pix
Copia e Cola. Duas adicoes: o VENCIMENTO, dentro da faixa do valor, e as
orientacoes de pagamento da configuracao, abaixo do Copia e Cola.

Cabecalho e rodape sao recortes do papel timbrado, em static/branding/. Para
atualizar endereco, telefone ou diretoria, troca-se o arquivo — o codigo nao
tem nenhum desses textos.

Renderer proprio e simples, de proposito: a guia e documento operacional, e
nao divide nada com o editor visual de certificados.

Fontes Type 1 padrao do PDF (Helvetica e Courier), como a guia original:
nenhum arquivo de fonte no servidor. Saida invariante (sem data de criacao
no arquivo): a mesma cobranca gera sempre os mesmos bytes.

Uma pagina, sempre. O espaco que sobra entre a faixa do valor e o rodape
define o tamanho do QR, entre um minimo e o tamanho da guia original;
orientacoes longas encolhem o QR, nunca empurram nada para uma segunda
pagina.
"""

import io

from django.conf import settings
from django.http import HttpResponse
from django.utils.cache import add_never_cache_headers, patch_cache_control
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from ministers import pix
from ministers.cobranca import nome_do_mes
from ministers.templatetags.financeiro import brl

LARGURA, ALTURA = letter
CENTRO_X = LARGURA / 2

# Medidas tiradas da guia original (pontos, origem no canto SUPERIOR).
MARGEM_X = 85
LARGURA_UTIL = LARGURA - 2 * MARGEM_X
LINHA_X0, LINHA_X1 = 80, 532

CABECALHO = settings.BASE_DIR / "static" / "branding" / "guia-cabecalho.png"
RODAPE = settings.BASE_DIR / "static" / "branding" / "guia-rodape.png"
# Os recortes estao a 180 dpi, como o papel timbrado: 2,5 px por ponto.
PX_POR_PONTO = 2.5
CABECALHO_X, CABECALHO_TOPO = 64, 29.6
RODAPE_X, RODAPE_TOPO = 67.2, 720

LIMITE_DO_CONTEUDO = 714  # o conteudo nunca desce alem disto (rodape)
QR_MAXIMO = 148  # o tamanho da guia original
QR_MINIMO = 110

TITULO = HexColor("#1f2a37")
TEXTO = HexColor("#111111")
ROTULO = HexColor("#4b5563")
SUAVE = HexColor("#6b7280")
LINHA = HexColor("#d1d5db")
FAIXA = HexColor("#eef0f3")


class _Pagina:
    """Canvas com o y medido de CIMA para baixo, como na guia de papel."""

    def __init__(self, c):
        self.c = c

    def texto(self, x, topo, conteudo, fonte, tamanho, cor=TEXTO, alinhamento="esquerda"):
        self.c.setFont(fonte, tamanho)
        self.c.setFillColor(cor)
        y = ALTURA - topo
        if alinhamento == "centro":
            self.c.drawCentredString(x, y, conteudo)
        elif alinhamento == "direita":
            self.c.drawRightString(x, y, conteudo)
        else:
            self.c.drawString(x, y, conteudo)

    def imagem(self, caminho, x, topo):
        leitor = ImageReader(str(caminho))
        largura_px, altura_px = leitor.getSize()
        largura, altura = largura_px / PX_POR_PONTO, altura_px / PX_POR_PONTO
        self.c.drawImage(leitor, x, ALTURA - topo - altura, width=largura, height=altura)


def _quebrar(texto, fonte, tamanho, largura):
    """Quebra por palavra; palavra maior que a linha e cortada por caractere."""
    linhas, atual = [], ""
    for palavra in texto.split():
        candidata = "{} {}".format(atual, palavra).strip()
        if stringWidth(candidata, fonte, tamanho) <= largura:
            atual = candidata
            continue
        if atual:
            linhas.append(atual)
        while stringWidth(palavra, fonte, tamanho) > largura:
            corte = len(palavra)
            while corte > 1 and stringWidth(palavra[:corte], fonte, tamanho) > largura:
                corte -= 1
            linhas.append(palavra[:corte])
            palavra = palavra[corte:]
        atual = palavra
    if atual:
        linhas.append(atual)
    return linhas


def _cabe(texto, fonte, tamanho, largura, minimo):
    while tamanho > minimo and stringWidth(texto, fonte, tamanho) > largura:
        tamanho -= 0.5
    return tamanho


def _pedacos(texto, fonte, tamanho, largura):
    """O payload em linhas de largura fixa. Nenhum caractere some ou muda."""
    por_linha = max(1, int(largura // stringWidth("0", fonte, tamanho)))
    return [texto[i : i + por_linha] for i in range(0, len(texto), por_linha)]


def nome_do_arquivo(cobranca):
    """mensalidade-outubro-2026.pdf. Sem nome do ministro, sem acento."""
    mes = pix.texto_do_br_code(nome_do_mes(cobranca.reference_month_snapshot)).lower()
    return "mensalidade-{}-{}.pdf".format(mes, cobranca.reference_month_snapshot.year)


def render_guia(cobranca):
    """Os bytes do PDF da guia. Uma pagina."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter, invariant=1, pageCompression=1)
    competencia = cobranca.reference_month_snapshot
    referencia = "{} DE {}".format(nome_do_mes(competencia).upper(), competencia.year)
    c.setTitle("Mensalidade de Ministro - {}".format(referencia.title()))
    c.setAuthor(settings.INSTITUTION_NAME)
    c.setSubject("Guia de mensalidade")
    c.setCreator(settings.APP_NAME)

    p = _Pagina(c)
    p.imagem(CABECALHO, CABECALHO_X, CABECALHO_TOPO)

    p.texto(CENTRO_X, 132, "MENSALIDADE DE MINISTRO", "Helvetica-Bold", 16, TITULO, "centro")
    p.texto(CENTRO_X, 152, "REFERÊNCIA: {}".format(referencia), "Helvetica", 10, ROTULO, "centro")
    c.setStrokeColor(LINHA)
    c.setLineWidth(1)
    c.line(LINHA_X0, ALTURA - 165, LINHA_X1, ALTURA - 165)

    # Campos do ministro. Nome longo encolhe e, no limite, quebra em duas
    # linhas; os demais sao curtos por natureza e so encolhem.
    topo = 186
    campos = (
        ("NOME DO MINISTRO", cobranca.minister_name_snapshot),
        ("MATRÍCULA CAMPO", cobranca.field_registration_snapshot or "-"),
        ("MATRÍCULA CONAMAD", cobranca.conamad_registration_snapshot),
        ("CONGREGAÇÃO", cobranca.congregation_snapshot or "-"),
        ("CARGO", cobranca.position_snapshot or "-"),
    )
    for rotulo, valor in campos:
        p.texto(MARGEM_X, topo, rotulo, "Helvetica", 9, ROTULO)
        tamanho = _cabe(valor, "Helvetica-Bold", 11, LARGURA_UTIL, 8)
        linhas = _quebrar(valor, "Helvetica-Bold", tamanho, LARGURA_UTIL)[:2]
        for i, linha in enumerate(linhas):
            p.texto(MARGEM_X, topo + 15 + i * (tamanho + 2), linha, "Helvetica-Bold", tamanho)
        topo += 41 + (len(linhas) - 1) * (tamanho + 2)

    # Faixa do valor, com o vencimento.
    faixa_topo, faixa_altura = topo + 4, 50
    c.setFillColor(FAIXA)
    c.roundRect(82, ALTURA - faixa_topo - faixa_altura, 448, faixa_altura, 6, stroke=0, fill=1)
    p.texto(98, faixa_topo + 21, "VALOR DA MENSALIDADE", "Helvetica-Bold", 11, TITULO)
    p.texto(
        98,
        faixa_topo + 37,
        "VENCIMENTO: {}".format(cobranca.due_date_snapshot.strftime("%d/%m/%Y")),
        "Helvetica",
        9.5,
        ROTULO,
    )
    p.texto(514, faixa_topo + 32, brl(cobranca.amount_snapshot), "Helvetica-Bold", 18, TITULO, "direita")
    topo = faixa_topo + faixa_altura

    # Do QR para baixo: mede tudo, decide o tamanho do QR, depois desenha.
    fonte_pix, tamanho_pix = "Courier", 7
    linhas_pix = _pedacos(cobranca.pix_payload, fonte_pix, tamanho_pix, LARGURA_UTIL)
    orientacoes = []
    for paragrafo in (cobranca.payment_instructions_snapshot or "").splitlines():
        orientacoes += _quebrar(paragrafo, "Helvetica", 8, LARGURA_UTIL)
    qr_topo = topo + 25
    fixo = 30 + 9 * len(linhas_pix)
    # Orientacoes cabem enquanto o QR nao ficar menor que o minimo. A
    # validacao (300 caracteres) faz isto nunca cortar na pratica; o corte
    # existe para que nem um nome de duas linhas com orientacoes longas
    # empurre a guia para uma segunda pagina.
    sobra = LIMITE_DO_CONTEUDO - qr_topo - QR_MINIMO - fixo - 6
    orientacoes = orientacoes[: max(0, int(sobra // 10))]
    abaixo_do_qr = fixo + (6 + 10 * len(orientacoes) if orientacoes else 0)
    qr = max(QR_MINIMO, min(QR_MAXIMO, LIMITE_DO_CONTEUDO - qr_topo - abaixo_do_qr))

    p.texto(CENTRO_X, topo + 18, "PAGUE COM PIX", "Helvetica-Bold", 10, TITULO, "centro")
    matriz = pix.matriz_qr(cobranca.pix_payload)
    modulo = qr / len(matriz)
    x0 = CENTRO_X - qr / 2
    c.setFillColor(HexColor("#000000"))
    for i, linha in enumerate(matriz):
        y = ALTURA - qr_topo - (i + 1) * modulo
        j = 0
        while j < len(linha):
            if linha[j]:
                inicio = j
                while j < len(linha) and linha[j]:
                    j += 1
                c.rect(x0 + inicio * modulo, y, (j - inicio) * modulo, modulo, stroke=0, fill=1)
            else:
                j += 1
    topo = qr_topo + qr

    p.texto(
        CENTRO_X,
        topo + 14,
        "Aponte a câmera do banco para o QR Code ou utilize o Pix Copia e Cola.",
        "Helvetica",
        8.5,
        TEXTO,
        "centro",
    )
    topo += 30
    p.texto(MARGEM_X, topo, "PIX COPIA E COLA:", "Helvetica-Bold", 8, ROTULO)
    for linha in linhas_pix:
        topo += 9
        p.texto(MARGEM_X, topo, linha, fonte_pix, tamanho_pix, TEXTO)
    if orientacoes:
        topo += 6
        for linha in orientacoes:
            topo += 10
            p.texto(MARGEM_X, topo, linha, "Helvetica", 8, SUAVE)

    p.imagem(RODAPE, RODAPE_X, RODAPE_TOPO)
    c.showPage()
    c.save()
    return buffer.getvalue()


def resposta_da_guia(cobranca):
    """
    O PDF como anexo, com cabecalhos de documento pessoal.

    Nome do arquivo por lista branca (mes e ano, sem nome de pessoa): nada
    que venha do cadastro chega ao cabecalho Content-Disposition. Sem cache
    em lugar nenhum — nem no navegador, nem em proxy — como as telas do
    portal.
    """
    pdf = render_guia(cobranca)
    resposta = HttpResponse(pdf, content_type="application/pdf")
    resposta["Content-Disposition"] = 'attachment; filename="{}"'.format(nome_do_arquivo(cobranca))
    resposta["Content-Length"] = str(len(pdf))
    add_never_cache_headers(resposta)
    patch_cache_control(resposta, private=True)
    return resposta
