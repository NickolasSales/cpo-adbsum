"""
Validacao do arquivo de comprovante enviado pelo ministro (Fase 2E).

Quem envia e um ministro pelo celular, e o arquivo depois e aberto por um
administrador no navegador. Entao a pergunta nao e "parece um comprovante?"
— isso e a conferencia humana —, e sim "e mesmo um PDF, JPG ou PNG, de um
tamanho razoavel?". Nada aqui le o conteudo do comprovante: nao ha OCR, o
JavaScript de um PDF nao e interpretado, a imagem nao e redimensionada.

Tres barreiras, da mais barata para a mais cara:

    tamanho      5 MB (models.TAMANHO_MAXIMO_DO_COMPROVANTE). O navegador
                 avisa antes de enviar, mas e o servidor que decide.
    nome         extensao .pdf, .jpg, .jpeg ou .png, e um tipo declarado pelo
                 navegador compativel. E a barreira fraca: os dois sao texto
                 escolhido por quem envia.
    conteudo     a assinatura (magic bytes) no inicio do arquivo decide o
                 formato. `comprovante.jpg` que comeca com "MZ" e um
                 executavel e e recusado. Imagem passa pelo Pillow (cabecalho,
                 limite de pixels, verify); PDF pela estrutura minima
                 (cabecalho %PDF-, objetos e %%EOF no fim).

O tipo gravado e o detectado no conteudo, e o nome original so vira
metadata exibida — nunca caminho. Nao importa nada de certificates (o
modulo de upload da arte): o Controle de Ministros nao depende do dominio
academico, e as regras aqui sao outras.
"""

import hashlib
import unicodedata
from dataclasses import dataclass

from PIL import Image

from common.exceptions import DomainError
from ministers.models import TAMANHO_MAXIMO_DO_COMPROVANTE

PDF = "application/pdf"
JPEG = "image/jpeg"
PNG = "image/png"

EXTENSOES = {".pdf": PDF, ".jpg": JPEG, ".jpeg": JPEG, ".png": PNG}

# O que o navegador pode declarar. octet-stream e vazio aparecem em alguns
# Androids e gerenciadores de arquivo; aceitos porque o conteudo e conferido
# de qualquer jeito. text/html, image/svg+xml e o resto sao recusados ja aqui.
TIPOS_DECLARADOS = {PDF, JPEG, PNG, "image/jpg", "image/pjpeg", "application/octet-stream", ""}

ASSINATURAS = (
    (b"%PDF-", PDF),
    (b"\x89PNG\r\n\x1a\n", PNG),
    (b"\xff\xd8\xff", JPEG),
)

FORMATO_DO_PILLOW = {JPEG: "JPEG", PNG: "PNG"}

# Imagem: limite por lado e por area, conferido no CABECALHO, antes de
# qualquer pixel ser decodificado. 50 megapixels cobrem a foto de qualquer
# celular atual (8160x6120); um PNG de 30 KB que declara 40000x40000 — a
# bomba de descompressao — para aqui.
LADO_MAXIMO = 12000
PIXELS_MAXIMOS = 50_000_000

# Onde procurar o %%EOF: alguns geradores deixam quebras de linha ou lixo
# depois dele.
CAUDA_DO_PDF = 2048

TAMANHO_DO_NOME = 120

FORMATO_NAO_ACEITO = "Formato não aceito. Envie o comprovante em PDF, JPG ou PNG."
CONTEUDO_INVALIDO = (
    "O conteúdo do arquivo não é um PDF, JPG ou PNG válido. "
    "A extensão do nome não define o formato."
)


@dataclass(frozen=True)
class ArquivoConferido:
    mime_type: str
    tamanho: int
    sha256: str
    nome_original: str


def _megabytes(tamanho):
    return "{:.1f}".format(tamanho / (1024 * 1024)).replace(".", ",")


def _extensao(nome):
    nome = (nome or "").lower()
    if "." not in nome:
        return ""
    return "." + nome.rsplit(".", 1)[-1]


def nome_para_exibicao(nome):
    """
    O nome que veio do navegador, reduzido a algo seguro de MOSTRAR.

    Fica so o nome base (sem pastas — `../../etc/passwd` vira `passwd`),
    sem caracteres de controle, com espacos normalizados e ate 120
    caracteres, preservando a extensao. Mesmo assim e exibido sempre com
    escape: e texto de usuario.
    """
    nome = (nome or "").replace("\\", "/").rsplit("/", 1)[-1]
    nome = unicodedata.normalize("NFC", nome)
    nome = "".join(c for c in nome if not unicodedata.category(c).startswith("C"))
    nome = " ".join(nome.split()).strip(" .")
    if len(nome) > TAMANHO_DO_NOME:
        extensao = _extensao(nome)
        if len(extensao) > 6:
            extensao = ""
        nome = nome[: TAMANHO_DO_NOME - len(extensao)].rstrip(" .") + extensao
    return nome or "comprovante"


