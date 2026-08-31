"""
Responsividade: o que da para testar sem navegador.

O limite deste arquivo, dito com todas as letras
------------------------------------------------
Nao existe navegador nesta suite, e nenhum teste aqui mede
document.body.scrollWidth. O que estes testes fazem e garantir as CONDICOES
estruturais que o overflow horizontal exigia — a regra que o causava nao esta
mais la, e as que o previnem estao. A confirmacao visual em cada largura
continua sendo trabalho de quem tem uma tela.

A causa raiz que motivou tudo
-----------------------------
O <main> do painel administrativo carregava, ao mesmo tempo:

    .container-fluid            width: 100%
    .cpo-conteudo--deslocado    margin-left: 250px   (>= 992px)

Margem fica FORA da caixa mesmo com box-sizing: border-box. A caixa de margem
media 100% + 250px, ou seja, a pagina inteira era 250px mais larga que a
viewport — em TODA tela administrativa. Nenhum .table-responsive resolvia,
porque o estouro nascia acima dele.

A correcao trocou isso por um shell flex com min-width: 0 no conteudo. Esse
min-width e o detalhe que sustenta o resto: por padrao um item flex nao
encolhe abaixo da largura natural do seu conteudo, entao uma tabela larga
empurraria a pagina em vez de rolar dentro do proprio cartao.
"""

import io
import pathlib
import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

RAIZ = pathlib.Path(__file__).resolve().parents[2]
CSS = RAIZ / "static/css/app.css"


def css():
    return io.open(CSS, encoding="utf-8").read()


def bloco(texto, seletor):
    """O corpo de uma regra CSS, ou string vazia."""
    achado = re.search(
        re.escape(seletor) + r"\s*\{([^}]*)\}", texto, re.DOTALL
    )
    return achado.group(1) if achado else ""


# ---------------------------------------------------------------------------
# A regra que causava o estouro
# ---------------------------------------------------------------------------


def test_a_margem_que_estourava_a_pagina_nao_existe_mais():
    """
    Guarda contra a regressao mais provavel desta etapa.

    Se alguem reintroduzir margin-left na largura da lateral para "empurrar o
    conteudo", a barra horizontal volta em todas as telas administrativas.
    """
    texto = css()

    assert "cpo-conteudo--deslocado" not in texto
    assert "margin-left: var(--cpo-largura-lateral)" not in texto


def test_o_conteudo_pode_encolher():
    """
    min-width: 0 e o que autoriza o item flex a ficar menor que o conteudo.

    Sem ele, uma tabela larga empurra a pagina em vez de rolar dentro do
    cartao — o sintoma volta, com outra causa.
    """
    texto = css()
    regra = bloco(texto, ".cpo-conteudo")

    assert "min-width: 0" in regra
    assert "max-width: 100%" in regra


def test_a_lateral_e_um_item_flex_de_largura_fixa():
    texto = css()
    regra = bloco(texto, ".cpo-lateral--fixa")

    assert "flex: 0 0 var(--cpo-largura-lateral)" in regra
    # position: fixed tirava a lateral do fluxo e obrigava ao margin-left.
    assert "position: fixed" not in regra


def test_o_shell_do_painel_e_flex():
    texto = css()
    regra = bloco(texto, ".cpo-shell--painel")

    assert "display: flex" in regra


def test_o_overflow_do_body_e_rede_e_nao_a_correcao():
    """
    body { overflow-x } pode existir como ultima linha de defesa, mas nao como
    solucao — e a especificacao pede exatamente essa distincao.

    `clip` no lugar de `hidden` de proposito: `hidden` cria contexto de
    rolagem e quebraria o position: sticky do cronometro da prova e da coluna
    de acoes.
    """
    texto = css()

    assert "overflow-x: clip" in texto
    assert "overflow-x: hidden" not in texto


# ---------------------------------------------------------------------------
# Tabelas
# ---------------------------------------------------------------------------


def test_toda_tabela_administrativa_vira_cartao_no_celular():
    """
    Nenhuma listagem pode ficar de fora.

    Uma tabela sem .cpo-lista--cartoes continua sendo uma tabela de dez
    colunas num aparelho de 360px.
    """
    templates = RAIZ / "templates"
    faltando = []

    for caminho in templates.rglob("*.html"):
        texto = io.open(caminho, encoding="utf-8").read()
        if "cpo-tabela" in texto and "cpo-lista--cartoes" not in texto:
            faltando.append(caminho.name)

    assert not faltando, "tabelas sem versao de cartao: {}".format(faltando)


def test_a_coluna_de_acoes_gruda_na_direita_no_desktop():
    """
    O sintoma relatado: "a coluna de acoes desaparece".

    Sticky mantem os botoes visiveis mesmo quando a tabela rola dentro do
    cartao, em vez de exigir que o usuario arraste ate o fim.
    """
    texto = css()
    regra = bloco(texto, ".cpo-tabela .cpo-acoes")

    assert "position: sticky" in regra
    assert "right: 0" in regra
    # Fundo opaco, senao o conteudo passa por baixo e fica ilegivel.
    assert "background:" in regra


def test_no_celular_as_acoes_viram_faixa_no_rodape_do_cartao():
    texto = css()
    regra = bloco(texto, ".cpo-lista--cartoes td.cpo-acoes")

    assert "border-top" in regra


# ---------------------------------------------------------------------------
# As telas de fato renderizam com as classes certas
# ---------------------------------------------------------------------------


