"""
Pix Copia e Cola (BR Code estatico) e o QR Code que o representa.

Modulo puro: nao le banco, nao conhece mensalidade nem configuracao. Recebe
chave, recebedor, cidade, valor e TXID ja decididos e devolve o texto. Quem
decide o que entra e ministers.cobranca; quem grava e o snapshot.

O formato
---------
O BR Code e o padrao EMV de QR Code para pagamentos (EMV QRCPS-MPM), com o
perfil do Banco Central para o Pix. E uma sequencia de campos TLV:

    ID (2 digitos) + TAMANHO (2 digitos) + VALOR

Campos usados, na ordem exigida:

    00  versao do payload                "01"
    26  conta do recebedor (template)
          00  GUI                        "br.gov.bcb.pix"
          01  chave Pix
          02  descricao (opcional)       so entra se couber nos 99 do campo 26
    52  categoria do comerciante         "0000"
    53  moeda                            "986" (real)
    54  valor                            "35.00" — ponto, duas casas
    58  pais                             "BR"
    59  nome do recebedor                ate 25 caracteres
    60  cidade do recebedor              ate 15 caracteres
    62  dados adicionais (template)
          05  identificador (TXID)       ate 25 caracteres, [A-Za-z0-9]
    63  CRC                              4 hexadecimais

O CRC e o CRC-16/CCITT-FALSE (polinomio 0x1021, valor inicial 0xFFFF) sobre
o payload inteiro ATE "6304" inclusive. Os testes conferem a implementacao
com o valor de verificacao do algoritmo e com o exemplo do manual do BR Code.

Sem biblioteca externa de Pix de proposito: sao sessenta linhas de regra
publica e estavel, testaveis campo a campo, contra uma dependencia a mais no
caminho do dinheiro. O QR Code usa o `qrcode`, que o projeto ja tem.

Nome e cidade saem sem acento e em maiusculas. O padrao aceita ASCII
imprimivel, mas aplicativos de banco recusam ou mostram mal caractere fora de
A-Z, 0-9 e espaco — e um payload que um banco rejeita e uma cobranca
quebrada.
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation

import qrcode

GUI_PIX = "br.gov.bcb.pix"
MOEDA_REAL = "986"
PAIS = "BR"
CATEGORIA = "0000"

TAMANHO_MAXIMO_CAMPO = 99
TAMANHO_MAXIMO_NOME = 25
TAMANHO_MAXIMO_CIDADE = 15
TAMANHO_MAXIMO_TXID = 25
TAMANHO_MAXIMO_CHAVE = 77
TAMANHO_MAXIMO_VALOR = 13

TXID_VALIDO = re.compile(r"^[A-Za-z0-9]{1,25}$")


class PayloadInvalido(ValueError):
    """Dado que produziria um BR Code invalido. Mensagem legivel."""


# ---------------------------------------------------------------------------
# CRC
# ---------------------------------------------------------------------------


def crc16(dados):
    """CRC-16/CCITT-FALSE em 4 hexadecimais maiusculos."""
    if isinstance(dados, str):
        dados = dados.encode("ascii")
    resto = 0xFFFF
    for byte in dados:
        resto ^= byte << 8
        for _ in range(8):
            if resto & 0x8000:
                resto = ((resto << 1) ^ 0x1021) & 0xFFFF
            else:
                resto = (resto << 1) & 0xFFFF
    return "{:04X}".format(resto)


# ---------------------------------------------------------------------------
# TLV
# ---------------------------------------------------------------------------


def campo(identificador, valor):
    if len(valor) > TAMANHO_MAXIMO_CAMPO:
        raise PayloadInvalido("Campo {} com mais de 99 caracteres.".format(identificador))
    return "{}{:02d}{}".format(identificador, len(valor), valor)


def decodificar(texto):
    """
    Lista de (id, valor) de um nivel TLV. Levanta PayloadInvalido se o
    tamanho declarado de algum campo nao fechar com o texto.
    """
    campos = []
    posicao = 0
    while posicao < len(texto):
        cabecalho = texto[posicao : posicao + 4]
        if len(cabecalho) < 4 or not cabecalho.isdigit():
            raise PayloadInvalido("Estrutura TLV inválida.")
        tamanho = int(cabecalho[2:])
        valor = texto[posicao + 4 : posicao + 4 + tamanho]
        if len(valor) != tamanho:
            raise PayloadInvalido("Estrutura TLV inválida.")
        campos.append((cabecalho[:2], valor))
        posicao += 4 + tamanho
    return campos


def ler(payload):
    """
    Confere CRC e estrutura e devolve os campos como dicionario, com os
    templates 26 e 62 abertos: {"26": {"00": ..., "01": ...}, "54": ...}.
    """
    if len(payload) < 8 or payload[-8:-4] != "6304":
        raise PayloadInvalido("Payload sem o campo de CRC no final.")
    if crc16(payload[:-4]) != payload[-4:]:
        raise PayloadInvalido("CRC do payload não confere.")
    resultado = {}
    for identificador, valor in decodificar(payload):
        if identificador in resultado:
            raise PayloadInvalido("Campo {} repetido.".format(identificador))
        if identificador in ("26", "62"):
            valor = dict(decodificar(valor))
        resultado[identificador] = valor
    return resultado


# ---------------------------------------------------------------------------
# Normalizacao
# ---------------------------------------------------------------------------


def texto_do_br_code(texto):
    """'Sumaré  - SP' -> 'SUMARE SP'. Sem acento, so A-Z, 0-9 e espaco."""
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = texto.encode("ascii", "ignore").decode("ascii").upper()
    texto = re.sub(r"[^A-Z0-9 ]+", " ", texto)
    return " ".join(texto.split())


def valor_do_br_code(valor):
    """Decimal('35') -> '35.00'. Recusa zero, negativo, float e lixo."""
    if isinstance(valor, float):
        raise PayloadInvalido("Valor em float não é aceito.")
    try:
        valor = Decimal(str(valor)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise PayloadInvalido("Valor inválido.")
    if valor <= 0:
        raise PayloadInvalido("O valor precisa ser maior que zero.")
    texto = format(valor, "f")
    if len(texto) > TAMANHO_MAXIMO_VALOR:
        raise PayloadInvalido("Valor acima do limite do Pix.")
    return texto


# ---------------------------------------------------------------------------
# Chave
# ---------------------------------------------------------------------------

TIPOS_DE_CHAVE = ("CPF", "CNPJ", "EMAIL", "TELEFONE", "ALEATORIA")

EMAIL = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")
ALEATORIA = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CNPJ = re.compile(r"^[0-9A-Z]{12}[0-9]{2}$")


def _digito(valores, pesos):
    resto = sum(v * p for v, p in zip(valores, pesos)) % 11
    return 0 if resto < 2 else 11 - resto


def cpf_valido(cpf):
    if len(cpf) != 11 or not cpf.isdigit() or len(set(cpf)) == 1:
        return False
    numeros = [int(c) for c in cpf]
    d1 = _digito(numeros[:9], range(10, 1, -1))
    d2 = _digito(numeros[:9] + [d1], range(11, 1, -1))
    return numeros[9:] == [d1, d2]


def cnpj_valido(cnpj):
    """
    Numerico e o alfanumerico da Receita (a partir de 07/2026): cada
    caractere vale o codigo ASCII menos 48, entao '0'-'9' continuam valendo
    0-9 e o calculo antigo e um caso particular do novo.
    """
    if not CNPJ.match(cnpj) or len(set(cnpj)) == 1:
        return False
    valores = [ord(c) - 48 for c in cnpj]
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = _digito(valores[:12], pesos1)
    d2 = _digito(valores[:12] + [d1], [6] + pesos1)
    return valores[12:] == [d1, d2]


def normalizar_chave(tipo, chave):
    """
    A chave no formato em que o DICT a registra, ou PayloadInvalido.

        CPF / CNPJ    so os caracteres, sem ponto, barra ou traco
        EMAIL         minusculas
        TELEFONE      +55 seguido de DDD e numero: +5519999998888
        ALEATORIA     UUID em minusculas
    """
    chave = (chave or "").strip()
    if tipo == "CPF":
        chave = re.sub(r"[.\-\s]", "", chave)
        if not cpf_valido(chave):
            raise PayloadInvalido("CPF inválido.")
    elif tipo == "CNPJ":
        chave = re.sub(r"[.\-/\s]", "", chave).upper()
        if not cnpj_valido(chave):
            raise PayloadInvalido("CNPJ inválido.")
    elif tipo == "EMAIL":
        chave = chave.lower()
        if len(chave) > TAMANHO_MAXIMO_CHAVE or not EMAIL.match(chave):
            raise PayloadInvalido("E-mail inválido para chave Pix.")
    elif tipo == "TELEFONE":
        digitos = re.sub(r"\D", "", chave)
        if chave.startswith("+") and not digitos.startswith("55"):
            raise PayloadInvalido("Use um telefone brasileiro (+55).")
        if digitos.startswith("55") and len(digitos) in (12, 13):
            digitos = digitos[2:]
        if len(digitos) not in (10, 11) or digitos[0] == "0":
            raise PayloadInvalido("Telefone inválido. Informe DDD e número.")
        chave = "+55" + digitos
    elif tipo == "ALEATORIA":
        chave = chave.lower()
        if not ALEATORIA.match(chave):
            raise PayloadInvalido("Chave aleatória inválida.")
    else:
        raise PayloadInvalido("Tipo de chave Pix inválido.")
    return chave


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------


def montar_payload(*, chave, nome, cidade, valor, txid, descricao=""):
    """
    O Pix Copia e Cola, com CRC. Deterministico: os mesmos argumentos
    produzem sempre o mesmo texto.

    `nome` e `cidade` precisam ja estar no formato do BR Code
    (texto_do_br_code) — aqui eles sao conferidos, nao corrigidos, para que
    nenhum corte silencioso mude o que o administrador cadastrou.
    """
    if not chave or len(chave) > TAMANHO_MAXIMO_CHAVE:
        raise PayloadInvalido("Chave Pix vazia ou longa demais.")
    if not chave.isascii() or any(c.isspace() for c in chave):
        raise PayloadInvalido("Chave Pix com caractere inválido.")
    if not nome or nome != texto_do_br_code(nome) or len(nome) > TAMANHO_MAXIMO_NOME:
        raise PayloadInvalido("Nome do recebedor inválido para o Pix.")
    if not cidade or cidade != texto_do_br_code(cidade) or len(cidade) > TAMANHO_MAXIMO_CIDADE:
        raise PayloadInvalido("Cidade do recebedor inválida para o Pix.")
    if not TXID_VALIDO.match(txid or ""):
        raise PayloadInvalido("TXID inválido.")

    conta = campo("00", GUI_PIX) + campo("01", chave)
    descricao = texto_do_br_code(descricao)
    if descricao:
        com_descricao = conta + campo("02", descricao)
        # A descricao e cortesia: com chave longa ela nao cabe, e sai.
        if len(com_descricao) <= TAMANHO_MAXIMO_CAMPO:
            conta = com_descricao

    payload = (
        campo("00", "01")
        + campo("26", conta)
        + campo("52", CATEGORIA)
        + campo("53", MOEDA_REAL)
        + campo("54", valor_do_br_code(valor))
        + campo("58", PAIS)
        + campo("59", nome)
        + campo("60", cidade)
        + campo("62", campo("05", txid))
        + "6304"
    )
    payload += crc16(payload)
    # Confere o proprio resultado: um payload que nao se le de volta nunca
    # chega ao banco.
    ler(payload)
    return payload


# ---------------------------------------------------------------------------
# QR Code
# ---------------------------------------------------------------------------


def matriz_qr(payload):
    """
    Os modulos do QR Code (lista de linhas de booleanos), SEM a zona de
    silencio. Correcao de erro M, a mesma do certificado: tolera sujeira e
    dobra no papel sem inflar demais o desenho.

    O conteudo e o payload e nada mais — nenhuma URL, id ou dado de sessao.
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        border=0,
    )
    qr.add_data(payload, optimize=0)
    qr.make(fit=True)
    return qr.get_matrix()


def svg_qr(payload, *, borda=4):
    """
    O QR em SVG, para a pagina. Um <path> so, com viewBox em modulos: escala
    para qualquer largura sem borrar. Nenhum texto do usuario entra no SVG.
    """
    matriz = matriz_qr(payload)
    lado = len(matriz) + 2 * borda
    trechos = []
    for y, linha in enumerate(matriz):
        x = 0
        while x < len(linha):
            if linha[x]:
                inicio = x
                while x < len(linha) and linha[x]:
                    x += 1
                trechos.append("M{} {}h{}v1h-{}z".format(inicio + borda, y + borda, x - inicio, x - inicio))
            else:
                x += 1
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {lado} {lado}" '
        'shape-rendering="crispEdges" role="img" aria-label="QR Code do Pix">'
        '<rect width="{lado}" height="{lado}" fill="#fff"/>'
        '<path fill="#000" d="{d}"/></svg>'
    ).format(lado=lado, d="".join(trechos))
