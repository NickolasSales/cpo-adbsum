"""
Registro central das fontes do certificado.

Um lugar so sabe o nome de cada familia, o peso de cada arquivo, o caminho
no disco e o nome que o ReportLab recebe. Nem a tela, nem o renderizador,
nem o JavaScript repetem essa informacao — todos perguntam aqui.

A razao e a que o pedido enuncia numa linha: a fonte do preview e a fonte do
PDF precisam ser a MESMA. Duas listas paralelas — uma em CSS, outra em
Python — divergem no primeiro arquivo acrescentado, e a divergencia so
aparece quando alguem compara a tela com o papel impresso.

    registro (este arquivo)
        |
        +-- @font-face   -> static/css/fontes-certificado.css  (gerado)
        +-- ReportLab    -> registrar_fontes()
        +-- editor       -> catalogo_para_o_editor()
        +-- validacao    -> FAMILIAS_PERMITIDAS / resolver_fonte()

O CSS e GERADO a partir daqui, por `manage.py gerar_css_das_fontes`, e um
teste refaz a geracao e compara. Nao ha como acrescentar um arquivo de fonte
e esquecer do navegador: a suite recusa.


Estatica, nunca variavel
------------------------
Montserrat e Bodoni Moda existem no google/fonts apenas como fontes
VARIAVEIS. Nao servem: o navegador entende o eixo de peso e desenha o Bold;
o ReportLab le a tabela `glyf` direto e desenha sempre a instancia padrao.
O resultado seria tela em Bold e PDF em Regular — exatamente a divergencia
que este registro existe para impedir.

As instancias estaticas vem do repositorio do proprio autor, no mesmo commit
que o Google usou para compilar a variavel. Ver PROVENIENCIA.md na pasta das
fontes.


Este modulo nao importa modelos
-------------------------------
De proposito. `certificates.models.template` importa daqui, e a seta so
aponta num sentido. O registro e um fato sobre arquivos em disco; nao
depende de banco, de migration nem de request.
"""

import threading
from pathlib import Path

from django.conf import settings

# ---------------------------------------------------------------------------
# Onde os arquivos moram
# ---------------------------------------------------------------------------

# Relativo a raiz do static. Nunca um caminho absoluto: o mesmo codigo roda
# no Windows do desenvolvimento e no Ubuntu do servidor, e um
# "C:\Users\..." gravado aqui quebraria um dos dois.
RAIZ_DAS_FONTES = "fonts/certificates"


# ---------------------------------------------------------------------------
# Pesos
# ---------------------------------------------------------------------------

# Os quatro pesos que as familias de texto oferecem. Os numeros sao os do
# CSS, e nao um enum proprio, porque e assim que o navegador entende
# `font-weight` — inventar "MEDIO" aqui obrigaria uma segunda traducao no
# JavaScript.
REGULAR = 400
MEDIO = 500
SEMIBOLD = 600
NEGRITO = 700

PESOS = (REGULAR, MEDIO, SEMIBOLD, NEGRITO)

ROTULOS_DOS_PESOS = {
    REGULAR: "Regular",
    MEDIO: "Medio",
    SEMIBOLD: "Semibold",
    NEGRITO: "Negrito",
}

PESO_PADRAO = REGULAR


# ---------------------------------------------------------------------------
# O registro
# ---------------------------------------------------------------------------
#
# Cada face e (peso, italico) -> (nome no ReportLab, arquivo).
#
# O nome do ReportLab e o nome PostScript real da fonte, conferido contra o
# proprio arquivo pelo teste. Nao e um apelido nosso: se um dia o arquivo for
# trocado por outra versao com nome diferente, o teste diz.