TELAS_ADMIN = [
    "admin_panel:dashboard",
    "admin_panel:student_list",
    "admin_panel:module_list",
    "admin_panel:enrollment_list",
    "admin_panel:exam_list",
    "admin_panel:correction_list",
    "admin_panel:grade_list",
]


@pytest.mark.parametrize("nome", TELAS_ADMIN)
def test_as_telas_administrativas_usam_o_shell_flex(admin_client_logado, nome):
    corpo = admin_client_logado.get(reverse(nome)).content.decode("utf-8")

    assert "cpo-shell--painel" in corpo
    assert "cpo-conteudo" in corpo
    assert "cpo-conteudo--deslocado" not in corpo


@pytest.mark.parametrize("nome", TELAS_ADMIN)
def test_as_telas_administrativas_tem_menu_de_celular(admin_client_logado, nome):
    """
    O hamburger e o offcanvas precisam existir em toda tela.

    Sem eles, um aparelho de 360px perde a navegacao inteira — a lateral fixa
    esta escondida por d-none d-lg-flex.
    """
    corpo = admin_client_logado.get(reverse(nome)).content.decode("utf-8")

    assert "cpo-hamburguer" in corpo
    assert 'data-bs-target="#menuAdmin"' in corpo
    assert 'id="menuAdmin"' in corpo


def test_o_menu_de_celular_tem_os_mesmos_itens_do_desktop(admin_client_logado):
    """
    O offcanvas inclui o mesmo _menu.html da lateral.

    Se um dia forem dois templates, um item novo entraria so num deles e o
    usuario de celular perderia a tela sem ninguem perceber.
    """
    corpo = admin_client_logado.get(
        reverse("admin_panel:dashboard")
    ).content.decode("utf-8")

    for item in ("Alunos", "Modulos", "Matriculas", "Provas", "Correcoes", "Notas"):
        # Uma vez na lateral, outra no offcanvas.
        assert corpo.count(">{}</a>".format(item)) == 2


def test_as_listagens_marcam_a_celula_de_titulo_e_as_acoes(
    admin_client_logado, student_user
):
    """Com pelo menos uma linha: numa lista vazia nao ha celula para marcar."""
    corpo = admin_client_logado.get(
        reverse("admin_panel:student_list")
    ).content.decode("utf-8")

    assert "cpo-lista--cartoes" in corpo
    assert "cpo-celula-titulo" in corpo
    assert "cpo-acoes" in corpo


def test_as_celulas_secundarias_tem_rotulo_para_o_celular():
    """
    Sem o cabecalho da tabela, um valor solto nao diz o que e.

    data-rotulo vira o prefixo da linha no cartao. Colunas cujo valor ja se
    explica — o titulo do registro, as acoes — ficam sem ele de proposito.
    """
    caminho = RAIZ / "templates/admin_panel/students/list.html"
    texto = io.open(caminho, encoding="utf-8").read()

    for rotulo in ("E-mail", "Situacao", "Modulos", "Origem", "Criado em"):
        assert 'data-rotulo="{}"'.format(rotulo) in texto


# ---------------------------------------------------------------------------
# Formularios
# ---------------------------------------------------------------------------


def test_os_formularios_tem_largura_maxima_de_leitura():
    """
    Um campo de e-mail com 1600px de largura nao ajuda ninguem.
    """
    texto = css()

    assert "max-width: 720px" in bloco(texto, ".cpo-form-estreito")
    assert "max-width: 980px" in bloco(texto, ".cpo-form-medio")


def test_os_campos_nao_ultrapassam_o_container():
    texto = css()

    assert ".cpo-conteudo .form-control" in texto
    assert ".cpo-conteudo .form-select" in texto


def test_o_modal_cabe_na_tela():
    """
    Um modal de 500px fixos vaza num aparelho de 360px.
    """
    texto = css()
    regra = bloco(texto, ".modal-dialog")

    assert "calc(100% - 1rem)" in regra


def test_a_barra_de_acoes_quebra_linha():
    texto = css()
    regra = bloco(texto, ".cpo-barra-acoes")

    assert "flex-wrap: wrap" in regra


def test_o_alvo_de_toque_e_confortavel_no_celular():
    """
    Um botao de 40px com texto espremido erra mais do que acerta no dedo.
    """
    texto = css()
    assert "min-height: 2.75rem" in texto


# ---------------------------------------------------------------------------
# Area do aluno
# ---------------------------------------------------------------------------


def test_a_area_do_aluno_nao_usa_o_shell_do_painel(student_client_logado):
    """
    O aluno nao tem lateral: poucas telas, a maioria no celular, navegacao no
    topo. Aplicar o shell flex aqui criaria uma coluna vazia.
    """
    corpo = student_client_logado.get(
        reverse("student:dashboard")
    ).content.decode("utf-8")

    assert "cpo-shell--painel" not in corpo
    assert "cpo-shell" in corpo


def test_a_viewport_esta_declarada(student_client_logado):
    """
    Sem <meta name="viewport">, um celular renderiza a pagina como se fosse
    desktop de 980px e depois reduz tudo — e o layout responsivo inteiro nao
    tem efeito nenhum.
    """
    corpo = student_client_logado.get(
        reverse("student:dashboard")
    ).content.decode("utf-8")

    assert 'name="viewport"' in corpo
    assert "width=device-width" in corpo
