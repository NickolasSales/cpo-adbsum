"""
JavaScript e requisito funcional desta tela. Aqui esta a prova disso.

O relatorio da Etapa 4 afirmava que "sem JavaScript a prova continua
funcionando". A afirmacao estava errada, e este arquivo existe para que ela
nao possa voltar sem que alguem perceba.

As respostas chegam ao servidor por um unico caminho: o endpoint de autosave,
chamado por fetch. A rota de finalizar recebe o POST do formulario e nao le
nenhum campo de resposta dele — le apenas o token CSRF, que o Django confere
antes da view. Um aluno com JavaScript desligado preencheria a tela inteira,
clicaria em finalizar e entregaria uma prova em branco.

A correcao desta etapa foi documental e de interface, e nao um segundo motor
de resposta: um aviso <noscript> no topo da tela, a afirmacao falsa removida
do README e do comentario do template, e estes testes fixando o comportamento
real. Ler os campos do formulario no envio significaria um segundo caminho de
escrita de resposta, com sua propria validacao de token, seu proprio
tratamento de prazo e sua propria chance de discordar do primeiro.

Nada aqui afrouxa o servidor: prazo, matricula, CSRF, validacao de token e
recusa depois do envio continuam todos no Django, e nenhum deles depende de o
script ter rodado.
"""

import io
import pathlib
import re

import pytest
from django.urls import reverse

from exams.models import Answer, AnswerOption, AttemptStatus, TIPOS_COM_ALTERNATIVAS
from exams.services import autosave_answer, submit_attempt

pytestmark = pytest.mark.django_db

RAIZ = pathlib.Path(__file__).resolve().parents[2]

TEXTO_DO_FORMULARIO = "TEXTO QUE VEIO DO FORMULARIO"
TEXTO_DO_AUTOSAVE = "texto salvo pelo autosave"


@pytest.fixture
def aluno_logado(client, aluno_matriculado):
    client.force_login(aluno_matriculado)
    return client


def url_finalizar(tentativa):
    return reverse("student:attempt_submit", kwargs={"public_id": tentativa.public_id})


def url_tentativa(tentativa):
    return reverse("student:attempt", kwargs={"public_id": tentativa.public_id})


def linhas(tentativa):
    return (
        tentativa.questions.select_related("question")
        .prefetch_related("options")
        .order_by("display_order")
    )


def campos_do_formulario(tentativa, *, texto=TEXTO_DO_FORMULARIO):
    """
    Monta o POST que o navegador enviaria sem JavaScript.

    Percorre a tentativa e preenche TODAS as questoes, do jeito que a tela
    nomeia os campos: q_<token> para cada questao, com o token da alternativa
    como valor, ou o texto digitado. E exatamente o corpo que sairia de um
    <form> comum submetido por um navegador com o script desligado.
    """
    corpo = {}
    for linha in linhas(tentativa):
        nome = "q_{}".format(linha.public_token)
        if linha.question.type in TIPOS_COM_ALTERNATIVAS:
            corpo[nome] = str(linha.options.first().public_token)
        else:
            corpo[nome] = texto
    return corpo


def responder_todas(tentativa, *, texto=TEXTO_DO_AUTOSAVE):
    """Preenche pelo unico caminho de escrita que existe: o autosave."""
    for linha in linhas(tentativa):
        if linha.question.type in TIPOS_COM_ALTERNATIVAS:
            autosave_answer(
                tentativa,
                question_token=str(linha.public_token),
                option_tokens=[str(linha.options.first().public_token)],
            )
        else:
            autosave_answer(
                tentativa, question_token=str(linha.public_token), text=texto
            )


def respostas_gravadas(tentativa):
    return Answer.objects.filter(attempt_question__attempt=tentativa).count()


def marcacoes_gravadas(tentativa):
    return AnswerOption.objects.filter(
        answer__attempt_question__attempt=tentativa
    ).count()


# ---------------------------------------------------------------------------
# O que o envio faz com os campos do formulario: nada
# ---------------------------------------------------------------------------


def test_finalizar_nao_grava_as_respostas_que_vem_no_formulario(
    aluno_logado, tentativa
):
    """
    O teste central desta etapa.

    O POST vai com TODAS as questoes preenchidas. Se a view lesse o
    formulario, o envio seria aceito e as respostas apareceriam no banco.

    O que acontece e o contrario: a view responde 409 dizendo que faltam
    obrigatorias — todas as cinco — e o banco continua vazio. Ou seja, o
    formulario foi inteiramente ignorado.

    Este 409 e a melhor prova possivel do problema: o aluno respondeu tudo e o
    servidor nao viu nada.
    """
    corpo = campos_do_formulario(tentativa)
    assert len(corpo) == 5

    resposta = aluno_logado.post(url_finalizar(tentativa), corpo)

    assert resposta.status_code == 409
    assert respostas_gravadas(tentativa) == 0
    assert marcacoes_gravadas(tentativa) == 0

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.IN_PROGRESS


def test_finalizar_ignora_o_formulario_mesmo_quando_o_envio_e_aceito(
    aluno_logado, tentativa
):
    """
    O caso anterior poderia ser explicado pela recusa: talvez a view lesse o
    formulario e desistisse no meio.

    Aqui o envio e aceito. As obrigatorias sao satisfeitas antes, por
    autosave, e so entao vem o POST — carregando um texto diferente do que foi
    salvo. Se o formulario fosse lido, esse conteudo apareceria gravado.

    Nada muda: vale o que passou pelo autosave.
    """
    responder_todas(tentativa)
    corpo = campos_do_formulario(tentativa)

    resposta = aluno_logado.post(url_finalizar(tentativa), corpo)

    assert resposta.status_code == 302
    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.SUBMITTED

    textos = list(
        Answer.objects.filter(attempt_question__attempt=tentativa).values_list(
            "text_answer", flat=True
        )
    )
    assert TEXTO_DO_FORMULARIO not in textos
    assert TEXTO_DO_AUTOSAVE in textos


