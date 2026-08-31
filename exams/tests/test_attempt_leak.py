"""
O que a tela da prova entrega ao navegador — e o que ela nao entrega.

Dois vazamentos diferentes, e cada um precisa do seu tipo de teste:

    gabarito           qual alternativa esta certa. Testado por renderizacao
                       diferencial: a mesma tentativa e desenhada duas vezes
                       com a resposta certa em lugares diferentes, e o HTML
                       precisa sair identico.

    ids internos       Question.id, QuestionOption.id, AttemptQuestion.id,
                       AttemptOption.id. Testado por analise dos atributos, e
                       nao por procurar numeros no texto.

Por que nao procurar o numero no HTML
-------------------------------------
`assert str(questao.pk) not in html` parece razoavel e nao serve. Um pk de um
digito casa com qualquer "60" de duracao, com "2026" da data e com qualquer
digito hexadecimal de um UUID. O teste comeca verde por sorte e fica vermelho
por sorte, e ninguem confia nele.

O que estes testes fazem em vez disso: extraem os atributos que carregam
identificador — name, value, id, for, data-* e a URL do formulario — e exigem
que todo identificador de resposta seja um UUID de tentativa. Um pk decimal
falharia por nao ser UUID, independentemente do valor.
"""

import re
import uuid
from html.parser import HTMLParser

import pytest
from django.urls import reverse

from exams.models import (
    AttemptOption,
    AttemptQuestion,
    QuestionOption,
    QuestionType,
)
from exams.services import autosave_answer

pytestmark = pytest.mark.django_db


# O token CSRF muda a cada renderizacao e nao diz nada sobre gabarito. E
# alfanumerico puro e longo; os tokens da tentativa sao UUID e tem hifen,
# entao esta normalizacao nunca os atinge.
PADRAO_CSRF = re.compile(r'value="[A-Za-z0-9]{32,}"')

ATRIBUTOS_DE_IDENTIFICACAO = ("name", "value", "id", "for", "action", "href")


class ColetorDeAtributos(HTMLParser):
    """Junta todos os atributos de identificacao presentes no documento."""

    def __init__(self):
        super().__init__()
        self.identificadores = []
        self.dados = []

    def handle_starttag(self, tag, attrs):
        for chave, valor in attrs:
            if valor is None:
                continue
            if chave in ATRIBUTOS_DE_IDENTIFICACAO:
                self.identificadores.append((tag, chave, valor))
            if chave.startswith("data-"):
                self.dados.append((tag, chave, valor))


def analisar(html):
    coletor = ColetorDeAtributos()
    coletor.feed(html)
    return coletor


def e_uuid(valor):
    try:
        uuid.UUID(str(valor))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def abrir_prova(client, tentativa):
    resposta = client.get(
        reverse("student:attempt", kwargs={"public_id": tentativa.public_id})
    )
    assert resposta.status_code == 200
    return resposta.content.decode("utf-8")


@pytest.fixture
def aluno_logado(client, aluno_matriculado, senha):
    client.force_login(aluno_matriculado)
    return client


# ---------------------------------------------------------------------------
# O gabarito nao muda o HTML
# ---------------------------------------------------------------------------


def test_mudar_a_resposta_certa_nao_muda_a_tela_da_prova(
    aluno_logado, tentativa, prova_aberta
):
    """
    O teste central desta etapa.

    A mesma tentativa e renderizada duas vezes. Entre uma e outra, a marca de
    resposta correta e movida da primeira alternativa para a ultima — mesmas
    PKs, mesmos textos, mesma ordem de exibicao. Se o HTML mudar em um unico
    byte, alguma coisa na pagina depende de is_correct: uma classe CSS, um
    atributo, um espaco, a ordenacao.

    Procurar a palavra "correct" no HTML nao pegaria um vazamento por ordem
    nem por classe. Comparar os dois documentos pega qualquer um deles.
    """
    questao = prova_aberta.questions.get(type=QuestionType.SINGLE_CHOICE)
    alternativas = list(questao.options.order_by("order", "id"))

    QuestionOption.objects.filter(question=questao).update(is_correct=False)
    QuestionOption.objects.filter(pk=alternativas[0].pk).update(is_correct=True)
    primeira = PADRAO_CSRF.sub('value="CSRF"', abrir_prova(aluno_logado, tentativa))

    QuestionOption.objects.filter(question=questao).update(is_correct=False)
    QuestionOption.objects.filter(pk=alternativas[-1].pk).update(is_correct=True)
    ultima = PADRAO_CSRF.sub('value="CSRF"', abrir_prova(aluno_logado, tentativa))

    assert primeira == ultima