CERTIFICATE_FONTS = {
    "BODONI_MODA": {
        "rotulo": "Bodoni Moda",
        # Serifada de contraste alto, para titulo e ano.
        "css_familia": "Bodoni Moda",
        "css_generica": "serif",
        "pasta": "bodoni-moda",
        "faces": {
            (REGULAR, False): ("BodoniModa-Regular", "BodoniModa-Regular.ttf"),
            (REGULAR, True): ("BodoniModa-Italic", "BodoniModa-Italic.ttf"),
            (MEDIO, False): ("BodoniModa-Medium", "BodoniModa-Medium.ttf"),
            (MEDIO, True): ("BodoniModa-MediumItalic", "BodoniModa-MediumItalic.ttf"),
            (SEMIBOLD, False): ("BodoniModa-SemiBold", "BodoniModa-SemiBold.ttf"),
            (SEMIBOLD, True): (
                "BodoniModa-SemiBoldItalic",
                "BodoniModa-SemiBoldItalic.ttf",
            ),
            (NEGRITO, False): ("BodoniModa-Bold", "BodoniModa-Bold.ttf"),
            (NEGRITO, True): ("BodoniModa-BoldItalic", "BodoniModa-BoldItalic.ttf"),
        },
    },
    "MONTSERRAT": {
        "rotulo": "Montserrat",
        "css_familia": "Montserrat",
        "css_generica": "sans-serif",
        "pasta": "montserrat",
        "faces": {
            (REGULAR, False): ("Montserrat-Regular", "Montserrat-Regular.ttf"),
            (REGULAR, True): ("Montserrat-Italic", "Montserrat-Italic.ttf"),
            (MEDIO, False): ("Montserrat-Medium", "Montserrat-Medium.ttf"),
            (MEDIO, True): ("Montserrat-MediumItalic", "Montserrat-MediumItalic.ttf"),
            (SEMIBOLD, False): ("Montserrat-SemiBold", "Montserrat-SemiBold.ttf"),
            (SEMIBOLD, True): (
                "Montserrat-SemiBoldItalic",
                "Montserrat-SemiBoldItalic.ttf",
            ),
            (NEGRITO, False): ("Montserrat-Bold", "Montserrat-Bold.ttf"),
            (NEGRITO, True): ("Montserrat-BoldItalic", "Montserrat-BoldItalic.ttf"),
        },
    },
    # As duas caligraficas existem em UM desenho so. Nao ha Bold, nao ha
    # Italico, e nao ha o que inventar: uma caligrafica "negrito" simulada
    # pelo navegador engorda os tracos e destroi justamente o que faz ela
    # servir para assinatura.
    #
    # Ver a nota sobre pesos_suportados() logo abaixo: a tela nem oferece a
    # combinacao, e o servidor cai no peso existente se ela chegar por outro
    # caminho.
    "GREAT_VIBES": {
        "rotulo": "Great Vibes",
        "css_familia": "Great Vibes",
        "css_generica": "cursive",
        "pasta": "great-vibes",
        "faces": {
            (REGULAR, False): ("GreatVibes-Regular", "GreatVibes-Regular.ttf"),
        },
    },
    "ALLURA": {
        "rotulo": "Allura",
        "css_familia": "Allura",
        "css_generica": "cursive",
        "pasta": "allura",
        "faces": {
            (REGULAR, False): ("Allura-Regular", "Allura-Regular.ttf"),
        },
    },
}


# As Type 1 que o proprio formato PDF carrega. Continuam disponiveis como
# alternativa: nao dependem de arquivo nenhum, e por isso sao o unico caminho
# que funciona mesmo que o diretorio de fontes desapareca.
#
# Os nomes ("Helvetica", "Times") ficaram como estavam. Renomea-los para
# HELVETICA obrigaria a migrar linhas ja gravadas e reescrever snapshots de
# certificados ja emitidos — troca de estilo que custaria historico.
FONTES_EMBUTIDAS = {
    "Helvetica": {
        "rotulo": "Helvetica",
        "css_familia": "Helvetica",
        "css_generica": "Arial, sans-serif",
        "faces": {
            (REGULAR, False): ("Helvetica", None),
            (REGULAR, True): ("Helvetica-Oblique", None),
            (NEGRITO, False): ("Helvetica-Bold", None),
            (NEGRITO, True): ("Helvetica-BoldOblique", None),
        },
    },
    "Times": {
        "rotulo": "Times",
        "css_familia": "Times New Roman",
        "css_generica": "Times, serif",
        "faces": {
            # A regular do Times chama "Times-Roman", e a inclinada da
            # Helvetica chama "Oblique". Nao ha padrao entre as familias do
            # formato — e por isso que a composicao precisa desta tabela em
            # vez de concatenar strings.
            (REGULAR, False): ("Times-Roman", None),
            (REGULAR, True): ("Times-Italic", None),
            (NEGRITO, False): ("Times-Bold", None),
            (NEGRITO, True): ("Times-BoldItalic", None),
        },
    },
    "Courier": {
        "rotulo": "Courier",
        "css_familia": "Courier New",
        "css_generica": "Courier, monospace",
        "faces": {
            (REGULAR, False): ("Courier", None),
            (REGULAR, True): ("Courier-Oblique", None),
            (NEGRITO, False): ("Courier-Bold", None),
            (NEGRITO, True): ("Courier-BoldOblique", None),
        },
    },
}