def test_finalizar_nao_le_campo_algum_do_corpo(aluno_logado, tentativa):
    """
    Generaliza: o corpo do POST pode vir com qualquer coisa.

    Campos de resposta, campos de estado, campos inventados. A view usa
    apenas a sessao (quem e o aluno) e a URL (qual tentativa).
    """
    responder_todas(tentativa)

    lixo = {
        "status": "APPROVED",
        "final_score": "10",
        "submitted_at": "2099-01-01T00:00:00Z",
        "student_id": "1",
        "q_inexistente": "valor",
    }

    resposta = aluno_logado.post(url_finalizar(tentativa), lixo)

    assert resposta.status_code == 302
    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.SUBMITTED
    assert tentativa.submitted_at is not None


def test_o_botao_de_enviar_depende_do_script(aluno_logado, tentativa):
    """
    O segundo motivo, independente do primeiro.

    Mesmo que a view lesse o formulario, o aluno sem JavaScript nao chegaria a
    envia-lo: o botao visivel e type="button" e abre um modal do Bootstrap; o
    unico type="submit" mora dentro desse modal, que sem script nunca abre.

    Duas barreiras separadas para a mesma conclusao. Este teste fixa a
    segunda, para que uma eventual mudanca no modal nao passe despercebida.
    """
    corpo = aluno_logado.get(url_tentativa(tentativa)).content.decode("utf-8")

    # Recorta o formulario da prova. O layout do aluno tem um formulario de
    # sair, com seu proprio type="submit", que nao tem nada a ver com isto —
    # procurar na pagina inteira encontraria aquele botao e o teste passaria
    # ou falharia pelo motivo errado.
    prova = re.search(
        r'<form[^>]*id="cpo-form-prova".*?</form>', corpo, re.DOTALL
    )
    assert prova is not None, "formulario da prova nao encontrado"
    prova = prova.group(0)

    assert 'data-bs-toggle="modal"' in prova

    # O modal e a ultima coisa dentro do formulario, entao partir por ele
    # separa exatamente as duas regioes que interessam: o corpo visivel da
    # prova, e o conteudo que so aparece se o Bootstrap abrir o modal.
    marcador = '<div class="modal fade" id="modalFinalizar"'
    assert prova.count(marcador) == 1
    visivel, dentro_do_modal = prova.split(marcador)

    assert 'type="submit"' in dentro_do_modal
    assert 'type="submit"' not in visivel


# ---------------------------------------------------------------------------
# O aviso na tela
# ---------------------------------------------------------------------------


def test_a_tela_avisa_quem_esta_sem_javascript(aluno_logado, tentativa):
    """
    O aviso precisa estar dentro de <noscript>, e nao num paragrafo comum.

    Um paragrafo comum apareceria para todo mundo, inclusive para os que tem
    JavaScript, e viraria ruido que o aluno aprende a ignorar.
    """
    corpo = aluno_logado.get(url_tentativa(tentativa)).content.decode("utf-8")

    dentro = re.search(r"<noscript>(.*?)</noscript>", corpo, re.DOTALL)
    assert dentro is not None, "a tela nao tem aviso de noscript"

    texto = dentro.group(1)
    assert "JavaScript" in texto
    assert "nao serao salvas" in texto


def test_o_aviso_aparece_antes_das_questoes(aluno_logado, tentativa):
    """
    Posicao importa mais que redacao aqui.

    Avisar depois das cinco questoes seria avisar depois de o aluno ter
    respondido tudo, com o cronometro correndo o tempo todo. O aviso precisa
    estar no topo, antes do primeiro campo.
    """
    corpo = aluno_logado.get(url_tentativa(tentativa)).content.decode("utf-8")

    assert corpo.index("<noscript>") < corpo.index("cpo-questao")


def test_a_tela_encerrada_nao_mostra_o_aviso(aluno_logado, tentativa):
    """
    Depois do envio nao ha o que salvar, entao o aviso perde a razao de ser e
    so assustaria quem ja entregou.
    """
    responder_todas(tentativa)
    submit_attempt(tentativa)

    corpo = aluno_logado.get(url_tentativa(tentativa)).content.decode("utf-8")

    assert "<noscript>" not in corpo


# ---------------------------------------------------------------------------
# A afirmacao falsa nao pode voltar
# ---------------------------------------------------------------------------


def test_a_documentacao_nao_promete_funcionamento_sem_javascript():
    """
    Guarda contra a regressao de redacao.

    Se alguem reescrever o README e recolocar a promessa, este teste falha. A
    promessa e pior que a ausencia de suporte: leva o aluno a confiar numa
    tela que vai perder o trabalho dele.
    """
    arquivos = [RAIZ / "README.md", RAIZ / "templates/student/exams/attempt.html"]

    for caminho in arquivos:
        texto = io.open(caminho, encoding="utf-8").read().lower()
        assert "prova ainda funciona" not in texto, caminho
        assert "prova continua funcionando" not in texto, caminho
        assert "sem javascript a prova" not in texto, caminho


def test_o_readme_registra_o_requisito():
    texto = io.open(RAIZ / "README.md", encoding="utf-8").read()

    assert "JavaScript" in texto
    assert "requisito funcional" in texto
