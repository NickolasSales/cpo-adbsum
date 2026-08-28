"""Telas administrativas de provas e questoes."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from exams.models import Exam, ExamStatus, Question, QuestionType
from exams.services import close_exam, duplicate_exam, set_exam_password

pytestmark = pytest.mark.django_db

URL_LISTA = "/admin-panel/provas/"
URL_NOVA = "/admin-panel/provas/nova/"


def url(prova, sufixo=""):
    return "/admin-panel/provas/{}/{}".format(prova.pk, sufixo)


def url_questao(prova, questao, sufixo=""):
    return "/admin-panel/provas/{}/questoes/{}/{}".format(prova.pk, questao.pk, sufixo)


def formato_datetime(momento):
    return timezone.localtime(momento).strftime("%Y-%m-%dT%H:%M")


def dados_de_prova(modulo, **trocas):
    agora = timezone.now()
    dados = {
        "module": modulo.pk,
        "title": "Avaliacao Nova",
        "description": "",
        "instructions": "",
        "open_at": formato_datetime(agora + timedelta(days=1)),
        "close_at": formato_datetime(agora + timedelta(days=2)),
        "duration_minutes": 60,
        "passing_score": "8.00",
        "max_attempts": 1,
        "failure_message": "",
        "show_score_after_submission": "on",
    }
    dados.update(trocas)
    return dados


def dados_de_questao(**trocas):
    dados = {
        "type": QuestionType.ESSAY,
        "text": "Disserte sobre o tema.",
        "points": "2.00",
        "required": "on",
        "active": "on",
        "internal_explanation": "",
        "opcoes-TOTAL_FORMS": "5",
        "opcoes-INITIAL_FORMS": "0",
        "opcoes-MIN_NUM_FORMS": "0",
        "opcoes-MAX_NUM_FORMS": "20",
    }
    for indice in range(5):
        dados["opcoes-{}-text".format(indice)] = ""
    dados.update(trocas)
    return dados


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def test_telas_de_leitura_respondem_para_admin(admin_client_logado, prova_pronta):
    questao = prova_pronta.questions.first()
    telas = [
        URL_LISTA,
        URL_NOVA,
        url(prova_pronta),
        url(prova_pronta, "editar/"),
        url(prova_pronta, "questoes/"),
        url(prova_pronta, "questoes/nova/"),
        url(prova_pronta, "gabarito/"),
        url(prova_pronta, "preview/"),
        url(prova_pronta, "senha/"),
        url_questao(prova_pronta, questao, "editar/"),
    ]
    for endereco in telas:
        assert admin_client_logado.get(endereco).status_code == 200, endereco


def test_lista_mostra_contagem_e_pontos(admin_client_logado, prova_pronta):
    resposta = admin_client_logado.get(URL_LISTA)
    encontrada = next(p for p in resposta.context["provas"] if p.pk == prova_pronta.pk)

    assert encontrada.total_questoes == 5
    assert encontrada.soma_pontos == Decimal("10.00")


def test_filtro_por_situacao(admin_client_logado, prova_pronta, prova, admin_user):
    from exams.services import publish_exam

    publish_exam(prova_pronta, actor=admin_user)

    publicadas = admin_client_logado.get(
        URL_LISTA, {"situacao": ExamStatus.PUBLISHED}
    ).context["provas"]
    assert [p.pk for p in publicadas] == [prova_pronta.pk]

    rascunhos = admin_client_logado.get(
        URL_LISTA, {"situacao": ExamStatus.DRAFT}
    ).context["provas"]
    assert prova_pronta.pk not in [p.pk for p in rascunhos]


def test_filtro_por_modulo_e_busca(
    admin_client_logado, prova, outro_modulo, admin_user
):
    from exams.services import create_exam

    outra = create_exam(module=outro_modulo, title="Recuperacao", actor=admin_user)

    por_modulo = admin_client_logado.get(
        URL_LISTA, {"modulo": outro_modulo.pk}
    ).context["provas"]
    assert [p.pk for p in por_modulo] == [outra.pk]

    por_busca = admin_client_logado.get(URL_LISTA, {"q": "Recupera"}).context["provas"]
    assert [p.pk for p in por_busca] == [outra.pk]


def test_lista_nao_faz_uma_consulta_por_prova(
    admin_client_logado, modulo, admin_user, django_assert_max_num_queries
):
    from exams.services import create_exam

    for indice in range(10):
        create_exam(
            module=modulo, title="Prova {:02d}".format(indice), actor=admin_user
        )

    with django_assert_max_num_queries(10):
        admin_client_logado.get(URL_LISTA)


def test_detalhe_mostra_pendencias_do_rascunho(admin_client_logado, prova):
    resposta = admin_client_logado.get(url(prova))
    assert resposta.status_code == 200
    assert resposta.context["pendencias"]


def test_detalhe_de_prova_pronta_nao_tem_pendencia(admin_client_logado, prova_pronta):
    resposta = admin_client_logado.get(url(prova_pronta))
    assert resposta.context["pendencias"] == []


def test_detalhe_mostra_a_linhagem(admin_client_logado, prova_publicada, admin_user):
    copia = duplicate_exam(prova_publicada, actor=admin_user)

    resposta = admin_client_logado.get(url(prova_publicada))
    versoes = [p.version for p in resposta.context["linhagem"]]
    assert versoes == [1, 2]
    assert copia.version == 2


# ---------------------------------------------------------------------------
# Criacao e edicao
# ---------------------------------------------------------------------------


def test_criacao_de_prova(admin_client_logado, modulo):
    resposta = admin_client_logado.post(URL_NOVA, dados_de_prova(modulo))
    assert resposta.status_code == 302

    prova = Exam.objects.get(title="Avaliacao Nova")
    assert prova.status == ExamStatus.DRAFT
    assert prova.version == 1
    assert prova.duration_minutes == 60
    assert prova.passing_score == Decimal("8.00")
    assert resposta.url == url(prova)


def test_criacao_recusa_janela_invertida(admin_client_logado, modulo):
    agora = timezone.now()
    resposta = admin_client_logado.post(
        URL_NOVA,
        dados_de_prova(
            modulo,
            open_at=formato_datetime(agora + timedelta(days=2)),
            close_at=formato_datetime(agora + timedelta(days=1)),
        ),
    )
    assert resposta.status_code == 200
    assert not Exam.objects.filter(title="Avaliacao Nova").exists()


def test_criacao_recusa_modulo_inativo(admin_client_logado, modulo_inativo):
    resposta = admin_client_logado.post(URL_NOVA, dados_de_prova(modulo_inativo))
    assert resposta.status_code == 200
    assert not Exam.objects.filter(title="Avaliacao Nova").exists()


def test_edicao_de_rascunho(admin_client_logado, prova, modulo):
    resposta = admin_client_logado.post(
        url(prova, "editar/"),
        dados_de_prova(modulo, title="Titulo Editado", duration_minutes=90),
    )
    assert resposta.status_code == 302

    prova.refresh_from_db()
    assert prova.title == "Titulo Editado"
    assert prova.duration_minutes == 90


def test_get_de_edicao_de_prova_publicada_redireciona(
    admin_client_logado, prova_publicada
):
    resposta = admin_client_logado.get(url(prova_publicada, "editar/"))
    assert resposta.status_code == 302
    assert resposta.url == url(prova_publicada)


def test_post_de_edicao_de_prova_publicada_responde_409(
    admin_client_logado, prova_publicada, modulo
):
    titulo_original = prova_publicada.title
    resposta = admin_client_logado.post(
        url(prova_publicada, "editar/"), dados_de_prova(modulo, title="Invadido")
    )
    assert resposta.status_code == 409

    prova_publicada.refresh_from_db()
    assert prova_publicada.title == titulo_original


# ---------------------------------------------------------------------------
# Acoes de estado
# ---------------------------------------------------------------------------


def test_publicar_pela_tela(admin_client_logado, prova_pronta):
    resposta = admin_client_logado.post(url(prova_pronta, "publicar/"))
    assert resposta.status_code == 302

    prova_pronta.refresh_from_db()
    assert prova_pronta.status == ExamStatus.PUBLISHED
    assert prova_pronta.total_points == Decimal("10.00")


def test_publicar_prova_invalida_lista_os_problemas(admin_client_logado, prova):
    resposta = admin_client_logado.post(url(prova, "publicar/"))
    assert resposta.status_code == 409

    conteudo = resposta.content.decode()
    assert "nenhuma questao ativa" in conteudo.lower()
    assert "traceback" not in conteudo.lower()

    prova.refresh_from_db()
    assert prova.status == ExamStatus.DRAFT


def test_fechar_pela_tela(admin_client_logado, prova_publicada):
    resposta = admin_client_logado.post(url(prova_publicada, "fechar/"))
    assert resposta.status_code == 302

    prova_publicada.refresh_from_db()
    assert prova_publicada.status == ExamStatus.CLOSED


def test_fechar_rascunho_responde_409(admin_client_logado, prova_pronta):
    resposta = admin_client_logado.post(url(prova_pronta, "fechar/"))
    assert resposta.status_code == 409

    prova_pronta.refresh_from_db()
    assert prova_pronta.status == ExamStatus.DRAFT


def test_duplicar_pela_tela(admin_client_logado, prova_publicada):
    resposta = admin_client_logado.post(url(prova_publicada, "duplicar/"))
    assert resposta.status_code == 302

    copia = Exam.objects.get(version=2, root_exam=prova_publicada)
    assert resposta.url == url(copia)
    assert copia.status == ExamStatus.DRAFT
    assert copia.questions.count() == 5


@pytest.mark.parametrize(
    "acao", ["publicar/", "fechar/", "duplicar/", "senha/remover/"]
)
def test_acoes_de_prova_recusam_get(admin_client_logado, prova_pronta, acao):
    """
    Alteracao de estado por GET seria disparavel por um link colado no chat
    ou por uma tag de imagem hospedada em outro site.
    """
    assert admin_client_logado.get(url(prova_pronta, acao)).status_code == 405


# ---------------------------------------------------------------------------
# Senha
# ---------------------------------------------------------------------------


def test_definir_senha_pela_tela(admin_client_logado, prova):
    resposta = admin_client_logado.post(
        url(prova, "senha/"),
        {"senha": "Turma#Alpha2026", "confirmacao": "Turma#Alpha2026"},
    )
    assert resposta.status_code == 302

    prova.refresh_from_db()
    assert prova.tem_senha is True


def test_senha_divergente_e_recusada(admin_client_logado, prova):
    resposta = admin_client_logado.post(
        url(prova, "senha/"),
        {"senha": "Turma#Alpha2026", "confirmacao": "Outra#Coisa2026"},
    )
    assert resposta.status_code == 200

    prova.refresh_from_db()
    assert prova.tem_senha is False


def test_formulario_de_senha_nunca_traz_a_senha_nem_o_hash(
    admin_client_logado, prova, admin_user
):
    senha = "Turma#Alpha2026"
    set_exam_password(prova, senha, actor=admin_user)
    prova.refresh_from_db()

    conteudo = admin_client_logado.get(url(prova, "senha/")).content.decode()

    assert senha not in conteudo
    assert prova.access_password_hash not in conteudo
    assert 'type="password"' in conteudo


def test_detalhe_nao_mostra_a_senha_nem_o_hash(admin_client_logado, prova, admin_user):
    senha = "Turma#Alpha2026"
    set_exam_password(prova, senha, actor=admin_user)
    prova.refresh_from_db()

    conteudo = admin_client_logado.get(url(prova)).content.decode()

    assert senha not in conteudo
    assert prova.access_password_hash not in conteudo
    assert "Configurada" in conteudo


def test_remover_senha_pela_tela(admin_client_logado, prova, admin_user):
    set_exam_password(prova, "Turma#Alpha2026", actor=admin_user)

    resposta = admin_client_logado.post(url(prova, "senha/remover/"))
    assert resposta.status_code == 302

    prova.refresh_from_db()
    assert prova.tem_senha is False


def test_senha_de_prova_fechada_responde_409(
    admin_client_logado, prova_publicada, admin_user
):
    set_exam_password(prova_publicada, "Turma#Alpha2026", actor=admin_user)
    close_exam(prova_publicada, actor=admin_user)

    resposta = admin_client_logado.post(url(prova_publicada, "senha/remover/"))
    assert resposta.status_code == 409


# ---------------------------------------------------------------------------
# Questoes
# ---------------------------------------------------------------------------


def test_criacao_de_questao_dissertativa(admin_client_logado, prova):
    resposta = admin_client_logado.post(
        url(prova, "questoes/nova/"), dados_de_questao()
    )
    assert resposta.status_code == 302

    questao = prova.questions.get()
    assert questao.type == QuestionType.ESSAY
    assert questao.points == Decimal("2.00")
    assert questao.options.count() == 0


def test_criacao_de_questao_de_escolha_unica_com_alternativas(
    admin_client_logado, prova
):
    dados = dados_de_questao(
        type=QuestionType.SINGLE_CHOICE,
        text="Qual e a capital?",
    )
    dados["opcoes-0-text"] = "Brasilia"
    dados["opcoes-0-is_correct"] = "on"
    dados["opcoes-1-text"] = "Rio"
    dados["opcoes-2-text"] = "Salvador"

    resposta = admin_client_logado.post(url(prova, "questoes/nova/"), dados)
    assert resposta.status_code == 302

    questao = prova.questions.get()
    assert questao.options.count() == 3
    corretas = list(questao.options.corretas())
    assert len(corretas) == 1
    assert corretas[0].text == "Brasilia"


def test_criacao_de_questao_verdadeiro_falso(admin_client_logado, prova):
    dados = dados_de_questao(
        type=QuestionType.TRUE_FALSE,
        text="A Terra e redonda.",
        resposta_verdadeira="true",
    )
    resposta = admin_client_logado.post(url(prova, "questoes/nova/"), dados)
    assert resposta.status_code == 302

    questao = prova.questions.get()
    assert [o.text for o in questao.options.order_by("order")] == ["Verdadeiro", "Falso"]
    assert questao.options.get(text="Verdadeiro").is_correct is True


def test_verdadeiro_falso_sem_escolher_resposta_e_recusada(admin_client_logado, prova):
    dados = dados_de_questao(type=QuestionType.TRUE_FALSE, text="Sem gabarito")
    resposta = admin_client_logado.post(url(prova, "questoes/nova/"), dados)

    assert resposta.status_code == 200
    assert prova.questions.count() == 0


def test_escolha_unica_sem_correta_e_recusada_com_mensagem(admin_client_logado, prova):
    dados = dados_de_questao(type=QuestionType.SINGLE_CHOICE, text="Sem gabarito")
    dados["opcoes-0-text"] = "A"
    dados["opcoes-1-text"] = "B"

    resposta = admin_client_logado.post(url(prova, "questoes/nova/"), dados)

    assert resposta.status_code == 200
    assert prova.questions.count() == 0
    assert "correta" in resposta.content.decode().lower()


def test_edicao_de_questao(admin_client_logado, prova_pronta):
    questao = prova_pronta.questions.get(type=QuestionType.ESSAY)
    dados = dados_de_questao(
        type=QuestionType.ESSAY, text="Enunciado revisado", points="4.00"
    )

    resposta = admin_client_logado.post(url_questao(prova_pronta, questao, "editar/"), dados)
    assert resposta.status_code == 302

    questao.refresh_from_db()
    assert questao.text == "Enunciado revisado"
    assert questao.points == Decimal("4.00")


def test_exclusao_de_questao(admin_client_logado, prova_pronta):
    questao = prova_pronta.questions.get(type=QuestionType.ESSAY)

    resposta = admin_client_logado.post(url_questao(prova_pronta, questao, "excluir/"))
    assert resposta.status_code == 302
    assert not Question.objects.filter(pk=questao.pk).exists()


def test_exclusao_recusa_get(admin_client_logado, prova_pronta):
    questao = prova_pronta.questions.first()
    resposta = admin_client_logado.get(url_questao(prova_pronta, questao, "excluir/"))

    assert resposta.status_code == 405
    assert Question.objects.filter(pk=questao.pk).exists()


def test_exclusao_em_prova_publicada_responde_409(admin_client_logado, prova_publicada):
    questao = prova_publicada.questions.first()

    resposta = admin_client_logado.post(url_questao(prova_publicada, questao, "excluir/"))

    assert resposta.status_code == 409
    assert Question.objects.filter(pk=questao.pk).exists()


def test_criar_questao_em_prova_publicada_responde_409(
    admin_client_logado, prova_publicada
):
    resposta = admin_client_logado.post(
        url(prova_publicada, "questoes/nova/"), dados_de_questao()
    )
    assert resposta.status_code == 409
    assert prova_publicada.questions.count() == 5


def test_editar_questao_em_prova_publicada_responde_409(
    admin_client_logado, prova_publicada
):
    questao = prova_publicada.questions.get(type=QuestionType.ESSAY)
    texto_original = questao.text

    resposta = admin_client_logado.post(
        url_questao(prova_publicada, questao, "editar/"),
        dados_de_questao(type=QuestionType.ESSAY, text="Adulterado"),
    )

    assert resposta.status_code == 409
    questao.refresh_from_db()
    assert questao.text == texto_original


def test_tela_de_questoes_mostra_o_total_de_pontos(admin_client_logado, prova_pronta):
    resposta = admin_client_logado.get(url(prova_pronta, "questoes/"))
    assert resposta.context["pontos"] == Decimal("10.00")


def test_gabarito_nao_faz_uma_consulta_por_alternativa(
    admin_client_logado, prova_pronta, django_assert_max_num_queries
):
    with django_assert_max_num_queries(10):
        admin_client_logado.get(url(prova_pronta, "gabarito/"))


def test_gabarito_mostra_a_resposta_e_a_explicacao(admin_client_logado, prova_pronta):
    conteudo = admin_client_logado.get(url(prova_pronta, "gabarito/")).content.decode()

    assert "Correta" in conteudo
    assert "apostila 1, pagina 12" in conteudo
    assert "Correcao manual" in conteudo


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def test_dashboard_conta_as_provas(admin_client_logado, prova_publicada):
    resposta = admin_client_logado.get("/admin-panel/")
    card = next(c for c in resposta.context["cards"] if c["titulo"] == "Provas")

    assert card["valor"] == 1
    assert "1 publicada" in card["nota"]
    assert card["url"] == "admin_panel:exam_list"


def test_menu_tem_provas(admin_client_logado, prova):
    resposta = admin_client_logado.get(URL_LISTA)
    secoes = [item["secao"] for item in resposta.context["itens_menu"]]
    assert "provas" in secoes