def test_contraprova_o_gabarito_administrativo_muda(
    admin_client_logado, prova_aberta
):
    """
    Sem isto, o teste acima poderia estar passando por nao alterar nada.

    A mesma manipulacao precisa produzir HTML diferente na tela de gabarito,
    que e onde a resposta certa deve mesmo aparecer.
    """
    questao = prova_aberta.questions.get(type=QuestionType.SINGLE_CHOICE)
    alternativas = list(questao.options.order_by("order", "id"))
    url = reverse("admin_panel:exam_gabarito", kwargs={"pk": prova_aberta.pk})

    QuestionOption.objects.filter(question=questao).update(is_correct=False)
    QuestionOption.objects.filter(pk=alternativas[0].pk).update(is_correct=True)
    primeira = admin_client_logado.get(url).content.decode("utf-8")

    QuestionOption.objects.filter(question=questao).update(is_correct=False)
    QuestionOption.objects.filter(pk=alternativas[-1].pk).update(is_correct=True)
    ultima = admin_client_logado.get(url).content.decode("utf-8")

    assert primeira != ultima


def test_mudar_a_explicacao_interna_nao_muda_a_tela_da_prova(
    aluno_logado, tentativa, prova_aberta
):
    from exams.models import Question

    primeira = PADRAO_CSRF.sub('value="CSRF"', abrir_prova(aluno_logado, tentativa))

    Question.objects.filter(exam=prova_aberta).update(
        internal_explanation="Segredo do professor que nao pode vazar."
    )
    segunda = PADRAO_CSRF.sub('value="CSRF"', abrir_prova(aluno_logado, tentativa))

    assert primeira == segunda


def test_marcadores_tecnicos_de_gabarito_nao_aparecem(aluno_logado, tentativa):
    html = abrir_prova(aluno_logado, tentativa).lower()

    for marcador in (
        "is_correct",
        "iscorrect",
        "correct_answer",
        "answer_key",
        "internal_explanation",
        "gabarito",
    ):
        assert marcador not in html


def test_o_contexto_da_tela_nao_carrega_o_gabarito(aluno_logado, tentativa):
    """
    Nao basta o template nao imprimir: o dado nao pode nem chegar ate ele.

    Se estivesse no contexto, bastaria alguem escrever {{ opcao.is_correct }}
    um dia para o vazamento acontecer sem que nenhum teste percebesse.
    """
    resposta = aluno_logado.get(
        reverse("student:attempt", kwargs={"public_id": tentativa.public_id})
    )

    for questao in resposta.context["questoes"]:
        assert not hasattr(questao, "internal_explanation")
        for opcao in questao.options:
            assert not hasattr(opcao, "is_correct")


def test_ler_o_gabarito_pelo_dto_levanta_erro(tentativa):
    """
    A barreira e estrutural, e nao disciplinar.

    Com objeto do ORM e .only()/.defer(), ler .is_correct dispararia uma
    consulta nova e devolveria a resposta certa em silencio. Numa dataclass
    sem o campo, a mesma tentativa vira AttributeError — o erro aparece em
    desenvolvimento, e nao na prova.
    """
    from exams import selectors

    questoes = selectors.questoes_da_tentativa(tentativa)
    objetiva = next(q for q in questoes if q.usa_alternativas)

    with pytest.raises(AttributeError):
        objetiva.options[0].is_correct

    with pytest.raises(AttributeError):
        objetiva.internal_explanation


def test_o_dto_e_imutavel(tentativa):
    """
    Frozen: ninguem pode enxertar um campo no caminho para o template.

    Sem isso, uma view futura poderia anexar o gabarito ao objeto "so para
    conferir" e o template passaria a ter acesso a ele.
    """
    import dataclasses

    from exams import selectors

    questoes = selectors.questoes_da_tentativa(tentativa)

    with pytest.raises(dataclasses.FrozenInstanceError):
        questoes[0].text = "outro enunciado"


# ---------------------------------------------------------------------------
# Ids internos nao chegam ao navegador
# ---------------------------------------------------------------------------