# Tudo junto, na ordem em que a tela oferece: primeiro as institucionais,
# depois as de sistema.
TIPOGRAFIA = {}
for _chave, _dados in CERTIFICATE_FONTS.items():
    TIPOGRAFIA[_chave] = dict(_dados, arquivo=True)
for _chave, _dados in FONTES_EMBUTIDAS.items():
    TIPOGRAFIA[_chave] = dict(_dados, arquivo=False, pasta=None)
del _chave, _dados

# A lista branca de familias. O navegador escolhe DENTRO dela; nada que venha
# de fora vira nome de arquivo.
FAMILIAS_PERMITIDAS = tuple(TIPOGRAFIA)
FAMILIA_PADRAO = "Helvetica"

# Todo nome que o ReportLab pode receber. E o ultimo filtro do renderizador:
# um nome fora daqui faria a biblioteca procurar um arquivo inexistente no
# meio de uma emissao.
FONTES_PERMITIDAS = tuple(
    sorted(
        {
            nome
            for dados in TIPOGRAFIA.values()
            for nome, _ in dados["faces"].values()
        }
    )
)

# Nome no ReportLab -> (familia, peso, italico). Construido a partir da
# tabela acima para que as duas nunca discordem.
_DECOMPOSICAO = {
    nome: (familia, peso, italico)
    for familia, dados in TIPOGRAFIA.items()
    for (peso, italico), (nome, _) in dados["faces"].items()
}


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------


def pesos_suportados(familia):
    """Os pesos que a familia realmente tem arquivo, em ordem."""
    dados = TIPOGRAFIA.get(familia) or TIPOGRAFIA[FAMILIA_PADRAO]
    return tuple(sorted({peso for peso, _ in dados["faces"]}))


def tem_italico(familia):
    dados = TIPOGRAFIA.get(familia) or TIPOGRAFIA[FAMILIA_PADRAO]
    return any(italico for _, italico in dados["faces"])


def rotulo(familia):
    dados = TIPOGRAFIA.get(familia) or TIPOGRAFIA[FAMILIA_PADRAO]
    return dados["rotulo"]


def pilha_css(familia):
    """
    O valor de `font-family` que o navegador recebe.

    A generica no fim nao e decoracao: se o arquivo demorar ou falhar, o
    texto aparece em algo legivel do mesmo genero em vez de cair no padrao do
    navegador. Uma caligrafica que vira Times avisa menos que uma
    caligrafica que vira a cursiva do sistema.
    """
    dados = TIPOGRAFIA.get(familia) or TIPOGRAFIA[FAMILIA_PADRAO]
    return '"{}", {}'.format(dados["css_familia"], dados["css_generica"])


def _peso_mais_proximo(familia, peso):
    """
    O peso existente mais perto do pedido.

    Great Vibes com negrito pedido cai em 400 — que e o unico que ela tem.
    Nao ha simulacao: o pedido diz, com todas as letras, para nao inventar
    arquivo inexistente. A tela tambem nao oferece a combinacao; isto aqui e
    a rede embaixo, para um POST montado a mao ou um snapshot antigo.
    """
    disponiveis = pesos_suportados(familia)
    if peso in disponiveis:
        return peso
    return min(disponiveis, key=lambda existente: (abs(existente - peso), existente))


def normalizar_peso(valor):
    """
    Um numero qualquer para um dos pesos do catalogo.

    O booleano vem primeiro, e nao por elegancia: em Python `True` E um int
    de valor 1, e sem esta linha `resolver_fonte("Times", True)` pediria peso
    1, cairia no mais proximo — 400 — e devolveria a Regular. O negrito
    sumiria em silencio, em qualquer chamador que ainda passe um marcador no
    lugar do peso: um snapshot antigo, um script, uma aba aberta antes do
    deploy.
    """
    if isinstance(valor, bool):
        return NEGRITO if valor else REGULAR
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return PESO_PADRAO
    if numero in PESOS:
        return numero
    return min(PESOS, key=lambda peso: (abs(peso - numero), peso))


