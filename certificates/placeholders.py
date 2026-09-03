"""
Variaveis do texto personalizado.

    Concluiu com exito o {{nome_curso}}, em {{data_conclusao}}.

O que este modulo NAO faz
-------------------------
Nao usa Django Template, nao usa Jinja, nao usa `eval`, nao usa
`str.format`. Nenhum dos quatro.

O motivo e concreto. Um texto administrativo entregue a um motor de template
nao e um texto: e codigo. `{{ settings.SECRET_KEY }}` no Django Template
resolve. `{x.__class__.__init__.__globals__}` no str.format alcanca o modulo
inteiro. Bastaria uma conta ADMIN comprometida — ou um dia de descuido — para
o campo de texto do certificado virar um leitor do processo.

O que ele faz
-------------
Uma expressao regular reconhece `{{nome}}`, e o nome e procurado num
dicionario fechado. O que nao esta no dicionario nao existe: nao resolve, e
nem chega a ser gravado, porque a validacao recusa antes.

    RESOLVEDORES[nome]  ->  FieldType correspondente

E so isso. O valor vem do mesmo lugar de sempre — os campos *_snapshot do
certificado, via certificates.snapshot — entao um placeholder nunca alcanca
mais dado do que um campo solto ja alcancava.
"""

import re

from certificates.models.template import FieldType

# ---------------------------------------------------------------------------
# A lista branca
# ---------------------------------------------------------------------------

# `{{nome}}` -> tipo de campo cujo valor sera impresso no lugar.
#
# Os nomes sao em portugues porque quem escreve o texto e o secretario da
# instituicao, e nao um programador. `{{data_conclusao}}` se le; STUDENT_NAME
# dentro de uma frase em portugues nao.
PLACEHOLDERS = {
    "nome_aluno": FieldType.STUDENT_NAME,
    "data_conclusao": FieldType.COMPLETION_DATE,
    "nome_curso": FieldType.COURSE_NAME,
    "nome_modulo": FieldType.MODULE_NAME,
    "datas_curso": FieldType.COURSE_DATES,
    "local_curso": FieldType.COURSE_LOCATION,
    "carga_horaria": FieldType.WORKLOAD,
    "ano": FieldType.YEAR,
    "data_emissao": FieldType.ISSUED_AT,
    "instituicao": FieldType.INSTITUTION,
    "signatario_nome": FieldType.SIGNATORY_NAME,
    "signatario_cargo": FieldType.SIGNATORY_TITLE,
    "codigo_validacao": FieldType.VERIFICATION_CODE,
}

# QR_CODE nao esta na lista, e nao por esquecimento: o QR e uma imagem, e uma
# imagem nao cabe no meio de uma frase. Ele continua sendo um elemento
# proprio, arrastavel e redimensionavel.


# Um placeholder dentro de uma frase quase sempre quer o dado cru; o mesmo
# dado como elemento SOLTO quer a unidade junto, senao um "08" flutua na
# pagina sem dizer o que e.
#
# Dai a unica excecao da tabela acima: o elemento Carga horaria imprime
# "08 horas", e `{{carga_horaria}}` imprime "08" — porque a frase que o usa
# ja escreve a palavra:
#
#     com carga horaria de {{carga_horaria}} horas.
#
# Sem isso essa frase sairia com "horas" duas vezes. O seletor da tela mostra
# o valor de exemplo de cada variavel, entao a diferenca fica visivel na hora
# de escrever, e nao so aqui.
FORMATADORES = {
    "carga_horaria": lambda texto: (texto or "").split(" ")[0],
}

# `{{ nome }}` com espacos tambem vale: quem digita a mao coloca espaco, e
# recusar por isso seria pedantismo que nao protege nada.
PADRAO = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

# Reconhece QUALQUER `{{...}}`, inclusive o que a regex de cima recusaria
# (`{{user.password}}`, `{{ 1+1 }}`). Serve para a mensagem de erro: dizer
# "variavel nao permitida: {{user.password}}" e util; ignorar em silencio
# faria o texto sair com chaves impressas no certificado.
PADRAO_LARGO = re.compile(r"\{\{[^}]*\}\}")

# Teto do texto. Nao e limite de banco — e limite de bom senso: um paragrafo
# de certificado tem duas ou tres linhas, e 2000 caracteres ja e dez vezes
# isso. O teto existe para que um POST montado a mao nao grave um romance
# que depois trava o auto-ajuste procurando um tamanho que caiba.
TAMANHO_MAXIMO_DO_TEXTO = 2000

