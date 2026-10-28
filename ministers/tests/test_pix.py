"""Pix Copia e Cola (BR Code) e QR Code: regras puras, sem banco."""

from decimal import Decimal

import pytest

from ministers import pix

CHAVE = "pix.teste@example.com"
NOME = "AD BRAS SUMARE TESTE"
CIDADE = "SUMARE"
TXID = "CPO202610ABCDE12345"


def montar(**extra):
    dados = {"chave": CHAVE, "nome": NOME, "cidade": CIDADE, "valor": Decimal("35.00"), "txid": TXID}
    dados.update(extra)
    return pix.montar_payload(**dados)


# ---------------------------------------------------------------------------
# CRC
# ---------------------------------------------------------------------------


def test_crc_valor_de_verificacao_do_algoritmo():
    # CRC-16/CCITT-FALSE de "123456789" e 0x29B1 por definicao.
    assert pix.crc16("123456789") == "29B1"


def test_crc_do_exemplo_do_manual_do_br_code():
    exemplo = (
        "00020126580014br.gov.bcb.pix0136123e4567-e12b-12d1-a456-426655440000"
        "5204000053039865802BR5913Fulano de Tal6008BRASILIA62070503***6304"
    )
    assert pix.crc16(exemplo) == "1D3D"


def test_crc_sempre_tem_quatro_hexadecimais_maiusculos():
    for texto in ("", "a", "0002010102", "x" * 300):
        crc = pix.crc16(texto)
        assert len(crc) == 4 and crc == crc.upper()
        int(crc, 16)


def test_payload_termina_com_o_crc_dele_mesmo():
    payload = montar()
    assert payload[-8:-4] == "6304"
    assert pix.crc16(payload[:-4]) == payload[-4:]


def test_qualquer_caractere_alterado_quebra_o_crc():
    adulterado = montar().replace("35.00", "36.00")
    with pytest.raises(pix.PayloadInvalido):
        pix.ler(adulterado)


# ---------------------------------------------------------------------------
# Estrutura
# ---------------------------------------------------------------------------


def test_estrutura_tlv_e_campos_obrigatorios():
    campos = pix.ler(montar())
    assert campos["00"] == "01"
    assert campos["26"]["00"] == "br.gov.bcb.pix"
    assert campos["26"]["01"] == CHAVE
    assert campos["52"] == "0000"
    assert campos["53"] == "986"
    assert campos["54"] == "35.00"
    assert campos["58"] == "BR"
    assert campos["59"] == NOME
    assert campos["60"] == CIDADE
    assert campos["62"] == {"05": TXID}
    assert list(campos) == ["00", "26", "52", "53", "54", "58", "59", "60", "62", "63"]


def test_payload_e_deterministico():
    assert montar() == montar()


def test_payload_conhecido_campo_a_campo():
    esperado = (
        "000201"
        "2643" "0014br.gov.bcb.pix" "0121pix.teste@example.com"
        "52040000" "5303986" "540535.00" "5802BR"
        "5920AD BRAS SUMARE TESTE" "6006SUMARE"
        "6223" "0519CPO202610ABCDE12345"
        "6304"
    )
    payload = montar()
    assert payload[:-4] == esperado
    assert payload[-4:] == pix.crc16(esperado)


@pytest.mark.parametrize(
    "valor, texto",
    [(Decimal("35"), "35.00"), (Decimal("35.5"), "35.50"), ("1234.56", "1234.56"), (Decimal("0.01"), "0.01")],
)
def test_valor_com_ponto_e_duas_casas(valor, texto):
    assert pix.ler(montar(valor=valor))["54"] == texto


def test_payload_muda_quando_o_valor_muda():
    assert montar(valor=Decimal("35.00")) != montar(valor=Decimal("40.00"))


def test_txid_vai_no_campo_62_05():
    assert pix.ler(montar(txid="CPO202611ZZZZZ99999"))["62"]["05"] == "CPO202611ZZZZZ99999"


@pytest.mark.parametrize("valor", [Decimal("0"), Decimal("-1"), "abc", None, 35.0])
def test_valor_invalido_e_recusado(valor):
    with pytest.raises(pix.PayloadInvalido):
        montar(valor=valor)