def decompor_fonte(valor):
    """
    (familia, peso, italico) a partir de familia OU de nome composto.

    Aceita os dois porque os dois existem no historico: os campos gravados
    ate a Etapa 10 guardavam "Times-BoldItalic", e os snapshots ja emitidos
    continuam guardando. Um documento antigo precisa continuar saindo com a
    fonte com que foi assinado.
    """
    texto = str(valor or "").strip()
    if texto in _DECOMPOSICAO:
        return _DECOMPOSICAO[texto]
    if texto in TIPOGRAFIA:
        return texto, PESO_PADRAO, False
    return FAMILIA_PADRAO, PESO_PADRAO, False


def resolver_fonte(valor, peso=None, italico=False, negrito=None):
    """
    Nome de fonte que o ReportLab vai receber.

    Os atributos do nome recebido e os argumentos se SOMAM, e por isso a
    funcao e idempotente: resolver duas vezes devolve o mesmo nome, e
    aplica-la sobre um snapshot antigo nao altera o resultado.

    `negrito` existe para os snapshots da Etapa 10, que gravaram um booleano
    em vez de um peso. True vale 700. Nao ha um segundo lugar guardando a
    mesma verdade: o campo do banco tem SO o peso, e este argumento e um
    tradutor de dado antigo na fronteira.
    """
    familia, peso_do_nome, italico_do_nome = decompor_fonte(valor)

    pedido = peso_do_nome
    if negrito:
        pedido = max(pedido, NEGRITO)
    if peso is not None:
        pedido = max(pedido, normalizar_peso(peso))

    quer_italico = bool(italico or italico_do_nome)

    dados = TIPOGRAFIA.get(familia) or TIPOGRAFIA[FAMILIA_PADRAO]
    escolhido = _peso_mais_proximo(familia, pedido)

    face = dados["faces"].get((escolhido, quer_italico))
    if face is None:
        # A familia nao tem italico. Sai a versao reta do mesmo peso, que e a
        # unica coisa honesta a fazer com um arquivo que nao existe.
        face = dados["faces"].get((escolhido, False))
    if face is None:
        face = dados["faces"][(pesos_suportados(familia)[0], False)]
    return face[0]


def familia_da_fonte(nome):
    """A familia a que um nome do ReportLab pertence."""
    return decompor_fonte(nome)[0]


# ---------------------------------------------------------------------------
# Caminho no disco
# ---------------------------------------------------------------------------


def raiz_no_disco():
    """
    A pasta das fontes, resolvida pela configuracao da aplicacao.

    Sai de STATICFILES_DIRS, e nao de um caminho escrito no codigo. O mesmo
    arquivo roda no Windows e no Ubuntu, e nenhum dos dois aparece aqui.
    """
    for diretorio in getattr(settings, "STATICFILES_DIRS", ()) or ():
        candidato = Path(diretorio) / RAIZ_DAS_FONTES
        if candidato.is_dir():
            return candidato
    return Path(settings.BASE_DIR) / "static" / RAIZ_DAS_FONTES


def caminho_relativo(familia, peso=REGULAR, italico=False):
    """
    O caminho da face DENTRO do static, com barras normais.

    E o que o CSS gerado escreve e o que o teste confere. None para as
    embutidas, que nao tem arquivo.
    """
    dados = TIPOGRAFIA.get(familia)
    if not dados or not dados.get("arquivo"):
        return None
    face = dados["faces"].get((peso, italico))
    if face is None or face[1] is None:
        return None
    return "{}/{}/{}".format(RAIZ_DAS_FONTES, dados["pasta"], face[1])


def caminho_no_disco(familia, peso=REGULAR, italico=False):
    relativo = caminho_relativo(familia, peso, italico)
    if relativo is None:
        return None
    return raiz_no_disco().joinpath(*relativo.split("/")[2:])


def faces_com_arquivo():
    """
    (familia, peso, italico, nome_reportlab, caminho_relativo) de tudo que
    depende de arquivo. Usado pelo registro no ReportLab, pelo gerador de
    CSS, pela verificacao do `manage.py check` e pelos testes.
    """
    saida = []
    for familia, dados in CERTIFICATE_FONTS.items():
        for (peso, italico), (nome, _arquivo) in sorted(dados["faces"].items()):
            saida.append(
                (familia, peso, italico, nome, caminho_relativo(familia, peso, italico))
            )
    return saida


def arquivos_ausentes():
    """As faces cujo arquivo nao esta no disco. Vazio e o estado saudavel."""
    faltando = []
    for familia, peso, italico, nome, _relativo in faces_com_arquivo():
        caminho = caminho_no_disco(familia, peso, italico)
        if caminho is None or not caminho.is_file():
            faltando.append((familia, peso, italico, nome))
    return faltando


