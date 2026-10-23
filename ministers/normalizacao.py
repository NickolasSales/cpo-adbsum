"""
Normalizacao dos dados ministeriais.

Um lugar so, usado pelo cadastro manual, pela edicao, pela importacao e pelo
login. Se cada caminho limpasse o texto do seu jeito, a mesma inscricao
digitada no formulario e lida da planilha poderia virar dois valores
diferentes — e a unicidade do banco deixaria de proteger alguma coisa.
"""

import re
import unicodedata
from decimal import Decimal

# Matriculas e inscricoes: digitos e letras, com os separadores que costumam
# aparecer em numeros de registro. Espaco nunca: ele e removido antes.
PADRAO_REGISTRO = re.compile(r"^[0-9A-Z./-]+$")
TAMANHO_MAXIMO_REGISTRO = 30


def normalizar_nome(valor):
    """
    Nome limpo: sem espacos nas pontas e sem espacos repetidos.

    A caixa e a acentuacao sao preservadas de proposito. A relacao oficial
    vem toda em maiusculas, mas converter tudo para maiusculas (ou para
    "Title Case") destruiria nomes que alguem ja corrigiu a mao — "DE", "da",
    "McDonald" — e nao ha regra automatica que acerte todos.
    """
    return " ".join(str(valor or "").split())


def normalizar_registro(valor):
    """
    Matricula ou inscricao como texto.

    Guardada como texto, e nao como inteiro, porque zeros a esquerda fazem
    parte do numero quando existem: "007764" e "7764" sao registros
    diferentes, e um IntegerField apagaria a diferenca em silencio.

    O Excel entrega numero como int ou float (132921.0). Os dois viram o
    texto do inteiro. Espacos — nas pontas e no meio, como em "132 921" — sao
    removidos. Letras ficam em maiusculas, para que a comparacao no login nao
    dependa de como a pessoa digitou.
    """
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return ""
    if isinstance(valor, float):
        if valor.is_integer():
            valor = int(valor)
    elif isinstance(valor, Decimal):
        if valor == valor.to_integral_value():
            valor = int(valor)
    texto = "".join(str(valor).split())
    return texto.upper()


def registro_valido(texto):
    """Se o registro ja normalizado tem formato aceitavel."""
    return (
        bool(texto)
        and len(texto) <= TAMANHO_MAXIMO_REGISTRO
        and bool(PADRAO_REGISTRO.match(texto))
    )


def sem_acento(texto):
    normalizado = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def chave_de_texto(texto):
    """
    Forma canonica para comparar textos livres: sem acento, minuscula, so
    letras e digitos separados por um espaco.

    Usada para casar cabecalhos de planilha, codigos de titulo e para avisar
    sobre homonimos. Nunca e gravada.
    """
    simples = sem_acento(texto).lower()
    simples = re.sub(r"[^a-z0-9]+", " ", simples)
    return " ".join(simples.split())


def chave_de_nome(nome):
    """Chave para detectar o mesmo nome escrito com caixa ou acento diferente."""
    return chave_de_texto(nome)
