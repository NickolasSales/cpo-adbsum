"""
Renderizador dirigido por modelo.

    arte de fundo
        +
    campos posicionados
        +
    QR Code
        =
    PDF

Este modulo nao sabe desenhar moldura, ornamento nem tipografia de titulo, e
essa e a mudanca inteira da Etapa 10. A estetica vem da imagem enviada pelo
administrador; o codigo acrescenta somente os valores que mudam de aluno para
aluno.

O que entra aqui
----------------
Um dicionario de configuracao — o "snapshot" — e um dicionario de valores. O
renderizador nunca recebe um objeto Certificate nem um CertificateTemplate:

    render_from_snapshot(snapshot, valores) -> bytes

Isso tem uma consequencia direta e proposital: o preview e o documento final
passam pelo MESMO caminho, com o mesmo sistema de coordenadas e a mesma
tipografia. Um preview em HTML que "parecesse" o PDF seria uma segunda
implementacao do layout, e as duas divergiriam no primeiro campo longo.

Coordenadas
-----------
O snapshot guarda percentuais com origem no canto superior esquerdo e y
crescendo para baixo, porque e assim que quem posiciona enxerga a pagina. O
PDF usa origem no canto inferior esquerdo. A conversao acontece em
`_caixa`, num lugar so.
"""

import io

import qrcode
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from certificates.fonts import (
    FONTES_PERMITIDAS,
    FonteIndisponivel,
    exigir_fonte,
    registrar_fontes,
    resolver_fonte,
)
from certificates.models.template import (
    CORES_ACEITAS,
    FieldType,
    TextAlign,
)

FONTE_PADRAO = "Helvetica"
COR_PADRAO = "#000000"

__all__ = ["FonteIndisponivel", "render_from_snapshot", "ajustar", "qr_para"]


# ---------------------------------------------------------------------------
# Conversoes
# ---------------------------------------------------------------------------


def _cor(texto):
    """
    #RRGGBB para a tripla 0..1 do ReportLab.

    Reconfere o formato mesmo vindo do snapshot. O snapshot foi gravado por
    um servico que valida, mas ele e JSON no banco: um UPDATE manual poderia
    ter posto qualquer coisa ali, e o renderizador nao deve ser o lugar onde
    isso vira exception no meio de uma emissao.
    """
    texto = (texto or "").strip()
    if not CORES_ACEITAS.match(texto):
        texto = COR_PADRAO
    return tuple(int(texto[i : i + 2], 16) / 255 for i in (1, 3, 5))


def _fonte(campo):
    """
    Nome de fonte do campo, a partir de familia + peso + italico.

    Funciona para as TRES geracoes de snapshot, e essa e a razao de a funcao
    existir em vez de o desenho ler os campos direto:

        Etapa 10   font_family = "Times-BoldItalic", sem peso e sem italico
        Etapa 11   font_family = "Times", bold = true
        agora      font_family = "MONTSERRAT", font_weight = 600

    `resolver_fonte` decompoe o nome composto, soma o que faltar e devolve o
    mesmo nome de sempre. Um documento antigo continua saindo com a fonte com
    que foi assinado — inclusive um emitido antes de estas fontes existirem.

    A conferencia contra FONTES_PERMITIDAS fica: o snapshot e JSON no banco, e
    um UPDATE manual poderia ter posto qualquer coisa ali.

    O que NAO acontece aqui e trocar a fonte em silencio quando o arquivo
    falta. `exigir_fonte` levanta, e quem chamou decide o que dizer. Um
    certificado impresso com outra tipografia que nao a configurada e um
    defeito que so aparece com o documento ja na mao de alguem.
    """
    nome = resolver_fonte(
        campo.get("font_family"),
        campo.get("font_weight"),
        bool(campo.get("italic")),
        # Snapshot da Etapa 11. Nao ha um segundo lugar guardando a mesma
        # verdade: o campo do banco tem so o peso, e isto e um tradutor de
        # dado antigo na fronteira.
        negrito=bool(campo.get("bold")),
    )
    if nome not in FONTES_PERMITIDAS:
        nome = FONTE_PADRAO
    return exigir_fonte(nome)