def test_todo_identificador_de_resposta_e_um_token_da_tentativa(
    aluno_logado, tentativa
):
    """
    Percorre os atributos que identificam campos de resposta e exige que o
    valor seja um UUID pertencente a esta tentativa.

    Um pk decimal falharia por nao ser UUID, qualquer que fosse o numero. Um
    UUID de outra tentativa falharia por nao estar no conjunto.
    """
    html = abrir_prova(aluno_logado, tentativa)
    analise = analisar(html)

    tokens_validos = {
        str(valor)
        for valor in AttemptQuestion.objects.filter(attempt=tentativa).values_list(
            "public_token", flat=True
        )
    } | {
        str(valor)
        for valor in AttemptOption.objects.filter(
            attempt_question__attempt=tentativa
        ).values_list("public_token", flat=True)
    }

    encontrados = 0
    for tag, chave, valor in analise.identificadores:
        if chave == "name" and valor.startswith("q_"):
            token = valor[2:]
            assert e_uuid(token), "name={} nao e token".format(valor)
            assert token in tokens_validos
            encontrados += 1
        elif chave == "value" and tag == "input" and e_uuid(valor):
            assert valor in tokens_validos
            encontrados += 1
        elif chave in ("id", "for") and valor.startswith(("opcao-", "titulo-")):
            token = valor.split("-", 1)[1]
            assert e_uuid(token), "{}={} nao e token".format(chave, valor)
            assert token in tokens_validos
            encontrados += 1

    # 5 questoes + 8 alternativas, cada uma aparecendo em mais de um atributo.
    assert encontrados >= 13


def test_nenhum_data_attribute_carrega_id_interno(aluno_logado, tentativa):
    """
    data-* e o esconderijo classico: nao aparece na tela, mas esta no HTML.

    Os unicos data-* desta pagina sao a URL do autosave, os segundos
    restantes, a marca de obrigatoria, o token da questao e os atributos do
    Bootstrap. Nenhum deles pode conter identificador interno.
    """
    html = abrir_prova(aluno_logado, tentativa)
    analise = analisar(html)

    tokens_de_questao = {
        str(valor)
        for valor in AttemptQuestion.objects.filter(attempt=tentativa).values_list(
            "public_token", flat=True
        )
    }

    permitidos = {
        "data-autosave-url",
        "data-restantes",
        "data-obrigatoria",
        "data-questao",
        "data-bs-toggle",
        "data-bs-target",
        "data-bs-dismiss",
    }

    for _tag, chave, valor in analise.dados:
        assert chave in permitidos, "data-* inesperado: {}={}".format(chave, valor)
        if chave == "data-questao":
            assert valor in tokens_de_questao


def test_a_url_do_formulario_usa_o_identificador_publico(aluno_logado, tentativa):
    """
    O formulario da prova aponta para o UUID, nunca para a PK.

    Filtra pelas acoes de tentativa: a pagina herda o layout do aluno, que ja
    traz o formulario de sair, e ele nao tem nada a ver com esta verificacao.
    """
    html = abrir_prova(aluno_logado, tentativa)
    analise = analisar(html)

    acoes = [
        valor
        for _t, chave, valor in analise.identificadores
        if chave == "action" and "/tentativas/" in valor
    ]
    assert acoes, "a pagina precisa ter o formulario da prova"

    for acao in acoes:
        assert str(tentativa.public_id) in acao
        assert "/tentativas/{}/".format(tentativa.pk) not in acao


def test_a_url_de_autosave_usa_o_identificador_publico(aluno_logado, tentativa):
    html = abrir_prova(aluno_logado, tentativa)
    analise = analisar(html)

    urls = [valor for _t, chave, valor in analise.dados if chave == "data-autosave-url"]
    assert len(urls) == 1
    assert str(tentativa.public_id) in urls[0]


