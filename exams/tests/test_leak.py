"""
Vazamento de gabarito.

O requisito foi explicito: nao basta procurar uma palavra no HTML, porque o
gabarito pode vazar por atributo, classe, ordem ou qualquer outro sinal.

O teste central deste arquivo e outro. Ele renderiza a MESMA prova duas
vezes, movendo a resposta certa de uma alternativa para outra entre as duas
renderizacoes, sem tocar em texto, ordem ou PK. Se o HTML sair identico, o
preview provavelmente nao depende do gabarito de nenhuma forma — nem por
classe CSS, nem por data-attribute, nem por ordenacao, nem por espaco em
branco. Se sair diferente, alguma coisa la dentro esta olhando is_correct.

Os testes de substring continuam, como rede complementar.
"""

import re

import pytest

from exams import selectors
from exams.models import QuestionOption, QuestionType

pytestmark = pytest.mark.django_db


# O token CSRF e mascarado de forma diferente a cada renderizacao, entao duas
# paginas identicas produzem bytes diferentes. Neutralizar so o token permite
# comparar o resto byte a byte.
PADRAO_CSRF = re.compile(r'value="[A-Za-z0-9]{32,}"')


def normalizar(html):
    return PADRAO_CSRF.sub('value="CSRF"', html)


def url_preview(prova):
    return "/admin-panel/provas/{}/preview/".format(prova.pk)


def html_do_preview(client, prova):
    resposta = client.get(url_preview(prova))
    assert resposta.status_code == 200
    return normalizar(resposta.content.decode())


# ---------------------------------------------------------------------------
# O teste que importa
# ---------------------------------------------------------------------------


def test_preview_nao_muda_quando_a_resposta_certa_muda(
    admin_client_logado, prova_pronta
):
    """
    Move o gabarito de alternativa e exige HTML identico.

    Nada alem de is_correct muda entre as duas renderizacoes: mesmos textos,
    mesmas ordens, mesmas PKs, mesma prova. Qualquer indicador da resposta
    correta no HTML faria as duas saidas divergirem.
    """
    questao = prova_pronta.questions.get(type=QuestionType.SINGLE_CHOICE)
    opcoes = list(questao.options.order_by("order", "id"))
    assert len(opcoes) == 3

    # Estado 1: a primeira alternativa e a correta.
    QuestionOption.objects.filter(pk=opcoes[0].pk).update(is_correct=True)
    QuestionOption.objects.filter(pk__in=[opcoes[1].pk, opcoes[2].pk]).update(
        is_correct=False
    )
    html_primeira_correta = html_do_preview(admin_client_logado, prova_pronta)

    # Estado 2: a ultima alternativa e a correta.
    QuestionOption.objects.filter(pk=opcoes[2].pk).update(is_correct=True)
    QuestionOption.objects.filter(pk__in=[opcoes[0].pk, opcoes[1].pk]).update(
        is_correct=False
    )
    html_ultima_correta = html_do_preview(admin_client_logado, prova_pronta)

    assert html_primeira_correta == html_ultima_correta


def test_preview_nao_muda_quando_o_gabarito_da_multipla_muda(
    admin_client_logado, prova_pronta
):
    """Mesma prova, agora para multiplas respostas."""
    questao = prova_pronta.questions.get(type=QuestionType.MULTIPLE_CHOICE)
    opcoes = list(questao.options.order_by("order", "id"))

    QuestionOption.objects.filter(pk__in=[opcoes[0].pk, opcoes[1].pk]).update(
        is_correct=True
    )
    QuestionOption.objects.filter(pk=opcoes[2].pk).update(is_correct=False)
    html_a = html_do_preview(admin_client_logado, prova_pronta)

    QuestionOption.objects.filter(pk__in=[opcoes[1].pk, opcoes[2].pk]).update(
        is_correct=True
    )
    QuestionOption.objects.filter(pk=opcoes[0].pk).update(is_correct=False)
    html_b = html_do_preview(admin_client_logado, prova_pronta)

    assert html_a == html_b


def test_preview_nao_muda_quando_o_verdadeiro_falso_inverte(
    admin_client_logado, prova_pronta
):
    questao = prova_pronta.questions.get(type=QuestionType.TRUE_FALSE)
    verdadeiro = questao.options.get(text="Verdadeiro")
    falso = questao.options.get(text="Falso")

    QuestionOption.objects.filter(pk=verdadeiro.pk).update(is_correct=True)
    QuestionOption.objects.filter(pk=falso.pk).update(is_correct=False)
    html_v = html_do_preview(admin_client_logado, prova_pronta)

    QuestionOption.objects.filter(pk=verdadeiro.pk).update(is_correct=False)
    QuestionOption.objects.filter(pk=falso.pk).update(is_correct=True)
    html_f = html_do_preview(admin_client_logado, prova_pronta)

    assert html_v == html_f


def test_a_contraprova_o_gabarito_muda_quando_a_resposta_muda(
    admin_client_logado, prova_pronta
):
    """
    Sem isto, os testes acima passariam mesmo se o preview estivesse quebrado
    e nao renderizasse alternativa nenhuma.

    Aqui a tela de gabarito, que PODE mostrar a resposta, precisa mudar
    quando a resposta muda. Se ela nao mudasse, o experimento nao estaria
    alterando nada de verdade.
    """
    url = "/admin-panel/provas/{}/gabarito/".format(prova_pronta.pk)
    questao = prova_pronta.questions.get(type=QuestionType.SINGLE_CHOICE)
    opcoes = list(questao.options.order_by("order", "id"))

    QuestionOption.objects.filter(pk=opcoes[0].pk).update(is_correct=True)
    QuestionOption.objects.filter(pk=opcoes[2].pk).update(is_correct=False)
    html_a = normalizar(admin_client_logado.get(url).content.decode())

    QuestionOption.objects.filter(pk=opcoes[0].pk).update(is_correct=False)
    QuestionOption.objects.filter(pk=opcoes[2].pk).update(is_correct=True)
    html_b = normalizar(admin_client_logado.get(url).content.decode())

    assert html_a != html_b


