"""
Desenho do certificado em PDF e o QR Code que aponta para a validacao.

Por que ReportLab, e nao um renderizador de HTML
------------------------------------------------
WeasyPrint e afins produzem um resultado bonito a partir de CSS, mas custam
Pango, Cairo e GDK-Pixbuf no servidor — biblioteca de sistema, nao wheel de
Python. Numa t3.small que hoje nao tem nada disso instalado, e num ambiente de
desenvolvimento Windows, isso significa dois caminhos de instalacao diferentes
e frageis para um documento de uma pagina.

ReportLab desenha direto no PDF, instala por wheel nos dois sistemas, e as
fontes que usamos (Helvetica, Times e Courier) sao as Type 1 padrao embutidas
no formato: nenhum arquivo de fonte precisa existir no servidor. Tudo que sai
impresso e vetor ou texto de verdade — nada de captura de tela esticada.

Duas versoes de desenho, e por que as duas continuam existindo
--------------------------------------------------------------
    versao 1   layout provisorio da Etapa 6
    versao 2   modelo oficial da AD Bras Sumare (Etapa 8)

Um certificado guarda a versao com que foi emitido e continua sendo desenhado
por ela. Nao e nostalgia: a versao 2 imprime data do curso, local, carga
horaria e ano, e nenhum desses campos existia quando os certificados da versao
1 foram gravados. Redesenha-los com o modelo novo produziria um documento
oficial com lacunas — ou, pior, com valores inventados na hora da renderizacao.

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

from certificates import ornamentos

LARGURA, ALTURA = landscape(A4)
CENTRO_X = LARGURA / 2

# Margem interna util. O texto nunca escreve fora desta faixa.
MARGEM = 25 * mm
LARGURA_UTIL = LARGURA - 2 * MARGEM

# Faixa reservada ao texto centralizado no modelo oficial. Mais estreita que a
# pagina porque o ano vertical ocupa a coluna da direita.
LARGURA_TEXTO = 185 * mm

PRETO = ornamentos.PRETO_TEXTO
CINZA = ornamentos.CINZA_TEXTO
DOURADO = ornamentos.OURO

# Um arquivo de logo institucional em alta resolucao, se algum dia existir,
# entra por aqui e substitui o desenho textual sem alteracao de codigo.
CAMINHO_DO_LOGO = settings.BASE_DIR / "static" / "img" / "certificado-logo.png"


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


def host_publico():
    """Somente o host de SITE_URL, para imprimir ao lado do QR."""
    return settings.SITE_URL.split("://")[-1].strip("/")


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
# Ajuste de texto
# ---------------------------------------------------------------------------


def _fonte_que_cabe(texto, fonte, tamanho_inicial, largura_maxima, minimo=10):
    """
    Maior tamanho de fonte em que o texto ainda cabe na largura dada.

    Nome proprio longo e comum, e um certificado com o nome cortado nao serve.
    """
    tamanho = tamanho_inicial
    while tamanho > minimo and stringWidth(texto, fonte, tamanho) > largura_maxima:
        tamanho -= 1
    return tamanho


def _quebrar(texto, fonte, tamanho, largura):
    """
    Quebra por palavras, com corte por caractere quando uma palavra sozinha
    nao cabe.

    O corte por caractere quase nunca acontece com dado real — nome e modulo
    tem limite de 150 caracteres no banco. Ele existe para que uma sequencia
    colada sem espacos nao empurre tinta para fora da moldura.
    """
    linhas = []
    atual = ""
    for palavra in (texto or "").split():
        while stringWidth(palavra, fonte, tamanho) > largura and len(palavra) > 1:
            corte = len(palavra) - 1
            while corte > 1 and stringWidth(
                palavra[:corte], fonte, tamanho
            ) > largura:
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
    segunda: "Maria Aparecida dos Santos de Oliveira" em cima e "Montenegro
    Rodrigues" embaixo. Num nome proprio em destaque isso parece erro. O ponto
    de corte mais equilibrado custa uma varredura sobre as palavras.
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


def _bloco(texto, fonte, tamanho, largura, *, max_linhas=1, minimo=8):
    """
    Encolhe a fonte ate o texto caber em `max_linhas` linhas.

    Devolve (linhas, tamanho). Primeiro tenta o tamanho pedido; a cada ponto
    perdido reavalia. Encolher e melhor que quebrar quando cabe uma linha so:
    o nome do aluno e apresentado no documento como uma unica afirmacao, e
    parti-lo em duas o transforma em duas.
    """
    while tamanho > minimo:
        linhas = _quebrar(texto, fonte, tamanho, largura)
        if len(linhas) <= max_linhas:
            return _equilibrar(linhas, fonte, tamanho, largura), tamanho
        tamanho -= 1
    linhas = _quebrar(texto, fonte, minimo, largura)
    if len(linhas) > max_linhas:
        # Guarda de ultimo recurso. Com os limites de coluna atuais (150
        # caracteres) este caminho e inalcancavel; se um dia alguem aumentar
        # os limites, o documento perde texto de forma visivel em vez de
        # invadir a moldura em silencio.
        linhas = linhas[:max_linhas]
        linhas[-1] = linhas[-1] + "…"
    return linhas, minimo


def _centralizado(c, texto, y, fonte, tamanho, cor=PRETO, largura_maxima=None):
    limite = largura_maxima or LARGURA_UTIL
    tamanho = _fonte_que_cabe(texto, fonte, tamanho, limite)
    c.setFillColorRGB(*cor)
    c.setFont(fonte, tamanho)
    c.drawCentredString(CENTRO_X, y, texto)
    return tamanho


def _bloco_centralizado(
    c, texto, y, fonte, tamanho, cor, largura, *, max_linhas=1, entrelinha=1.35,
    minimo=8,
):
    """Desenha um bloco centralizado e devolve o y da ultima linha."""
    linhas, tamanho = _bloco(
        texto, fonte, tamanho, largura, max_linhas=max_linhas, minimo=minimo
    )
    c.setFillColorRGB(*cor)
    c.setFont(fonte, tamanho)
    atual = y
    for linha in linhas:
        c.drawCentredString(CENTRO_X, atual, linha)
        atual -= tamanho * entrelinha
    return atual + tamanho * entrelinha


def _espacado(c, texto, y, fonte, tamanho, cor, espaco, x=None, centro=None):
    """
    Texto com espacamento extra entre letras.

    O espacamento de caracteres do PDF (operador Tc) vive no objeto de texto, e
    nao no canvas — `canvas.setCharSpace` nao existe. Por isso o desenho passa
    por beginText/drawText em vez de drawString.

    Sem `centro`, escreve a partir de `x`. Com `centro`, centraliza ali: a
    largura precisa ser calculada a mao, porque stringWidth nao conhece o Tc.

    O zero no fim nao e enfeite. Tc pertence ao estado de texto do PDF e
    sobrevive ao fim do objeto: sem zerar, TODO texto desenhado depois sai
    espacado, inclusive o desenhado por drawString — que nem sabe que o
    espacamento existe. Foi exatamente assim que a primeira amostra saiu com o
    documento inteiro esparramado para fora da moldura.
    """
    espacada = stringWidth(texto, fonte, tamanho) + espaco * max(len(texto) - 1, 0)
    inicio = x if x is not None else (centro if centro is not None else CENTRO_X) - espacada / 2

    c.setFillColorRGB(*cor)
    objeto = c.beginText(inicio, y)
    objeto.setFont(fonte, tamanho)
    objeto.setCharSpace(espaco)
    objeto.textOut(texto)
    objeto.setCharSpace(0)
    c.drawText(objeto)
    return espacada


# ---------------------------------------------------------------------------
# QR Code
# ---------------------------------------------------------------------------


def _qr_code(conteudo, borda=1):
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
# Versao 1 — layout provisorio da Etapa 6
# ---------------------------------------------------------------------------


def _desenhar_v1(c, certificado):
    """
    Desenho original, mantido para os certificados emitidos com ele.

    Nao recebe melhorias: e o retrato de um documento ja entregue.
    """
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
        _qr_code(endereco, borda=2),
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


# ---------------------------------------------------------------------------
# Versao 2 — modelo oficial
# ---------------------------------------------------------------------------


def _texto_de_conclusao(certificado):
    """
    A frase central do documento, montada a partir dos snapshots.

    Nada aqui e escrito a mao: modulo, datas, local e carga horaria vem do que
    foi congelado na emissao. Reler do Module faria a frase mudar quando a
    secretaria corrigisse um dado meses depois — num documento ja assinado.
    """
    horas = certificado.workload_hours_snapshot
    if horas is None:
        carga = ""
    elif horas == 1:
        carga = ", com carga horária de 1 hora"
    else:
        carga = ", com carga horária de {:02d} horas".format(horas)

    partes = ["Concluiu com êxito o {}".format(certificado.course_name_snapshot)]
    if certificado.modulo_impresso:
        partes.append(", {}".format(certificado.modulo_impresso))
    if certificado.course_dates_snapshot:
        partes.append(", realizado em {}".format(certificado.course_dates_snapshot))
    if certificado.course_location_snapshot:
        partes.append(", em {}".format(certificado.course_location_snapshot))
    partes.append(carga)
    return "{}.".format("".join(partes))


def _logo(c, x, y):
    """
    Identidade no alto a esquerda.

    Se um arquivo oficial existir em static/img/certificado-logo.png, ele e
    usado. Nao existindo, o lugar recebe uma composicao tipografica com as
    fontes padrao — deliberadamente, e nao um recorte de baixa resolucao
    extraido da imagem de referencia: um logo pixelado num documento oficial e
    pior do que nenhum logo.
    """
    if CAMINHO_DO_LOGO.exists():
        try:
            c.drawImage(
                str(CAMINHO_DO_LOGO),
                x,
                y - 4 * mm,
                width=34 * mm,
                height=18 * mm,
                preserveAspectRatio=True,
                anchor="sw",
                mask="auto",
            )
            return
        except Exception:
            # Arquivo corrompido ou em formato inesperado nao pode impedir a
            # emissao de um certificado. Cai no desenho textual.
            pass

    c.setFillColorRGB(*PRETO)
    c.setFont("Times-BoldItalic", 25)
    c.drawString(x, y + 3 * mm, "AD")
    largura_ad = stringWidth("AD", "Times-BoldItalic", 25)
    c.setFont("Times-Italic", 14)
    c.drawString(x + largura_ad + 2 * mm, y + 3 * mm, "Brás")
    _espacado(c, "SUMARÉ", y - 2 * mm, "Helvetica", 7, PRETO, 1.6, x=x + 1 * mm)


def _bloco_do_qr(c, certificado):
    """
    QR e codigo, no canto inferior direito, dentro da moldura.

    Fica abaixo do ano vertical e a direita da assinatura, sem encostar em
    nenhum dos dois. O codigo completo aparece por extenso: quem estiver com o
    papel na mao e sem camera precisa conseguir digitar o endereco.
    """
    endereco = url_de_validacao(certificado)
    lado = 24 * mm
    # A 60 mm da borda, a linha do codigo — que e o elemento mais largo do
    # bloco, 52 mm em Courier 5,6 — termina antes da moldura interna, que
    # esta em 280 mm. Encostar o QR no canto empurraria o codigo para fora.
    x = LARGURA - 60 * mm
    y = 32 * mm
    centro = x + lado / 2

    c.drawImage(
        _qr_code(endereco), x, y, width=lado, height=lado, mask="auto"
    )

    c.setFillColorRGB(*CINZA)
    c.setFont("Helvetica", 6)
    c.drawCentredString(centro, y - 4 * mm, "Valide este certificado em")
    c.setFillColorRGB(*PRETO)
    c.setFont("Helvetica-Bold", 6.8)
    c.drawCentredString(centro, y - 7.6 * mm, host_publico())
    c.setFont("Courier", 5.6)
    c.drawCentredString(
        centro, y - 11.4 * mm, "Código: {}".format(certificado.verification_code)
    )


def _assinatura(c, certificado):
    """
    Bloco de assinatura: rubrica, nome em caixa alta e cargo.

    A rubrica usa Times-BoldItalic. Nao e uma assinatura digitalizada, e nao
    finge ser: nenhum arquivo de assinatura foi inventado. Quando existir um
    asset oficial, ele entra aqui como imagem.
    """
    nome = certificado.signatory_name_snapshot
    cargo = certificado.signatory_title_snapshot
    if not nome:
        return

    c.setFillColorRGB(*PRETO)
    tamanho = _fonte_que_cabe(nome, "Times-BoldItalic", 21, 110 * mm, minimo=12)
    c.setFont("Times-BoldItalic", tamanho)
    c.drawCentredString(CENTRO_X, 47 * mm, nome)

    _espacado(c, nome.upper(), 38 * mm, "Helvetica-Bold", 10, PRETO, 0.8)

    if cargo:
        c.setFillColorRGB(*CINZA)
        c.setFont("Helvetica", 9.5)
        c.drawCentredString(CENTRO_X, 31.5 * mm, cargo)


def _ano_vertical(c, certificado):
    """
    O ano em destaque na lateral direita, como no modelo.

    Vem de certificate_year_snapshot, nunca de timezone.now().year: o
    documento e reimpresso anos depois e precisa continuar dizendo o ano em
    que o curso aconteceu.
    """
    ano = certificado.certificate_year_snapshot
    if not ano:
        return

    # A 36 mm da borda o bloco do ano ocupa de 261 a 275 mm, e a linha interna
    # da moldura esta em 280: fica dentro, sem encostar.
    c.saveState()
    c.translate(LARGURA - 36 * mm, 108 * mm)
    c.rotate(-90)
    _espacado(c, str(ano), 0, "Times-Bold", 54, ornamentos.VERDE_ESCURO, 1.5, centro=0)
    c.restoreState()


def _desenhar_v2(c, certificado):
    """Modelo oficial da AD Bras Sumare."""
    ornamentos.fundo(c, LARGURA, ALTURA)
    ornamentos.trama_esquerda(c)
    ornamentos.trama_inferior_direita(c)
    ornamentos.canto_inferior_esquerdo(c)
    ornamentos.canto_superior_direito(c)
    ornamentos.moldura(c, LARGURA, ALTURA)

    _logo(c, 30 * mm, 178 * mm)

    _espacado(c, "CERTIFICADO", 168 * mm, "Times-Bold", 44, PRETO, 2.5)
    _espacado(c, "DE CONCLUSÃO", 157 * mm, "Helvetica-Bold", 13, PRETO, 3.5)

    _bloco_centralizado(
        c,
        certificado.course_name_snapshot,
        138 * mm,
        "Helvetica",
        12,
        CINZA,
        LARGURA_TEXTO,
        max_linhas=1,
        minimo=9,
    )
    _bloco_centralizado(
        c,
        certificado.modulo_impresso,
        129 * mm,
        "Helvetica-Bold",
        13.5,
        PRETO,
        LARGURA_TEXTO,
        max_linhas=1,
        minimo=9,
    )

    c.setFillColorRGB(*CINZA)
    c.setFont("Times-Italic", 11.5)
    c.drawCentredString(CENTRO_X, 115 * mm, "Certificamos que")

    # A partir daqui o fluxo e vertical de verdade: o divisor desce quando o
    # nome ocupa duas linhas, e o texto de conclusao desce junto. Com posicoes
    # fixas, um nome de quatro sobrenomes escrevia por cima da regua — e o
    # documento so mostrava isso depois de gerado.
    #
    # Duas linhas no maximo para o nome. Encolher indefinidamente deixaria o
    # elemento principal do documento menor que a frase de rodape.
    ultima_do_nome = _bloco_centralizado(
        c,
        certificado.student_name_snapshot,
        101 * mm,
        "Times-Bold",
        27,
        PRETO,
        LARGURA_TEXTO - 10 * mm,
        max_linhas=2,
        entrelinha=1.15,
        minimo=14,
    )

    y_divisor = ultima_do_nome - 7 * mm
    ornamentos.divisor(c, CENTRO_X, y_divisor, 90 * mm)

    _bloco_centralizado(
        c,
        _texto_de_conclusao(certificado),
        y_divisor - 10 * mm,
        "Helvetica",
        11.5,
        PRETO,
        LARGURA_TEXTO - 20 * mm,
        max_linhas=3,
        entrelinha=1.75,
        minimo=8,
    )

    _ano_vertical(c, certificado)
    _assinatura(c, certificado)
    _bloco_do_qr(c, certificado)


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------

DESENHOS = {1: _desenhar_v1, 2: _desenhar_v2}


def render_certificate_pdf(certificado):
    """
    Devolve os bytes do PDF de um certificado.

    Le exclusivamente os campos *_snapshot: o documento e o que foi emitido,
    nao o que o banco diz hoje. Nota nao aparece de proposito — o certificado
    atesta conclusao, e a nota pertence ao resultado academico.

    Dois caminhos, e a ordem entre eles importa:

        template_snapshot preenchido
            Certificado emitido da Etapa 10 em diante. O desenho vem da
            configuracao congelada na emissao: arte oficial no fundo, campos
            posicionados por cima. Nada aqui decide estetica.

        template_snapshot vazio
            Certificado emitido ate a Etapa 9, quando o layout era codigo.
            Continua sendo desenhado por _desenhar_v1 ou _desenhar_v2, pela
            template_version.

    O segundo caminho nao e um fallback para quando falta configuracao — a
    emissao recusa nesse caso. Ele existe porque os documentos ja emitidos
    nao tem snapshot e nao ha de onde tirar um: inventar posicoes para eles
    seria afirmar uma configuracao que nunca existiu.
    """
    if certificado.template_snapshot:
        from certificates.render import render_from_snapshot
        from certificates.snapshot import valores_do_certificado

        snapshot = dict(certificado.template_snapshot)
        snapshot.setdefault(
            "title", "Certificado - {}".format(certificado.student_name_snapshot)
        )
        snapshot.setdefault("author", certificado.institution_name_snapshot)
        snapshot.setdefault("creator", settings.APP_NAME)
        return render_from_snapshot(snapshot, valores_do_certificado(certificado))

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(A4))
    c.setTitle("Certificado - {}".format(certificado.student_name_snapshot))
    c.setAuthor(certificado.institution_name_snapshot)
    c.setSubject("Certificado de conclusão")
    c.setCreator(settings.APP_NAME)

    desenhar = DESENHOS.get(certificado.template_version, _desenhar_v2)
    desenhar(c, certificado)

    c.showPage()
    c.save()
    return buffer.getvalue()
