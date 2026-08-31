"""
A nota: calculo, precisao e aprovacao.

O teste que da nome a este arquivo e o de 7.996.

Uma prova em que o aluno tira 7.996 aparece na tela como "8,00" — duas casas,
como toda nota do sistema. A nota minima e 8,00. Se a comparacao usasse o
valor exibido, esse aluno seria aprovado, e a tela mostraria "8,00 · Aprovado"
sem nada que revelasse o erro. Ele passaria por causa de um arredondamento.

Por isso final_score guarda seis casas decimais e a comparacao acontece antes
de qualquer formatacao. O helper de exibicao existe justamente para que o
arredondamento more num lugar so, longe de quem decide.

O outro fio que atravessa o arquivo e Decimal. Com float, 0.1 + 0.2 nao da
0.3, e uma prova de dez questoes de 0,1 ponto somaria 0,9999999999999999 —
reprovando quem acertou tudo.
"""

from decimal import Decimal

import pytest

from exams.services.grading import (
    ESCALA_DA_NOTA,
    calculate_final_score,
    nota_para_exibicao,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# O caso critico
# ---------------------------------------------------------------------------


def test_nota_que_arredonda_para_o_minimo_ainda_reprova():
    """
    7.996 exibido como 8,00, nota minima 8,00, resultado REPROVADO.

    E o teste exigido pela especificacao, e o unico lugar do sistema onde a
    diferenca entre 7.996 e 8.00 muda a vida de alguem.
    """
    nota = calculate_final_score(Decimal("7.996"), Decimal("10.00"))
    minima = Decimal("8.00")

    assert nota == Decimal("7.996000")
    assert nota_para_exibicao(nota) == "8,00"
    assert nota < minima, "7.996 nao pode ser tratado como 8.00"


def test_nota_exatamente_no_minimo_aprova():
    nota = calculate_final_score(Decimal("8.00"), Decimal("10.00"))
    minima = Decimal("8.00")

    assert nota == Decimal("8.000000")
    assert nota >= minima


def test_nota_pouco_acima_do_minimo_aprova_e_exibe_o_minimo():
    """
    O espelho do caso critico: 8.004 tambem aparece como "8,00", mas aprova.

    Os dois juntos provam que a exibicao nao decide nada — duas notas que a
    tela mostra igual tem resultados opostos, e cada um esta correto.
    """
    nota = calculate_final_score(Decimal("8.004"), Decimal("10.00"))
    minima = Decimal("8.00")

    assert nota_para_exibicao(nota) == "8,00"
    assert nota >= minima


# ---------------------------------------------------------------------------
# A formula
# ---------------------------------------------------------------------------


def test_a_formula_e_pontos_sobre_total_vezes_dez():
    assert calculate_final_score(Decimal("5.00"), Decimal("10.00")) == Decimal(
        "5.000000"
    )
    assert calculate_final_score(Decimal("10.00"), Decimal("10.00")) == Decimal(
        "10.000000"
    )
    assert calculate_final_score(Decimal("0.00"), Decimal("10.00")) == Decimal(
        "0.000000"
    )


def test_a_escala_nao_depende_do_total_da_prova():
    """
    Metade dos pontos e cinco, em qualquer prova.

    Uma prova de 40 pontos e uma de 10 precisam produzir a mesma nota para o
    mesmo desempenho relativo — senao a nota minima significaria coisas
    diferentes em provas diferentes.
    """
    assert calculate_final_score(
        Decimal("20.00"), Decimal("40.00")
    ) == calculate_final_score(Decimal("5.00"), Decimal("10.00"))


def test_prova_com_total_zero_devolve_zero_em_vez_de_estourar():
    """
    Nao deveria acontecer: publicar exige pontuacao positiva.

    Mas se acontecesse, uma divisao por zero derrubaria a correcao inteira —
    inclusive a das outras tentativas do lote. Zero e um resultado ruim que
    aparece na tela; ZeroDivisionError e um erro 500 que ninguem entende.
    """
    assert calculate_final_score(Decimal("0.00"), Decimal("0.00")) == Decimal("0.00")
    assert calculate_final_score(Decimal("5.00"), None) == Decimal("0.00")


# ---------------------------------------------------------------------------
# Decimal, nao float
# ---------------------------------------------------------------------------


def test_o_resultado_e_decimal():
    nota = calculate_final_score(Decimal("7.00"), Decimal("10.00"))
    assert isinstance(nota, Decimal)
    assert not isinstance(nota, float)


def test_soma_de_decimos_nao_perde_precisao():
    """
    O erro classico de float.

    Dez questoes de 0,1 ponto somam exatamente 1,0 em Decimal. Em float
    somariam 0.9999999999999999, e o aluno que acertou tudo tiraria 9,99.
    """
    total = sum((Decimal("0.10") for _ in range(10)), Decimal("0"))
    assert total == Decimal("1.00")
    assert calculate_final_score(total, Decimal("1.00")) == Decimal("10.000000")


def test_dizimo_periodico_nao_estoura_nem_perde_a_escala():
    """
    Um ponto em tres vale 3,333333... A nota guarda seis casas e para ali.
    """
    nota = calculate_final_score(Decimal("1.00"), Decimal("3.00"))
    assert nota == Decimal("3.333333")
    assert nota_para_exibicao(nota) == "3,33"


def test_a_escala_e_dez():
    """Constante explicita, para que ninguem a troque por 100 sem perceber."""
    assert ESCALA_DA_NOTA == Decimal("10")


# ---------------------------------------------------------------------------
# Exibicao
# ---------------------------------------------------------------------------


def test_exibicao_usa_virgula_e_duas_casas():
    assert nota_para_exibicao(Decimal("8.000000")) == "8,00"
    assert nota_para_exibicao(Decimal("7.500000")) == "7,50"
    assert nota_para_exibicao(Decimal("9.250000")) == "9,25"
    assert nota_para_exibicao(Decimal("10.000000")) == "10,00"


def test_exibicao_de_nota_ausente_e_vazia():
    """Tentativa nao corrigida nao tem nota, e a tela nao pode inventar 0,00."""
    assert nota_para_exibicao(None) == ""


def test_exibicao_arredonda_para_cima_no_meio():
    """ROUND_HALF_UP, que e o que a pessoa espera ao ler uma nota."""
    assert nota_para_exibicao(Decimal("8.005000")) == "8,01"
    assert nota_para_exibicao(Decimal("8.004999")) == "8,00"


def test_exibicao_nunca_e_usada_para_comparar():
    """
    Guarda de projeto.

    nota_para_exibicao devolve str, e nao Decimal. Isso e deliberado: uma
    string nao pode ser comparada com a nota minima por engano — a tentativa
    levantaria TypeError em vez de aprovar alguem em silencio.
    """
    exibida = nota_para_exibicao(Decimal("7.996000"))
    assert isinstance(exibida, str)

    with pytest.raises(TypeError):
        assert exibida >= Decimal("8.00")