def _caixa(campo, largura_pt, altura_pt):
    """
    Percentual com origem em cima para pontos PDF com origem embaixo.

    Devolve (x, y_topo, largura, altura) em pontos, onde y_topo e a borda
    SUPERIOR da caixa medida no sistema do PDF. As funcoes de desenho descem
    a partir dali, que e como texto se comporta.
    """
    x = float(campo.get("x", 0)) / 100 * largura_pt
    largura = float(campo.get("width", 0)) / 100 * largura_pt
    altura = float(campo.get("height", 0)) / 100 * altura_pt
    # Aqui o eixo se inverte.
    y_topo = altura_pt - (float(campo.get("y", 0)) / 100 * altura_pt)
    return x, y_topo, largura, altura


# ---------------------------------------------------------------------------
# Ajuste de texto
#
# Herdado do desenho da Etapa 8, que ja resolvia o mesmo problema: nome
# proprio longo num campo estreito. A diferenca e que agora a caixa vem do
# modelo, e nao de constantes no codigo.
# ---------------------------------------------------------------------------


def _quebrar(texto, fonte, tamanho, largura):
    """
    Quebra por palavras, com corte por caractere quando uma palavra sozinha
    nao cabe.

    O corte por caractere quase nunca acontece com dado real. Ele existe para
    que uma sequencia colada sem espacos nao empurre tinta para fora da caixa.
    """
    linhas = []
    atual = ""
    for palavra in (texto or "").split():
        while stringWidth(palavra, fonte, tamanho) > largura and len(palavra) > 1:
            corte = len(palavra) - 1
            while corte > 1 and stringWidth(palavra[:corte], fonte, tamanho) > largura:
                corte -= 1
            if atual:
                linhas.append(atual)
                atual = ""
            linhas.append(palavra[:corte])
            palavra = palavra[corte:]

        tentativa = "{} {}".format(atual, palavra).strip()
        if atual and stringWidth(tentativa, fonte, tamanho) > largura:
            linhas.append(atual)
            atual = palavra
        else:
            atual = tentativa
    if atual:
        linhas.append(atual)
    return linhas or [""]


def _equilibrar(linhas, fonte, tamanho, largura):
    """
    Reparte duas linhas no ponto que deixa as duas mais parecidas.

    A quebra gulosa enche a primeira linha ate o limite e joga o resto na
    segunda: "Maria Aparecida dos Santos de Oliveira" em cima e "Rodrigues"
    embaixo. Num nome em destaque isso parece erro.
    """
    if len(linhas) != 2:
        return linhas

    palavras = " ".join(linhas).split()
    if len(palavras) < 2:
        return linhas

    melhor = None
    for corte in range(1, len(palavras)):
        alto = " ".join(palavras[:corte])
        baixo = " ".join(palavras[corte:])
        larguras = (
            stringWidth(alto, fonte, tamanho),
            stringWidth(baixo, fonte, tamanho),
        )
        if max(larguras) > largura:
            continue
        diferenca = abs(larguras[0] - larguras[1])
        if melhor is None or diferenca < melhor[0]:
            melhor = (diferenca, [alto, baixo])

    return melhor[1] if melhor else linhas


def _montar_linhas(texto, fonte, tamanho, largura, quebrar):
    """
    Linhas do texto num tamanho de fonte, respeitando as quebras escritas.

    As quebras explicitas vem primeiro: um texto personalizado escrito em
    tres linhas sai em tres linhas, e a quebra automatica age DENTRO de cada
    uma. Tratar o texto como um paragrafo unico juntaria as frases que o
    administrador separou de proposito.

    Com `quebrar` desligado nao ha quebra automatica — so encolhimento. Serve
    para uma linha que precisa caber inteira num espaco estreito da arte.
    """
    linhas = []
    for paragrafo in texto.split("\n"):
        if not paragrafo.strip():
            # Linha em branco escrita de proposito: vale como espaco
            # vertical, e nao some.
            linhas.append("")
            continue
        if quebrar:
            linhas.extend(_quebrar(paragrafo, fonte, tamanho, largura))
        else:
            linhas.append(paragrafo)
    return linhas or [""]


