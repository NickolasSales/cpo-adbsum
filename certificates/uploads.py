"""
Validacao dos arquivos enviados para os modelos de certificado.

O que este modulo recusa, e por que
-----------------------------------
Um upload administrativo parece inofensivo — quem envia ja e ADMIN. Mas o
arquivo enviado passa a ser servido de volta pela aplicacao, entra num PDF
oficial e ocupa disco de uma instancia pequena. Tres coisas diferentes podem
dar errado, e cada uma tem a sua checagem:

    o arquivo nao e o que diz ser
        A extensao e um pedaco de texto escolhido por quem envia.
        `arte.png` pode ser um HTML, um SVG com script, ou um executavel.
        Por isso o formato e determinado ABRINDO o arquivo com o Pillow e
        lendo o que ele encontrou lá dentro — nao o que o nome promete.

    o arquivo e grande demais
        Dez megabytes de teto. Um A4 em 300 dpi comprimido cabe com folga.

    o arquivo e uma bomba de descompressao
        Um PNG de 40 KB pode declarar 100000 x 100000 pixels. Descompactar
        isso pede dezenas de gigabytes de RAM e derruba o processo. O Pillow
        tem um teto proprio (MAX_IMAGE_PIXELS) que so avisa; aqui o limite e
        conferido antes de qualquer decodificacao, lendo apenas o cabecalho.

Por que somente PNG e JPG, e nao PDF
------------------------------------
O pedido admitia PDF de uma pagina como fundo. O ReportLab nao importa uma
pagina de PDF sozinho: seria preciso pdfrw ou pypdf so para isso, uma
dependencia nova no servidor para um caminho que a alternativa ja resolve.

A decisao desta versao e exigir PNG ou JPG, e ela e explicita. Quem tem a
arte em PDF exporta em 300 dpi — o resultado impresso e o mesmo, porque a
arte entra no documento como imagem de qualquer maneira. Um PDF vetorial
importado como vetor seria melhor em teoria; na pratica a diferenca some na
impressao, e o custo e uma dependencia a mais numa t3.small.
"""

import hashlib

from PIL import Image

from common.exceptions import DomainError

# Formatos aceitos, pelo que o Pillow encontra DENTRO do arquivo.
FORMATOS_ACEITOS = {"PNG": ".png", "JPEG": ".jpg"}

# Extensoes aceitas no nome. Primeira barreira, e a mais fraca das duas: ela
# so evita gastar decodificacao com o que nem pretende ser imagem.
EXTENSOES_ACEITAS = {".png", ".jpg", ".jpeg"}

TAMANHO_MAXIMO = 10 * 1024 * 1024

# Uma arte menor que isto nao tem resolucao para impressao: a A4 em 150 dpi
# ja pede cerca de 1754 px de largura. O piso e propositalmente generoso —
# recusar arte boa seria pior que aceitar arte media.
LARGURA_MINIMA = 900
ALTURA_MINIMA = 600

# Teto por lado e por area. A area e o que de fato protege: 20000 x 200
# passaria por um teto de lado sozinho.
LADO_MAXIMO = 20000
PIXELS_MAXIMOS = 60_000_000

# O aviso do Pillow vira erro nosso antes disso, mas deixar o teto dele
# proximo do nosso evita que uma versao futura decodifique algo enorme em
# silencio.
Image.MAX_IMAGE_PIXELS = PIXELS_MAXIMOS


def _extensao(nome):
    nome = (nome or "").lower()
    if "." not in nome:
        return ""
    return "." + nome.rsplit(".", 1)[-1]