# ---------------------------------------------------------------------------
# Rede complementar: substrings e estrutura dos dados
# ---------------------------------------------------------------------------


def corpo_da_prova(html):
    """
    A parte da pagina que corresponde a prova, sem o cabecalho administrativo.

    O preview e uma tela de admin e traz uma faixa com um link para o
    gabarito. Essa faixa nao existira na tela do aluno, entao procurar
    palavras nela produziria falso positivo. O corte e feito no <h1> do
    titulo da prova, que e onde o conteudo comeca.
    """
    partes = html.split("<h1", 1)
    assert len(partes) == 2, "o preview mudou de estrutura; ajuste o corte"
    return partes[1]


@pytest.mark.parametrize(
    "marcador",
    [
        "is_correct",
        "correct_answer",
        "answer_key",
        "internal_explanation",
        "gabarito",
    ],
)
def test_corpo_do_preview_nao_contem_marcadores_de_gabarito(
    admin_client_logado, prova_pronta, marcador
):
    """
    Marcadores tecnicos, nao palavras do idioma.

    "correta" foi deixada de fora de proposito: ela aparece legitimamente na
    instrucao "Marque todas as alternativas corretas", que o aluno precisa
    ler para saber que a questao aceita mais de uma marcacao. Proibir a
    palavra testaria a redacao da interface, e nao o vazamento.

    Quem cobre o vazamento de verdade e
    test_preview_nao_muda_quando_a_resposta_certa_muda, que nao depende de
    adivinhar como um indicador poderia ser escrito.
    """
    corpo = corpo_da_prova(html_do_preview(admin_client_logado, prova_pronta).lower())
    assert marcador.lower() not in corpo


def test_preview_nao_contem_o_texto_da_explicacao_interna(
    admin_client_logado, prova_pronta
):
    html = html_do_preview(admin_client_logado, prova_pronta)
    assert "apostila 1, pagina 12" not in html
    assert "Avaliar coesao" not in html


def test_preview_mostra_as_alternativas(admin_client_logado, prova_pronta):
    """Contraprova: o preview precisa de fato renderizar o conteudo."""
    html = html_do_preview(admin_client_logado, prova_pronta)
    assert "Brasilia" in html
    assert "Rio de Janeiro" in html
    assert "Qual e a capital do Brasil?" in html


def test_preview_nao_expoe_atributo_data_com_a_resposta(
    admin_client_logado, prova_pronta
):
    """
    Varredura por qualquer data-attribute suspeito. Um data-correta="1" nao
    seria pego pelo teste de substring de "is_correct".
    """
    corpo = corpo_da_prova(html_do_preview(admin_client_logado, prova_pronta).lower())
    suspeitos = re.findall(r'data-[a-z-]*(?:correct|correta|answer|gabarito)[a-z-]*', corpo)
    assert suspeitos == []


# ---------------------------------------------------------------------------
# A barreira nos dados, antes do template
# ---------------------------------------------------------------------------


def test_estruturas_do_selector_nao_possuem_o_campo(prova_pronta):
    """
    A defesa nao depende do template ter sido escrito com cuidado: o dado que
    chega nele nao tem o atributo.
    """
    questoes = selectors.questoes_para_aluno(prova_pronta)
    assert len(questoes) == 5

    for questao in questoes:
        assert not hasattr(questao, "internal_explanation")
        for opcao in questao.options:
            assert not hasattr(opcao, "is_correct")


def test_ler_o_campo_no_selector_levanta_erro(prova_pronta):
    """
    Consequencia pratica: um template futuro que tente ler o gabarito quebra
    em vez de imprimi-lo. Falhar alto e melhor do que vazar em silencio.
    """
    questoes = selectors.questoes_para_aluno(prova_pronta)
    opcao = questoes[0].options[0]

    with pytest.raises(AttributeError):
        opcao.is_correct


def test_contexto_do_preview_nao_carrega_o_gabarito(admin_client_logado, prova_pronta):
    resposta = admin_client_logado.get(url_preview(prova_pronta))
    for questao in resposta.context["questoes"]:
        for opcao in questao.options:
            assert not hasattr(opcao, "is_correct")


def test_sem_gabarito_devolve_apenas_tres_campos(prova_pronta):
    questao = prova_pronta.questions.get(type=QuestionType.SINGLE_CHOICE)
    linhas = list(questao.options.sem_gabarito())

    assert linhas
    for linha in linhas:
        assert set(linha) == {"id", "question_id", "text", "order"}
        assert "is_correct" not in linha


def test_selector_nao_faz_uma_consulta_por_questao(
    prova_pronta, django_assert_max_num_queries
):
    """Duas consultas no total: questoes e alternativas."""
    with django_assert_max_num_queries(3):
        selectors.questoes_para_aluno(prova_pronta)


def test_questoes_inativas_nao_aparecem_para_o_aluno(prova_pronta, admin_user):
    prova_pronta.questions.filter(type=QuestionType.ESSAY).update(active=False)

    questoes = selectors.questoes_para_aluno(prova_pronta)
    tipos = {questao.type for questao in questoes}

    assert QuestionType.ESSAY not in tipos
    assert len(questoes) == 4
