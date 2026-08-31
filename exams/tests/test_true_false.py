"""
Verdadeiro ou falso: o bug visual e as garantias do backend.

O bug
-----
O campo resposta_verdadeira usava RadioSelect com class="form-check-input". No
Bootstrap 5 essa classe carrega margin-left: -1.5em, pensada para cancelar o
padding-left: 1.5em que o container .form-check fornece. O RadioSelect do
Django nao gera esse container — ele gera <div><label><input> Texto</label>
</div>. Sem o pai, a margem negativa puxava cada radio 1.5em para FORA da
propria caixa: circulo deslocado, rotulo escorregando por cima do anterior,
texto de ajuda atravessando os campos.

A correcao nao foi trocar a classe por outra. Foi parar de usar a renderizacao
generica: o template desenha as duas opcoes com .cpo-vf, que e feito para
exatamente duas escolhas de texto fixo, com <label> envolvendo o input — assim
clicar em qualquer ponto da linha marca a opcao, o que importa no celular.

O backend ja estava correto e continua sendo a fonte da verdade: os dois textos
sao criados pelo servico, e qualquer alternativa que venha no POST e ignorada
quando o tipo e TRUE_FALSE.
"""

import re
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from django.urls import reverse

from exams.models import TEXTO_FALSO, TEXTO_VERDADEIRO, QuestionType
from exams.services import create_question, update_question

pytestmark = pytest.mark.django_db


def criar_vf(prova, admin_user, *, verdadeira=True, ordem=10):
    return create_question(
        prova,
        type=QuestionType.TRUE_FALSE,
        text="A Terra e redonda.",
        points=Decimal("2.00"),
        order=ordem,
        resposta_verdadeira=verdadeira,
        actor=admin_user,
    )


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


def test_criar_gera_exatamente_duas_alternativas(prova, admin_user):
    questao = criar_vf(prova, admin_user)

    textos = list(questao.options.order_by("order").values_list("text", flat=True))
    assert textos == [TEXTO_VERDADEIRO, TEXTO_FALSO]


def test_apenas_uma_alternativa_e_correta(prova, admin_user):
    questao = criar_vf(prova, admin_user, verdadeira=True)

    corretas = questao.options.filter(is_correct=True)
    assert corretas.count() == 1
    assert corretas.first().text == TEXTO_VERDADEIRO


def test_marcar_falso_inverte_o_gabarito(prova, admin_user):
    questao = criar_vf(prova, admin_user, verdadeira=False)

    correta = questao.options.get(is_correct=True)
    assert correta.text == TEXTO_FALSO


def test_editar_troca_a_alternativa_correta(prova, admin_user):
    questao = criar_vf(prova, admin_user, verdadeira=True)

    update_question(
        questao,
        type=QuestionType.TRUE_FALSE,
        text=questao.text,
        points=questao.points,
        resposta_verdadeira=False,
        actor=admin_user,
    )

    questao.refresh_from_db()
    assert questao.options.get(is_correct=True).text == TEXTO_FALSO
    assert questao.options.count() == 2


def test_post_manipulado_com_tres_alternativas_e_ignorado(prova, admin_user):
    """
    O backend e a fonte da verdade.

    Mesmo que o POST traga alternativas — porque alguem montou a requisicao a
    mao, ou porque o JavaScript da tela falhou em esconder o bloco —, o
    servico as descarta e cria os dois textos canonicos.
    """
    questao = create_question(
        prova,
        type=QuestionType.TRUE_FALSE,
        text="A Terra e redonda.",
        points=Decimal("2.00"),
        order=11,
        resposta_verdadeira=True,
        opcoes=[
            {"text": "Talvez", "is_correct": True},
            {"text": "Depende", "is_correct": True},
            {"text": "Nao sei", "is_correct": False},
        ],
        actor=admin_user,
    )

    textos = list(questao.options.order_by("order").values_list("text", flat=True))
    assert textos == [TEXTO_VERDADEIRO, TEXTO_FALSO]
    assert questao.options.filter(is_correct=True).count() == 1


def test_sem_escolher_a_resposta_e_recusado(prova, admin_user):
    from common.exceptions import DomainError

    with pytest.raises(DomainError):
        create_question(
            prova,
            type=QuestionType.TRUE_FALSE,
            text="A Terra e redonda.",
            points=Decimal("2.00"),
            order=12,
            resposta_verdadeira=None,
            actor=admin_user,
        )


# ---------------------------------------------------------------------------
# Template: a estrutura que o bug quebrava
# ---------------------------------------------------------------------------


class ColetorDeRadios(HTMLParser):
    """Junta os inputs de radio do campo resposta_verdadeira."""

    def __init__(self):
        super().__init__()
        self.radios = []

    def handle_starttag(self, tag, attrs):
        atributos = dict(attrs)
        if tag == "input" and atributos.get("type") == "radio":
            if atributos.get("name") == "resposta_verdadeira":
                self.radios.append(atributos)