def validar_imagem_enviada(arquivo, *, exigir_resolucao=True):
    """
    Confere um upload e devolve (formato, largura, altura, checksum).

    Levanta DomainError com mensagem legivel na primeira coisa errada. As
    checagens vao da mais barata para a mais cara: tamanho em bytes, depois
    extensao, depois o cabecalho da imagem, e so entao a decodificacao.

    `exigir_resolucao=False` para imagens pequenas por natureza — uma
    assinatura digitalizada ou um selo nao precisam ter a resolucao de uma
    arte de pagina inteira.
    """
    if arquivo is None:
        raise DomainError("Selecione um arquivo.")

    tamanho = getattr(arquivo, "size", None)
    if tamanho is None:
        raise DomainError("Nao foi possivel ler o arquivo enviado.")
    if tamanho <= 0:
        raise DomainError("O arquivo enviado esta vazio.")
    if tamanho > TAMANHO_MAXIMO:
        raise DomainError(
            "O arquivo tem {:.1f} MB. O limite e {} MB.".format(
                tamanho / (1024 * 1024), TAMANHO_MAXIMO // (1024 * 1024)
            )
        )

    if _extensao(getattr(arquivo, "name", "")) not in EXTENSOES_ACEITAS:
        raise DomainError(
            "Formato nao aceito. Envie a arte em PNG ou JPG. "
            "Se ela estiver em PDF, exporte em 300 dpi."
        )

    arquivo.seek(0)
    try:
        # open() le apenas o cabecalho: o formato e as dimensoes ficam
        # disponiveis sem descompactar um unico pixel. E aqui que a bomba de
        # descompressao e barrada, antes de custar memoria.
        with Image.open(arquivo) as imagem:
            formato = imagem.format
            largura, altura = imagem.size
    except DomainError:
        raise
    except Exception:
        # Qualquer falha de leitura vira a mesma mensagem. Distinguir "nao e
        # imagem" de "imagem corrompida" nao ajuda quem envia e detalha o
        # parser para quem sonda.
        raise DomainError(
            "Nao foi possivel ler o arquivo como imagem. Envie um PNG ou JPG "
            "valido."
        )

    if formato not in FORMATOS_ACEITOS:
        raise DomainError(
            "O conteudo do arquivo e {}, e nao PNG ou JPG. A extensao do nome "
            "nao define o formato.".format(formato or "desconhecido")
        )

    if largura > LADO_MAXIMO or altura > LADO_MAXIMO:
        raise DomainError(
            "A imagem tem {}x{} pixels. O limite por lado e {}.".format(
                largura, altura, LADO_MAXIMO
            )
        )
    if largura * altura > PIXELS_MAXIMOS:
        raise DomainError(
            "A imagem tem pixels demais ({}x{}). Reduza a resolucao.".format(
                largura, altura
            )
        )

    if exigir_resolucao and (largura < LARGURA_MINIMA or altura < ALTURA_MINIMA):
        raise DomainError(
            "A arte tem {}x{} pixels, resolucao baixa demais para impressao. "
            "O minimo e {}x{}.".format(
                largura, altura, LARGURA_MINIMA, ALTURA_MINIMA
            )
        )

    # Agora sim decodifica, para garantir que o corpo do arquivo tambem esta
    # integro — um cabecalho valido com dados truncados passaria pelo open().
    arquivo.seek(0)
    try:
        with Image.open(arquivo) as imagem:
            imagem.verify()
    except Exception:
        raise DomainError(
            "O arquivo esta corrompido ou incompleto. Envie novamente."
        )

    arquivo.seek(0)
    digest = hashlib.sha256()
    for pedaco in arquivo.chunks():
        digest.update(pedaco)
    arquivo.seek(0)

    return formato, largura, altura, digest.hexdigest()


def proporcao_compativel(largura, altura, page_width_mm, page_height_mm, *, folga=0.06):
    """
    Se a proporcao da imagem bate com a da pagina, dentro de uma folga.

    Nao levanta erro: uma arte levemente fora de proporcao ainda produz um
    documento utilizavel, e recusar o upload por causa disso deixaria o
    administrador sem saida. Quem chama transforma isto num aviso.
    """
    if not largura or not altura or not page_width_mm or not page_height_mm:
        return True
    da_imagem = largura / altura
    da_pagina = float(page_width_mm) / float(page_height_mm)
    return abs(da_imagem - da_pagina) <= da_pagina * folga