def _detectar(inicio):
    for assinatura, tipo in ASSINATURAS:
        if inicio.startswith(assinatura):
            return tipo
    return None


def _conferir_imagem(arquivo, tipo):
    arquivo.seek(0)
    try:
        # open() le so o cabecalho: formato e dimensoes sem decodificar.
        with Image.open(arquivo) as imagem:
            formato = imagem.format
            largura, altura = imagem.size
    except Image.DecompressionBombError:
        raise DomainError("A imagem tem pixels demais. Envie uma foto ou print de tamanho normal.")
    except Exception:
        raise DomainError(CONTEUDO_INVALIDO)

    if formato != FORMATO_DO_PILLOW[tipo]:
        raise DomainError(CONTEUDO_INVALIDO)
    if largura < 1 or altura < 1:
        raise DomainError(CONTEUDO_INVALIDO)
    if largura > LADO_MAXIMO or altura > LADO_MAXIMO or largura * altura > PIXELS_MAXIMOS:
        raise DomainError(
            "A imagem tem {}x{} pixels, acima do limite. Envie uma foto ou print "
            "de tamanho normal.".format(largura, altura)
        )

    arquivo.seek(0)
    try:
        with Image.open(arquivo) as imagem:
            imagem.verify()
    except Exception:
        raise DomainError("A imagem está corrompida ou incompleta. Envie novamente.")


def _conferir_pdf(arquivo, tamanho):
    """
    Estrutura minima, sem parser: cabecalho %PDF-1.x/2.x, ao menos um objeto
    e o marcador de fim. Pega o arquivo truncado e o que so finge ser PDF.
    O conteudo nao e interpretado.
    """
    arquivo.seek(0)
    cabecalho = arquivo.read(1024)
    if not (cabecalho[:5] == b"%PDF-" and cabecalho[5:6] in (b"1", b"2") and cabecalho[6:7] == b"."):
        raise DomainError(CONTEUDO_INVALIDO)

    arquivo.seek(max(0, tamanho - CAUDA_DO_PDF))
    if b"%%EOF" not in arquivo.read(CAUDA_DO_PDF):
        raise DomainError("O PDF está incompleto ou corrompido. Envie novamente.")

    arquivo.seek(0)
    tem_objeto = False
    anterior = b""
    for pedaco in arquivo.chunks():
        if b" obj" in anterior[-8:] + pedaco:
            tem_objeto = True
            break
        anterior = pedaco
    if not tem_objeto:
        raise DomainError(CONTEUDO_INVALIDO)


def conferir_comprovante(arquivo):
    """
    Confere um upload e devolve ArquivoConferido, ou levanta DomainError com
    a primeira coisa errada. Deixa o arquivo posicionado no inicio.
    """
    if arquivo is None:
        raise DomainError("Selecione o comprovante.")

    tamanho = getattr(arquivo, "size", None)
    if not tamanho or tamanho <= 0:
        raise DomainError("O arquivo enviado está vazio.")
    if tamanho > TAMANHO_MAXIMO_DO_COMPROVANTE:
        raise DomainError(
            "O arquivo tem {} MB. O limite é {} MB.".format(
                _megabytes(tamanho), TAMANHO_MAXIMO_DO_COMPROVANTE // (1024 * 1024)
            )
        )

    nome = getattr(arquivo, "name", "") or ""
    pela_extensao = EXTENSOES.get(_extensao(nome))
    declarado = (getattr(arquivo, "content_type", "") or "").split(";")[0].strip().lower()
    if pela_extensao is None or declarado not in TIPOS_DECLARADOS:
        raise DomainError(FORMATO_NAO_ACEITO)

    arquivo.seek(0)
    tipo = _detectar(arquivo.read(16))
    if tipo is None:
        raise DomainError(CONTEUDO_INVALIDO)
    # Foto e print trocam .jpg e .png com frequencia, e as duas sao imagem.
    # PDF chamado de .jpg (ou o contrario) e outra coisa: recusado.
    if (tipo == PDF) != (pela_extensao == PDF):
        raise DomainError(CONTEUDO_INVALIDO)

    if tipo == PDF:
        _conferir_pdf(arquivo, tamanho)
    else:
        _conferir_imagem(arquivo, tipo)

    arquivo.seek(0)
    digest = hashlib.sha256()
    for pedaco in arquivo.chunks():
        digest.update(pedaco)
    arquivo.seek(0)

    return ArquivoConferido(
        mime_type=tipo,
        tamanho=tamanho,
        sha256=digest.hexdigest(),
        nome_original=nome_para_exibicao(nome),
    )