def test_a_pagina_nao_contem_os_ids_internos_em_atributo_nenhum(
    aluno_logado, tentativa
):
    """
    Varredura complementar: os valores exatos dos ids internos nao podem
    aparecer sozinhos em nenhum atributo de identificacao.

    Compara valores completos, e nao substring — e por isso nao sofre do falso
    positivo descrito no topo do arquivo.
    """
    html = abrir_prova(aluno_logado, tentativa)
    analise = analisar(html)

    proibidos = set()
    proibidos.add(str(tentativa.pk))
    for linha in AttemptQuestion.objects.filter(attempt=tentativa):
        proibidos.add(str(linha.pk))
        proibidos.add(str(linha.question_id))
    for alternativa in AttemptOption.objects.filter(
        attempt_question__attempt=tentativa
    ):
        proibidos.add(str(alternativa.pk))
        proibidos.add(str(alternativa.option_id))

    for tag, chave, valor in analise.identificadores:
        if chave in ("name", "value", "id", "for") and valor in proibidos:
            pytest.fail(
                "id interno exposto em <{} {}={}>".format(tag, chave, valor)
            )


def test_a_ordem_exibida_nao_segue_o_id_das_alternativas(
    aluno_logado, tentativa, prova_aberta
):
    """
    A renderizacao usa display_order, nunca QuestionOption.id.

    Importa porque o administrador costuma digitar a alternativa correta
    primeiro, e a correta ficaria com o menor id da questao. Ordenar por id
    entregaria o gabarito pela posicao — sem nenhum campo sensivel aparecer no
    HTML.
    """
    from exams import selectors

    questao = prova_aberta.questions.get(type=QuestionType.MULTIPLE_CHOICE)
    linha = AttemptQuestion.objects.get(attempt=tentativa, question=questao)

    # Inverte a ordem de exibicao sem tocar em id nem em texto.
    alternativas = list(
        AttemptOption.objects.filter(attempt_question=linha).order_by("display_order")
    )
    total = len(alternativas)
    for posicao, alternativa in enumerate(alternativas):
        AttemptOption.objects.filter(pk=alternativa.pk).update(
            display_order=total + posicao
        )
    for posicao, alternativa in enumerate(reversed(alternativas)):
        AttemptOption.objects.filter(pk=alternativa.pk).update(display_order=posicao)

    questoes = selectors.questoes_da_tentativa(tentativa)
    exibida = next(q for q in questoes if q.multipla)

    esperada = [str(a.public_token) for a in reversed(alternativas)]
    assert [o.token for o in exibida.options] == esperada


# ---------------------------------------------------------------------------
# Respostas salvas voltam para a tela
# ---------------------------------------------------------------------------


def test_a_alternativa_marcada_volta_marcada(aluno_logado, tentativa, tokens):
    questao, alternativas = tokens[QuestionType.SINGLE_CHOICE]
    autosave_answer(tentativa, question_token=questao, option_tokens=[alternativas[1]])

    html = abrir_prova(aluno_logado, tentativa)

    # O input daquela alternativa precisa vir com checked, e os outros nao.
    marcado = re.search(
        r'<input[^>]*value="{}"[^>]*>'.format(re.escape(alternativas[1])), html
    )
    assert marcado is not None
    assert "checked" in marcado.group(0)

    nao_marcado = re.search(
        r'<input[^>]*value="{}"[^>]*>'.format(re.escape(alternativas[0])), html
    )
    assert "checked" not in nao_marcado.group(0)


def test_o_texto_salvo_volta_no_campo(aluno_logado, tentativa, tokens):
    questao, _ = tokens[QuestionType.ESSAY]
    autosave_answer(tentativa, question_token=questao, text="Minha redacao inteira.")

    html = abrir_prova(aluno_logado, tentativa)

    assert "Minha redacao inteira." in html


def test_o_texto_salvo_e_escapado_no_html(aluno_logado, tentativa, tokens):
    """
    O texto do aluno volta para a tela e nao pode virar marcacao.

    Uma resposta com <script> precisa reaparecer como texto. O autoescape do
    Django faz isso; o teste existe para que ninguem o desligue com |safe
    numa refatoracao futura.
    """
    questao, _ = tokens[QuestionType.ESSAY]
    autosave_answer(
        tentativa,
        question_token=questao,
        text='<script>alert("xss")</script> & <b>negrito</b>',
    )

    html = abrir_prova(aluno_logado, tentativa)

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;negrito" in html


def test_o_enunciado_tambem_e_escapado(aluno_logado, tentativa, prova_aberta):
    from exams.models import Question

    Question.objects.filter(
        exam=prova_aberta, type=QuestionType.SHORT_TEXT
    ).update(text='Cite <script>alert(1)</script> um bioma')

    html = abrir_prova(aluno_logado, tentativa)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
