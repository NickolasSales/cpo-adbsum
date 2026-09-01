"""
Ornamentos vetoriais do certificado oficial.

Por que desenhar, e nao colar a imagem de referencia
----------------------------------------------------
O modelo institucional chegou como imagem, com os textos de um certificado ja
preenchido: um modulo especifico, uma data especifica, um ano especifico. Usar
o arquivo como fundo e escrever por cima produziria dois modulos, duas datas e
duas assinaturas no mesmo papel — e, no que sobrasse, um fundo rasterizado que
imprime borrado e pesa alguns megabytes por documento.

Entao o desenho e refeito aqui em curvas de Bezier. Sai vetorial: mesmo
resultado em tela e em papel, arquivo pequeno, e nenhum asset binario para
versionar.

O que este modulo NAO tenta ser
-------------------------------
Uma copia pixel a pixel. As formas sao uma releitura das que aparecem na
referencia — as ondas escuras nos cantos opostos, as fitas douradas cruzando
por cima, e a trama de linhas finas em verde acinzentado. A intencao e que o
documento seja reconhecivel como o mesmo modelo, nao que passe numa comparacao
de diferenca de imagem.

Coordenadas: tudo em milimetros, convertido por `mm` do ReportLab. A origem do
PDF fica no canto inferior esquerdo.
"""

from reportlab.lib.colors import Color
from reportlab.lib.units import mm

# Paleta lida da referencia. Escrita uma vez, aqui, porque cor repetida em
# quinze lugares e cor que muda em quatorze deles.
MARFIM = (0.980, 0.973, 0.957)
GRAFITE = (0.106, 0.098, 0.157)
GRAFITE_CLARO = (0.180, 0.169, 0.243)
OURO = (0.788, 0.612, 0.204)
OURO_CLARO = (0.929, 0.749, 0.365)
AMBAR = (0.851, 0.541, 0.184)
SALVIA = (0.725, 0.780, 0.737)
VERDE_ESCURO = (0.043, 0.267, 0.184)
PRETO_TEXTO = (0.098, 0.098, 0.118)
CINZA_TEXTO = (0.380, 0.380, 0.420)


def _rgb(cor):
    """Converte a tupla 0..1 usada aqui no objeto de cor do ReportLab."""
    return Color(*cor)


def _curva(c, pontos):
    """
    Caminho fechado a partir de (inicio, [(c1, c2, fim), ...]).

    Cada tupla e um segmento de Bezier cubico com os dois pontos de controle e
    o ponto final, na ordem em que o ReportLab os espera.
    """
    inicio, segmentos = pontos
    caminho = c.beginPath()
    caminho.moveTo(inicio[0] * mm, inicio[1] * mm)
    for (c1x, c1y), (c2x, c2y), (fx, fy) in segmentos:
        caminho.curveTo(
            c1x * mm, c1y * mm, c2x * mm, c2y * mm, fx * mm, fy * mm
        )
    caminho.close()
    return caminho


def _preencher(c, pontos, cor):
    c.setFillColorRGB(*cor)
    c.drawPath(_curva(c, pontos), stroke=0, fill=1)


def _preencher_com_gradiente(c, pontos, cores, eixo):
    """
    Preenche a forma com um degrade linear.

    linearGradient() do ReportLab pinta a area de recorte inteira, entao a
    forma entra como clip e o degrade cai dentro dela. E o mesmo mecanismo de
    sombreamento do proprio formato PDF: continua vetorial, e as fitas douradas
    ganham o volume que elas tem na referencia sem virar imagem.
    """
    (x0, y0), (x1, y1) = eixo
    c.saveState()
    c.clipPath(_curva(c, pontos), stroke=0, fill=0)
    c.linearGradient(x0 * mm, y0 * mm, x1 * mm, y1 * mm, cores)
    c.restoreState()


# ---------------------------------------------------------------------------
# Fundo
# ---------------------------------------------------------------------------


def fundo(c, largura, altura):
    """Marfim em toda a pagina, antes de qualquer outra coisa."""
    c.setFillColorRGB(*MARFIM)
    c.rect(0, 0, largura, altura, stroke=0, fill=1)


# ---------------------------------------------------------------------------
# Trama de linhas finas
# ---------------------------------------------------------------------------


def _feixe(c, base, quantidade, passo, cor=SALVIA, espessura=0.35):
    """
    Familia de curvas paralelas deslocadas.

    Cada linha e a anterior empurrada por `passo`. O efeito de moire suave da
    referencia vem da quantidade — vinte e poucas linhas finas —, e nao de
    cada uma delas.
    """
    c.setStrokeColorRGB(*cor)
    c.setLineWidth(espessura)
    inicio, segmentos = base
    for i in range(quantidade):
        deslocamento = i * passo
        caminho = c.beginPath()
        caminho.moveTo((inicio[0] + deslocamento) * mm, inicio[1] * mm)
        for (c1x, c1y), (c2x, c2y), (fx, fy) in segmentos:
            caminho.curveTo(
                (c1x + deslocamento) * mm,
                c1y * mm,
                (c2x + deslocamento) * mm,
                c2y * mm,
                (fx + deslocamento) * mm,
                fy * mm,
            )
        c.drawPath(caminho, stroke=1, fill=0)


def trama_esquerda(c):
    """Linhas finas subindo pela borda esquerda, como na referencia."""
    base = (
        (-6, 8),
        [
            ((10, 40), (-2, 78), (14, 118)),
            ((24, 148), (10, 176), (26, 204)),
        ],
    )
    _feixe(c, base, quantidade=26, passo=1.35)


def trama_inferior_direita(c):
    """Contraponto no canto oposto, mais aberto e mais curto."""
    base = (
        (196, -4),
        [
            ((226, 10), (250, 2), (272, 26)),
            ((288, 42), (300, 34), (312, 52)),
        ],
    )
    _feixe(c, base, quantidade=22, passo=1.6)