def _cabe_na_largura(linhas, fonte, tamanho, largura):
    return all(stringWidth(linha, fonte, tamanho) <= largura for linha in linhas)


def ajustar(
    texto,
    *,
    fonte,
    tamanho,
    minimo,
    auto_fit,
    largura,
    altura,
    entrelinha,
    quebrar=True,
):
    """
    Decide as linhas e o tamanho de fonte que cabem na caixa.

    Devolve (linhas, tamanho). A ordem das tentativas e:

        1  no tamanho pedido, quebrando o necessario
        2  se nao couber na ALTURA, encolhe um ponto e tenta de novo
        3  no tamanho minimo, aceita o numero de linhas que for preciso

    O passo 3 e uma escolha, e vale dizer qual: um nome que nao cabe sai em
    mais linhas, nunca cortado. "Maria Aparecida dos Santos de Oli..." num
    certificado e pior do que qualquer desalinho — e um documento oficial com
    o nome da pessoa errado.

    Sem auto_fit o tamanho e respeitado como pedido e so a quebra acontece.
    Quem desligou o ajuste quer a fonte daquele tamanho.

    Com `quebrar` desligado e auto_fit ligado, o criterio de parada passa a
    ser a LARGURA — nao ha quebra que resolva, so encolhimento. Se nem no
    minimo couber, o texto transborda a caixa, visivelmente. Transbordar e
    melhor que cortar: o desalinho aparece no preview e se corrige; um nome
    truncado passa despercebido ate estar impresso.
    """
    texto = "" if texto is None else str(texto)
    if not texto.strip():
        return [], tamanho

    tamanho = max(int(tamanho), 1)
    minimo = max(min(int(minimo), tamanho), 1)
    largura = max(largura, 1)

    def acabar(linhas, corpo):
        # O equilibrio so faz sentido num paragrafo unico partido em duas:
        # com quebras escritas, repartir palavras entre linhas desmontaria o
        # que o administrador escreveu.
        if "\n" in texto or not quebrar:
            return linhas, corpo
        return _equilibrar(linhas, fonte, corpo, largura), corpo

    if not auto_fit:
        return acabar(
            _montar_linhas(texto, fonte, tamanho, largura, quebrar), tamanho
        )

    atual = tamanho
    while atual >= minimo:
        linhas = _montar_linhas(texto, fonte, atual, largura, quebrar)
        alto = len(linhas) * atual * entrelinha
        if alto <= altura and (
            quebrar or _cabe_na_largura(linhas, fonte, atual, largura)
        ):
            return acabar(linhas, atual)
        atual -= 1

    return acabar(_montar_linhas(texto, fonte, minimo, largura, quebrar), minimo)


# ---------------------------------------------------------------------------
# QR Code
# ---------------------------------------------------------------------------


def qr_para(conteudo, borda=1):
    imagem = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=borda,
    )
    imagem.add_data(conteudo)
    imagem.make(fit=True)
    png = imagem.make_image(fill_color="black", back_color="white").convert("RGB")
    buffer = io.BytesIO()
    png.save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)


# ---------------------------------------------------------------------------
# Desenho
# ---------------------------------------------------------------------------


def _desenhar_fundo(c, snapshot, largura_pt, altura_pt):
    """
    A arte, cobrindo a pagina inteira.

    Desenhada antes de tudo e sem preservar proporcao: a caixa e a pagina. Se
    a arte tiver proporcao diferente, ela estica — e isso e visivel, que e o
    comportamento certo. Encaixar com barras brancas esconderia o problema
    num documento oficial, e a tela de upload ja avisa quando a proporcao nao
    bate.

    Fundo ausente nao interrompe a renderizacao. Um modelo ativo sempre tem
    arte (o banco garante), mas o preview de um rascunho pode nao ter, e ver
    os campos sobre o branco e util enquanto se posiciona.
    """
    caminho = snapshot.get("background_path")
    if not caminho:
        return
    try:
        c.drawImage(
            caminho,
            0,
            0,
            width=largura_pt,
            height=altura_pt,
            preserveAspectRatio=False,
            anchor="c",
            mask="auto",
        )
    except Exception:
        # Arquivo removido do disco, permissao, imagem ilegivel. O documento
        # sai sem fundo em vez de a emissao falhar: um certificado com os
        # dados certos e sem moldura ainda serve para validar, e a falha fica
        # visivel para quem abrir.
        return


