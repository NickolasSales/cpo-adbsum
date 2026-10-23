"""
Responsividade do Controle de Ministros: as condicoes estruturais.

Mesmo limite de common/tests/test_responsivo.py: sem navegador na suite, o
que se garante aqui e a estrutura que evita barra horizontal — a conferencia
visual em 360, 390, 768, 1366 e 1920 px foi feita com navegador real e esta
registrada no README.
"""

import io
import pathlib
import re

import pytest

from ministers.tests.conftest import URL_LOGIN, URL_PAINEL, URL_PERFIL

pytestmark = pytest.mark.django_db

CSS = pathlib.Path(__file__).resolve().parents[2] / "static/css/app.css"


def test_portal_tem_viewport_e_nao_usa_lateral(ministro_client_logado):
    for rota in (URL_PAINEL, URL_PERFIL):
        html = ministro_client_logado.get(rota).content.decode()
        assert 'name="viewport" content="width=device-width, initial-scale=1"' in html
        assert "cpo-lateral" not in html
        assert "Portal do Ministro" in html


def test_login_do_portal_usa_o_layout_mobile_first(client):
    html = client.get(URL_LOGIN).content.decode()
    assert 'class="cpo-login"' in html
    assert "cpo-botao-bloco" in html
    assert 'inputmode="numeric"' in html


def test_dados_do_portal_comecam_em_uma_coluna():
    texto = io.open(CSS, encoding="utf-8").read()
    base = re.search(r"\.cpo-portal__dados \{([^}]*)\}", texto).group(1)
    assert "grid-template-columns: 1fr;" in base
    assert "overflow-wrap: anywhere" in texto


def test_lista_administrativa_vira_cartoes(admin_client_logado, ministro_com_acesso):
    html = admin_client_logado.get("/admin-panel/ministros/cadastro/").content.decode()
    assert "cpo-lista--cartoes" in html
    for rotulo in ("Campo", "CONAMAD", "Situação", "Acesso"):
        assert 'data-rotulo="{}"'.format(rotulo) in html
    assert ">Abrir<" in html