# ---------------------------------------------------------------------------
# Registro no ReportLab
# ---------------------------------------------------------------------------


class FonteIndisponivel(RuntimeError):
    """
    O arquivo de uma fonte pedida nao pode ser carregado.

    Levantada em vez de trocar a fonte em silencio. Um certificado impresso
    com outra tipografia que nao a configurada e um defeito que so aparece
    quando o documento ja esta na mao de alguem.
    """

    def __init__(self, familia):
        self.familia = familia
        # O rotulo, e nao a chave interna nem o caminho: esta mensagem chega
        # ao administrador na tela de preview. "BODONI_MODA" nao diz nada a
        # ele, e o caminho do disco do servidor nao e assunto dele.
        legivel = rotulo(familia) if familia in TIPOGRAFIA else str(familia)
        super().__init__("Nao foi possivel carregar a fonte {}.".format(legivel))


_TRAVA = threading.Lock()
_REGISTRADAS = set()
_FALHAS = {}


def registrar_fontes(forcar=False):
    """
    Poe as fontes de arquivo no ReportLab. Idempotente.

    Chamada no inicio de cada renderizacao. Registrar de novo a cada elemento
    desenhado releria dezoito arquivos por pagina; o conjunto `_REGISTRADAS`
    evita isso, e a trava existe porque o gunicorn atende em varias threads e
    o registro do ReportLab e um dicionario global.

    O que falha nao interrompe o resto: as demais familias continuam
    utilizaveis, e a face ausente vira uma recusa clara no momento em que
    alguem tentar usa-la — nao um PDF com a fonte errada.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    with _TRAVA:
        if forcar:
            _REGISTRADAS.clear()
            _FALHAS.clear()

        conhecidas = set(pdfmetrics.getRegisteredFontNames())
        for familia, peso, italico, nome, _relativo in faces_com_arquivo():
            if nome in _REGISTRADAS and nome in conhecidas:
                continue
            caminho = caminho_no_disco(familia, peso, italico)
            try:
                pdfmetrics.registerFont(TTFont(nome, str(caminho)))
            except Exception as erro:
                # A mensagem guarda o motivo tecnico para o log; o caminho
                # nao vai para a tela de ninguem.
                _FALHAS[nome] = "{}: {}".format(type(erro).__name__, erro)
                _REGISTRADAS.discard(nome)
            else:
                _FALHAS.pop(nome, None)
                _REGISTRADAS.add(nome)

    return tuple(sorted(_REGISTRADAS))


def falhas_de_registro():
    return dict(_FALHAS)


def exigir_fonte(nome):
    """
    Confere que `nome` esta pronto para desenhar, ou levanta.

    As Type 1 embutidas passam sempre: nao dependem de arquivo. As de arquivo
    passam se o registro deu certo.
    """
    if nome not in FONTES_PERMITIDAS:
        # Nao e arquivo faltando: e nome que nunca existiu. Quem chama ja
        # deveria ter passado por resolver_fonte; dizer "carregue Helvetica"
        # aqui esconderia um defeito de codigo atras de uma mensagem de
        # operacao.
        raise FonteIndisponivel(nome)
    familia, _peso, _italico = decompor_fonte(nome)
    if not TIPOGRAFIA[familia].get("arquivo"):
        return nome
    if nome in _REGISTRADAS:
        return nome
    raise FonteIndisponivel(familia)


# ---------------------------------------------------------------------------
# Para a tela
# ---------------------------------------------------------------------------


def catalogo_para_o_editor():
    """
    O que o JavaScript do editor precisa saber sobre fontes.

    Uma estrutura so, entregue por json_script. Sem isso o editor teria a
    propria tabela de familias e pesos, e a tela ofereceria um Semibold que o
    PDF nao tem — ou deixaria de oferecer um que tem.
    """
    return [
        {
            "valor": familia,
            "rotulo": dados["rotulo"],
            "css": pilha_css(familia),
            "pesos": [
                {"valor": peso, "rotulo": ROTULOS_DOS_PESOS[peso]}
                for peso in pesos_suportados(familia)
            ],
            "italico": tem_italico(familia),
            "arquivo": bool(dados.get("arquivo")),
        }
        for familia, dados in TIPOGRAFIA.items()
    ]