def _quarto_de_volta(rotacao):
    """Se a rotacao troca a horizontal pela vertical."""
    return rotacao % 180 == 90


def _desenhar_texto(c, campo, texto, largura_pt, altura_pt):
    x, y_topo, largura, altura = _caixa(campo, largura_pt, altura_pt)
    fonte = _fonte(campo)
    entrelinha = float(campo.get("line_height") or 1.2)
    alinhamento = campo.get("text_align") or TextAlign.CENTER
    rotacao = int(campo.get("rotation") or 0)

    # Num campo girado um quarto de volta, o texto corre ao longo da ALTURA
    # da caixa na pagina. Quebrar pela largura produziria o defeito que o ano
    # vertical do modelo oficial expos na primeira amostra: uma caixa estreita
    # e alta partia "2026" em "20" e "26", lado a lado.
    #
    # A troca vale so no quarto de volta. Para um angulo qualquer nao existe
    # "a dimensao ao longo do texto", e girar 15 graus com a caixa trocada
    # surpreenderia quem so queria inclinar um pouco.
    if _quarto_de_volta(rotacao):
        largura_do_texto, altura_do_texto = altura, largura
    else:
        largura_do_texto, altura_do_texto = largura, altura

    linhas, tamanho = ajustar(
        texto,
        fonte=fonte,
        tamanho=int(campo.get("font_size") or 12),
        minimo=int(campo.get("min_font_size") or 8),
        auto_fit=bool(campo.get("auto_fit", True)),
        largura=largura_do_texto,
        altura=altura_do_texto,
        entrelinha=entrelinha,
        quebrar=bool(campo.get("wrap", True)),
    )
    if not linhas:
        return

    c.saveState()
    c.setFillColorRGB(*_cor(campo.get("text_color")))
    c.setFont(fonte, tamanho)

    if rotacao:
        # Gira em torno do centro da caixa. Girar pelo canto faria o campo
        # sair de onde foi posicionado, e quem arrastou para o lugar certo
        # nao esperaria isso. E o que permite o ano vertical do modelo
        # oficial: rotation=90 sem recalcular x e y.
        c.translate(x + largura / 2, y_topo - altura / 2)
        c.rotate(rotacao)
        c.translate(-largura_do_texto / 2, -altura_do_texto / 2)
        esquerda, base_topo = 0, altura_do_texto
    else:
        esquerda, base_topo = x, y_topo

    largura = largura_do_texto

    # A primeira linha comeca abaixo do topo pela altura da propria fonte,
    # senao ela sairia acima da caixa: em PDF a coordenada de texto e a linha
    # de base, e nao o topo do glifo.
    y = base_topo - tamanho
    for linha in linhas:
        if alinhamento == TextAlign.LEFT:
            c.drawString(esquerda, y, linha)
        elif alinhamento == TextAlign.RIGHT:
            c.drawRightString(esquerda + largura, y, linha)
        else:
            c.drawCentredString(esquerda + largura / 2, y, linha)
        y -= tamanho * entrelinha

    c.restoreState()