@pytest.mark.parametrize("txid", ["", "COM ESPACO", "ACENTUAÇÃO", "X" * 26, "***", "a-b"])
def test_txid_invalido_e_recusado(txid):
    with pytest.raises(pix.PayloadInvalido):
        montar(txid=txid)


def test_nome_e_cidade_precisam_estar_normalizados_e_no_limite():
    for extra in ({"nome": "Igreja com acentuação"}, {"nome": "X" * 26}, {"cidade": "Y" * 16}, {"nome": ""}):
        with pytest.raises(pix.PayloadInvalido):
            montar(**extra)


def test_descricao_entra_quando_cabe_e_sai_quando_nao_cabe():
    com = pix.ler(montar(descricao="Mensalidade 10 2026"))
    assert com["26"]["02"] == "MENSALIDADE 10 2026"
    sem = pix.ler(montar(chave="a" * 60 + "@example.com", descricao="Mensalidade 10 2026"))
    assert "02" not in sem["26"]


def test_decodificar_recusa_tamanho_que_nao_fecha():
    with pytest.raises(pix.PayloadInvalido):
        pix.decodificar("0005abc")


def test_texto_do_br_code():
    assert pix.texto_do_br_code("  Igreja Assembléia  de Deus — Brás ") == "IGREJA ASSEMBLEIA DE DEUS BRAS"
    assert pix.texto_do_br_code("Sumaré/SP") == "SUMARE SP"
    assert pix.texto_do_br_code(None) == ""


# ---------------------------------------------------------------------------
# Chave
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tipo, entrada, saida",
    [
        ("CPF", "529.982.247-25", "52998224725"),
        ("CNPJ", "11.222.333/0001-81", "11222333000181"),
        ("CNPJ", "12.ABC.345/01DE-35", "12ABC34501DE35"),
        ("EMAIL", "  Pix.Teste@Example.COM ", "pix.teste@example.com"),
        ("TELEFONE", "(19) 99999-8888", "+5519999998888"),
        ("TELEFONE", "+55 19 3333-4444", "+551933334444"),
        ("ALEATORIA", "123E4567-E89B-12D3-A456-426614174000", "123e4567-e89b-12d3-a456-426614174000"),
    ],
)
def test_chave_valida_e_normalizada(tipo, entrada, saida):
    assert pix.normalizar_chave(tipo, entrada) == saida


@pytest.mark.parametrize(
    "tipo, entrada",
    [
        ("CPF", "529.982.247-24"),
        ("CPF", "111.111.111-11"),
        ("CPF", "123"),
        ("CNPJ", "11.222.333/0001-80"),
        ("CNPJ", "00000000000000"),
        ("EMAIL", "sem-arroba"),
        ("EMAIL", "a@b"),
        ("EMAIL", "x" * 70 + "@example.com"),
        ("TELEFONE", "123"),
        ("TELEFONE", "+1 202 555 0100"),
        ("ALEATORIA", "nao-e-uuid"),
        ("OUTRO", "qualquer"),
        ("EMAIL", ""),
    ],
)
def test_chave_invalida_e_recusada(tipo, entrada):
    with pytest.raises(pix.PayloadInvalido):
        pix.normalizar_chave(tipo, entrada)


# ---------------------------------------------------------------------------
# QR Code
# ---------------------------------------------------------------------------


def test_qr_e_quadrado_e_deterministico():
    matriz = pix.matriz_qr(montar())
    assert len(matriz) == len(matriz[0]) >= 21
    assert matriz == pix.matriz_qr(montar())


def test_qr_muda_quando_o_payload_muda():
    assert pix.matriz_qr(montar()) != pix.matriz_qr(montar(valor=Decimal("40.00")))


def test_qr_contem_exatamente_o_payload(monkeypatch):
    """O QR recebe o payload e so ele: nada de URL, id ou sessao."""
    recebido = []
    original = pix.qrcode.QRCode.add_data

    def espiao(self, dados, *args, **kwargs):
        recebido.append(dados)
        return original(self, dados, *args, **kwargs)

    monkeypatch.setattr(pix.qrcode.QRCode, "add_data", espiao)
    payload = montar()
    pix.svg_qr(payload)
    assert recebido == [payload]


def test_svg_nao_tem_texto_nem_script():
    svg = pix.svg_qr(montar())
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "<script" not in svg and "<text" not in svg and CHAVE not in svg