def bloco_vf(corpo):
    achado = re.search(
        r'<div class="cpo-vf".*?</div>\s*\n', corpo, re.DOTALL
    )
    return achado.group(0) if achado else ""


def abrir_form(client, prova):
    url = reverse("admin_panel:question_create", kwargs={"exam_id": prova.pk})
    return client.get(url).content.decode("utf-8")


def test_a_tela_tem_exatamente_duas_opcoes(admin_client_logado, prova):
    """
    Duas, nunca tres.

    Uma terceira opcao significaria que o formset generico de alternativas
    vazou para o bloco de Verdadeiro ou Falso — que e o que a interface antiga
    fazia parecer.
    """
    corpo = abrir_form(admin_client_logado, prova)

    coletor = ColetorDeRadios()
    coletor.feed(corpo)

    assert len(coletor.radios) == 2
    assert [r.get("value") for r in coletor.radios] == ["true", "false"]


def test_nenhum_radio_fica_sem_rotulo(admin_client_logado, prova):
    """
    O sintoma relatado como "radio vazio".

    Cada input precisa estar dentro de um <label> que carregue o texto. Se o
    rotulo se soltar do input de novo, este teste falha.
    """
    corpo = abrir_form(admin_client_logado, prova)
    bloco = bloco_vf(corpo)

    assert bloco, "bloco .cpo-vf nao encontrado"
    assert bloco.count("<label") == 2
    assert TEXTO_VERDADEIRO in bloco
    assert TEXTO_FALSO in bloco


def test_cada_label_aponta_para_o_seu_input(admin_client_logado, prova):
    """
    O `for` do label precisa casar com o `id` do input.

    E o que faz clicar no texto marcar a opcao — e o que um leitor de tela usa
    para anunciar a escolha.
    """
    corpo = abrir_form(admin_client_logado, prova)
    bloco = bloco_vf(corpo)

    ids = re.findall(r'<input[^>]*id="([^"]+)"', bloco)
    fors = re.findall(r'<label[^>]*for="([^"]+)"', bloco)

    assert len(ids) == 2
    assert sorted(ids) == sorted(fors)


def test_o_radio_nao_usa_mais_a_classe_que_quebrava_o_layout(
    admin_client_logado, prova
):
    """
    A causa raiz, fixada.

    form-check-input tem margin-left negativo e so funciona dentro de um
    .form-check. Aplicada solta num RadioSelect, ela puxava o input para fora
    da caixa. Se alguem a recolocar, este teste falha.
    """
    corpo = abrir_form(admin_client_logado, prova)

    coletor = ColetorDeRadios()
    coletor.feed(corpo)

    for radio in coletor.radios:
        assert "form-check-input" not in (radio.get("class") or "")


def test_o_bloco_usa_a_interface_propria(admin_client_logado, prova):
    corpo = abrir_form(admin_client_logado, prova)

    assert 'class="cpo-vf"' in corpo
    assert corpo.count("cpo-vf__opcao") == 2


def test_editar_traz_o_radio_correto_marcado(admin_client_logado, prova, admin_user):
    """
    O radio de Verdadeiro precisa vir marcado ao editar uma questao que diz
    Verdadeiro. Sem isso o administrador salvaria a resposta errada sem
    perceber.
    """
    questao = criar_vf(prova, admin_user, verdadeira=True)

    url = reverse(
        "admin_panel:question_update",
        kwargs={"exam_id": prova.pk, "question_id": questao.pk},
    )
    corpo = admin_client_logado.get(url).content.decode("utf-8")

    coletor = ColetorDeRadios()
    coletor.feed(corpo)

    marcados = [r for r in coletor.radios if "checked" in r]
    assert len(marcados) == 1
    assert marcados[0]["value"] == "true"


def test_editar_falso_traz_falso_marcado(admin_client_logado, prova, admin_user):
    questao = criar_vf(prova, admin_user, verdadeira=False)

    url = reverse(
        "admin_panel:question_update",
        kwargs={"exam_id": prova.pk, "question_id": questao.pk},
    )
    corpo = admin_client_logado.get(url).content.decode("utf-8")

    coletor = ColetorDeRadios()
    coletor.feed(corpo)

    marcados = [r for r in coletor.radios if "checked" in r]
    assert len(marcados) == 1
    assert marcados[0]["value"] == "false"


def test_o_css_da_interface_propria_existe():
    """
    Guarda contra a metade da correcao.

    O template pode estar certo e o CSS ausente — nesse caso o bloco voltaria
    a parecer quebrado, so que por outro motivo.
    """
    import io
    import pathlib

    raiz = pathlib.Path(__file__).resolve().parents[2]
    css = io.open(raiz / "static/css/app.css", encoding="utf-8").read()

    assert ".cpo-vf {" in css
    assert ".cpo-vf__opcao {" in css