def _desenhar_imagem(c, campo, imagem, largura_pt, altura_pt):
    """
    QR Code ou imagem fixa, encaixados na caixa preservando a proporcao.

    Aqui a proporcao e preservada, ao contrario do fundo: um QR esticado
    deixa de ser lido, e uma assinatura achatada fica visivelmente errada.
    """
    if imagem is None:
        return

    x, y_topo, largura, altura = _caixa(campo, largura_pt, altura_pt)
    rotacao = int(campo.get("rotation") or 0)

    # Mesma troca do texto: a caixa desenhada e a que o campo ocupa NA
    # PAGINA, e num quarto de volta as dimensoes locais sao as invertidas.
    if _quarto_de_volta(rotacao):
        largura, altura = altura, largura

    c.saveState()
    if rotacao:
        c.translate(x + largura / 2, y_topo - altura / 2)
        c.rotate(rotacao)
        c.translate(-largura / 2, -altura / 2)
        destino = (0, 0)
    else:
        destino = (x, y_topo - altura)

    try:
        c.drawImage(
            imagem,
            destino[0],
            destino[1],
            width=largura,
            height=altura,
            preserveAspectRatio=True,
            anchor="c",
            mask="auto",
        )
    except Exception:
        pass
    c.restoreState()


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


def render_from_snapshot(snapshot, valores):
    """
    Bytes do PDF descrito por `snapshot`, preenchido com `valores`.

    `snapshot` e a configuracao congelada:

        {"page_width_mm", "page_height_mm", "background_path", "fields": [...]}

    `valores` e {field_type: texto}. QR_CODE espera a URL em
    valores["QR_CODE"]; STATIC_IMAGE traz o caminho no proprio campo;
    CUSTOM_TEXT traz a frase em `content`, com as variaveis resolvidas
    contra o mesmo `valores`.

    Campos sem valor sao ignorados em silencio. Um certificado da versao 1 nao
    tem carga horaria; imprimir um espaco em branco onde deveria haver "08
    horas" e melhor do que imprimir "None".
    """
    # Uma vez por documento, e nao por elemento desenhado: registrar de novo
    # a cada campo releria dezoito arquivos por pagina. Precisa vir ANTES do
    # primeiro `ajustar`, porque o auto-ajuste mede o texto com stringWidth,
    # e stringWidth so conhece as metricas de uma fonte ja registrada. Medir
    # com Helvetica e desenhar com Bodoni daria uma caixa certa para a fonte
    # errada.
    registrar_fontes()

    largura_pt = float(snapshot.get("page_width_mm") or 297) * mm
    altura_pt = float(snapshot.get("page_height_mm") or 210) * mm

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(largura_pt, altura_pt))
    c.setTitle(snapshot.get("title") or "Certificado")
    c.setAuthor(snapshot.get("author") or "")
    c.setSubject("Certificado de conclusão")
    c.setCreator(snapshot.get("creator") or "")

    _desenhar_fundo(c, snapshot, largura_pt, altura_pt)

    # z_index decide quem cobre quem; a ordem de insercao desempata, para que
    # dois campos com o mesmo z_index saiam sempre na mesma ordem.
    campos = sorted(
        (campo for campo in snapshot.get("fields", []) if campo.get("is_visible", True)),
        key=lambda campo: (int(campo.get("z_index") or 0), campo.get("_ordem", 0)),
    )

    for campo in campos:
        tipo = campo.get("field_type")

        if tipo == FieldType.QR_CODE:
            conteudo = valores.get(FieldType.QR_CODE)
            if conteudo:
                _desenhar_imagem(c, campo, qr_para(conteudo), largura_pt, altura_pt)
            continue

        if tipo == FieldType.STATIC_IMAGE:
            caminho = campo.get("image_path")
            if caminho:
                _desenhar_imagem(c, campo, caminho, largura_pt, altura_pt)
            continue

        if tipo == FieldType.CUSTOM_TEXT:
            # O texto vem do proprio campo; as variaveis sao resolvidas
            # agora, contra os mesmos valores que os campos soltos usam.
            # Import local para nao amarrar o renderizador a um modulo que
            # so importa por causa de um tipo entre quinze.
            from certificates.placeholders import aplicar

            texto = aplicar(campo.get("content") or "", valores)
        else:
            texto = valores.get(tipo)

        if texto is None or str(texto).strip() == "":
            continue
        _desenhar_texto(c, campo, str(texto), largura_pt, altura_pt)

    c.showPage()
    c.save()
    return buffer.getvalue()
