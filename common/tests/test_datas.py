"""
Datas por extenso.

Estes testes existem por um motivo concreto: `strftime("%B")` devolveria
"September" no servidor, porque o locale de um Ubuntu recem-instalado e `C`.
Um certificado oficial com o mes em ingles nao seria um detalhe de
formatacao — seria um documento errado, impresso e assinado.
"""

from datetime import date, datetime, timedelta, timezone as tz

import pytest
from django.utils import timezone

from common.datas import MESES, data_curta, data_por_extenso


def test_o_formato_pedido():
    assert data_por_extenso(date(2026, 9, 2)) == "02 de setembro de 2026"


def test_o_dia_vem_com_zero_a_esquerda():
    assert data_por_extenso(date(2026, 1, 5)).startswith("05 de janeiro")


def test_todos_os_doze_meses_em_portugues():
    nomes = [data_por_extenso(date(2026, mes, 1)).split(" de ")[1] for mes in range(1, 13)]

    assert nomes == list(MESES[1:])
    assert "March" not in nomes
    assert "março" in nomes


def test_nao_depende_do_locale_do_sistema():
    """
    O contraponto direto de `%B`.

    Se um dia alguem trocar a tabela por strftime, este teste cai em
    qualquer maquina cujo locale nao seja pt_BR — que e o caso do servidor.
    """
    esperado = data_por_extenso(date(2026, 3, 14))

    assert esperado == "14 de março de 2026"
    assert date(2026, 3, 14).strftime("%d de %B de %Y") != esperado


def test_ausencia_devolve_vazio_e_nunca_a_palavra_none():
    assert data_por_extenso(None) == ""
    assert data_curta(None) == ""


def test_data_curta():
    assert data_curta(date(2026, 9, 2)) == "02/09/2026"


@pytest.mark.parametrize("hora", [22, 23])
def test_datetime_a_noite_usa_o_fuso_da_aplicacao(settings, hora):
    """
    Uma correcao fechada as 22h de Sumare e do dia seguinte em UTC.

    Formatar o instante cru imprimiria um dia a mais em toda avaliacao
    fechada a noite — e as aulas do CPO sao a noite.
    """
    settings.TIME_ZONE = "America/Sao_Paulo"
    fechado_local = timezone.make_aware(datetime(2026, 9, 2, hora, 30))
    em_utc = fechado_local.astimezone(tz.utc)

    assert em_utc.day == 3
    assert data_por_extenso(fechado_local) == "02 de setembro de 2026"
    assert data_por_extenso(em_utc) == "02 de setembro de 2026"


def test_o_mesmo_instante_sempre_produz_o_mesmo_texto():
    instante = timezone.now()

    assert data_por_extenso(instante) == data_por_extenso(instante)
    assert data_por_extenso(instante) != data_por_extenso(instante + timedelta(days=1))