# ---------------------------------------------------------------------------
# Ondas escuras e fitas douradas
# ---------------------------------------------------------------------------


def canto_inferior_esquerdo(c):
    """
    O bloco mais pesado do modelo: onda grafite com fitas douradas por cima.

    Desenhado em camadas, de tras para frente — grafite escuro, grafite claro,
    depois as duas fitas. A ordem importa: o dourado precisa cruzar por cima do
    escuro, e nao ao contrario.
    """
    _preencher(
        c,
        (
            (-2, -2),
            [
                ((-2, 34), (16, 52), (46, 58)),
                ((74, 64), (96, 52), (112, 30)),
                ((122, 16), (126, 6), (128, -2)),
                ((90, -2), (40, -2), (-2, -2)),
            ],
        ),
        GRAFITE,
    )
    _preencher(
        c,
        (
            (-2, -2),
            [
                ((-2, 20), (12, 36), (38, 42)),
                ((62, 47), (82, 36), (96, 18)),
                ((103, 9), (106, 3), (108, -2)),
                ((72, -2), (34, -2), (-2, -2)),
            ],
        ),
        GRAFITE_CLARO,
    )

    fita_larga = (
        (-2, 4),
        [
            ((26, 30), (58, 44), (96, 40)),
            ((124, 37), (146, 24), (162, 4)),
            ((150, 0), (140, -2), (134, -2)),
            ((96, 28), (44, 26), (-2, -6)),
        ],
    )
    _preencher_com_gradiente(
        c, fita_larga, [_rgb(AMBAR), _rgb(OURO_CLARO), _rgb(OURO)], ((-2, 0), (162, 44))
    )

    fita_fina = (
        (-2, 16),
        [
            ((22, 40), (54, 54), (88, 52)),
            ((112, 50), (132, 40), (146, 24)),
            ((142, 20), (139, 18), (136, 16)),
            ((92, 48), (40, 42), (-2, 12)),
        ],
    )
    _preencher_com_gradiente(
        c, fita_fina, [_rgb(OURO_CLARO), _rgb(AMBAR)], ((-2, 12), (146, 52))
    )


def canto_superior_direito(c, largura_mm=297, altura_mm=210):
    """
    Espelho reduzido do canto oposto, no alto a direita.

    Menor de proposito: na referencia o peso visual esta embaixo a esquerda, e
    repetir a mesma massa nos dois cantos deixaria o documento pesado nas duas
    pontas e vazio no meio.
    """
    l, a = largura_mm, altura_mm

    _preencher(
        c,
        (
            (l + 2, a + 2),
            [
                ((l + 2, a - 26), (l - 14, a - 42), (l - 40, a - 46)),
                ((l - 62, a - 49), (l - 78, a - 38), (l - 90, a - 20)),
                ((l - 96, a - 10), (l - 98, a - 4), (l - 99, a + 2)),
                ((l - 66, a + 2), (l - 30, a + 2), (l + 2, a + 2)),
            ],
        ),
        GRAFITE,
    )
    _preencher(
        c,
        (
            (l + 2, a + 2),
            [
                ((l + 2, a - 15), (l - 10, a - 28), (l - 30, a - 32)),
                ((l - 48, a - 35), (l - 62, a - 26), (l - 72, a - 12)),
                ((l - 77, a - 5), (l - 79, a - 1), (l - 80, a + 2)),
                ((l - 54, a + 2), (l - 26, a + 2), (l + 2, a + 2)),
            ],
        ),
        GRAFITE_CLARO,
    )

    fita = (
        (l + 2, a - 4),
        [
            ((l - 20, a - 24), (l - 46, a - 35), (l - 74, a - 32)),
            ((l - 94, a - 30), (l - 110, a - 20), (l - 122, a - 5)),
            ((l - 113, a - 2), (l - 106, a - 1), (l - 102, a - 1)),
            ((l - 74, a - 22), (l - 34, a - 20), (l + 2, a + 4)),
        ],
    )
    _preencher_com_gradiente(
        c,
        fita,
        [_rgb(OURO), _rgb(OURO_CLARO), _rgb(AMBAR)],
        ((l - 122, a - 35), (l + 2, a + 2)),
    )


# ---------------------------------------------------------------------------
# Moldura
# ---------------------------------------------------------------------------


def moldura(c, largura, altura):
    """
    Moldura dupla dourada.

    Duas linhas de espessuras diferentes a tres milimetros uma da outra. E o
    elemento que amarra a composicao: sem ela os ornamentos dos cantos ficam
    soltos e o texto parece flutuar no meio da folha.
    """
    c.setStrokeColorRGB(*OURO)
    c.setLineWidth(1.6)
    c.rect(14 * mm, 14 * mm, largura - 28 * mm, altura - 28 * mm, stroke=1, fill=0)
    c.setLineWidth(0.45)
    c.rect(17 * mm, 17 * mm, largura - 34 * mm, altura - 34 * mm, stroke=1, fill=0)


def divisor(c, centro_x, y, meia_largura):
    """
    Regua horizontal com um ponto cheio em cada ponta.

    Na referencia ela separa a identificacao do modulo do texto de conclusao,
    e e onde o nome do aluno se apoia.
    """
    c.setStrokeColorRGB(*PRETO_TEXTO)
    c.setFillColorRGB(*PRETO_TEXTO)
    c.setLineWidth(1.1)
    c.line(centro_x - meia_largura, y, centro_x + meia_largura, y)
    raio = 1.6 * mm
    c.circle(centro_x - meia_largura, y, raio, stroke=0, fill=1)
    c.circle(centro_x + meia_largura, y, raio, stroke=0, fill=1)