# Quantidade de linhas explicitas. Mesmo raciocinio.
LINHAS_MAXIMAS = 20


class PlaceholderInvalido(ValueError):
    """O texto cita variaveis fora da lista branca."""

    def __init__(self, invalidos):
        self.invalidos = list(invalidos)
        super().__init__(
            "O texto contem variaveis nao permitidas: {}".format(
                ", ".join(self.invalidos)
            )
        )


# ---------------------------------------------------------------------------
# Validacao
# ---------------------------------------------------------------------------


def encontrar_invalidos(texto):
    """
    Os `{{...}}` do texto que nao estao na lista branca, como aparecem.

    Compara o conjunto largo com o reconhecido: o que sobra ou tem sintaxe
    que a lista nao aceita (`{{user.password}}`), ou tem sintaxe valida mas
    nome desconhecido (`{{senha}}`).
    """
    texto = texto or ""
    invalidos = []
    for ocorrencia in PADRAO_LARGO.findall(texto):
        casado = PADRAO.fullmatch(ocorrencia)
        if casado is None or casado.group(1) not in PLACEHOLDERS:
            invalidos.append(ocorrencia)
    return invalidos


def validar_texto(texto):
    """
    Devolve o texto pronto para gravar, ou levanta.

    Normaliza as quebras de linha para `\\n` — um textarea manda `\\r\\n` no
    Windows, e o renderizador contaria o `\\r` como caractere a desenhar.
    """
    # str() antes de tudo: o texto chega de um corpo JSON, e JSON tem
    # numeros, listas e nulos. Sem a coercao, `{"content": 5}` chamaria
    # .replace() num inteiro e devolveria 500 no lugar de uma recusa.
    texto = str(texto or "").replace("\r\n", "\n").replace("\r", "\n")

    if len(texto) > TAMANHO_MAXIMO_DO_TEXTO:
        raise ValueError(
            "O texto passa de {} caracteres.".format(TAMANHO_MAXIMO_DO_TEXTO)
        )
    if texto.count("\n") + 1 > LINHAS_MAXIMAS:
        raise ValueError(
            "O texto passa de {} linhas.".format(LINHAS_MAXIMAS)
        )

    invalidos = encontrar_invalidos(texto)
    if invalidos:
        raise PlaceholderInvalido(invalidos)

    return texto


# ---------------------------------------------------------------------------
# Substituicao
# ---------------------------------------------------------------------------


def aplicar(texto, valores):
    """
    Troca cada `{{nome}}` pelo valor correspondente.

    `valores` e o mesmo dicionario {field_type: texto} que o renderizador ja
    recebe. Um placeholder sem valor — carga horaria num certificado da
    versao 1, por exemplo — vira string vazia, e nao a palavra "None" nem as
    chaves impressas.

    O que a substituicao produz NAO e reprocessado. Se o nome do aluno fosse
    literalmente "{{ano}}", o resultado sairia com as chaves visiveis em vez
    de virar 2026 — que e o comportamento certo: o dado do aluno e dado, e
    nao marcacao.
    """
    if not texto:
        return ""

    def trocar(casado):
        nome = casado.group(1)
        tipo = PLACEHOLDERS.get(nome)
        if tipo is None:
            # Nao acontece com texto gravado pelo servico, que valida antes.
            # Acontece se alguem editar o JSON no banco a mao.
            return ""
        valor = (valores or {}).get(tipo)
        valor = "" if valor is None else str(valor)
        formatador = FORMATADORES.get(nome)
        return formatador(valor) if formatador else valor

    return PADRAO.sub(trocar, texto)


def valor_de_exemplo(nome, valores):
    """O que uma variavel produz, para o seletor da tela mostrar."""
    return aplicar("{{%s}}" % nome, valores)


def opcoes_para_o_editor(valores=None):
    """
    A lista que alimenta o seletor "Inserir variavel" da tela.

    Devolve (placeholder, rotulo, exemplo). O rotulo e o mesmo do tipo de
    campo — a tela nao inventa um segundo nome para a mesma coisa — e o
    exemplo evita a duvida que a excecao da carga horaria criaria.
    """
    return [
        (
            "{{%s}}" % nome,
            FieldType(tipo).label,
            valor_de_exemplo(nome, valores) if valores else "",
        )
        for nome, tipo in PLACEHOLDERS.items()
    ]
