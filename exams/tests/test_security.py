"""
Seguranca das rotas de provas.

Exaustivo de proposito. O modo mais provavel de um furo aparecer nao e alguem
escrever uma regra errada: e alguem acrescentar uma view nova e esquecer o
mixin de papel. Estes testes varrem todas as rotas de uma vez, entao a rota
esquecida aparece.
"""

import pytest
from django.test import Client

from exams.models import Exam, ExamStatus, Question, QuestionType
from exams.services import create_exam, create_question

pytestmark = pytest.mark.django_db


@pytest.fixture
def rotas(prova_pronta):
    questao = prova_pronta.questions.first()
    identificador = prova_pronta.pk
    return {
        "leitura": [
            "/admin-panel/provas/",
            "/admin-panel/provas/nova/",
            "/admin-panel/provas/{}/".format(identificador),
            "/admin-panel/provas/{}/editar/".format(identificador),
            "/admin-panel/provas/{}/gabarito/".format(identificador),
            "/admin-panel/provas/{}/preview/".format(identificador),
            "/admin-panel/provas/{}/senha/".format(identificador),
            "/admin-panel/provas/{}/questoes/".format(identificador),
            "/admin-panel/provas/{}/questoes/nova/".format(identificador),
            "/admin-panel/provas/{}/questoes/{}/editar/".format(
                identificador, questao.pk
            ),
        ],
        "acao": [
            "/admin-panel/provas/{}/publicar/".format(identificador),
            "/admin-panel/provas/{}/fechar/".format(identificador),
            "/admin-panel/provas/{}/duplicar/".format(identificador),
            "/admin-panel/provas/{}/senha/remover/".format(identificador),
            "/admin-panel/provas/{}/questoes/{}/excluir/".format(
                identificador, questao.pk
            ),
        ],
    }


# ---------------------------------------------------------------------------
# Segregacao de papel
# ---------------------------------------------------------------------------


def test_admin_alcanca_todas_as_telas(admin_client_logado, rotas):
    """
    Contraprova.

    Sem ela, os testes de bloqueio abaixo passariam mesmo que as rotas nao
    existissem.
    """
    for endereco in rotas["leitura"]:
        assert admin_client_logado.get(endereco).status_code == 200, endereco


def test_aluno_e_barrado_em_todas_as_telas(student_client_logado, rotas):
    for endereco in rotas["leitura"]:
        assert student_client_logado.get(endereco).status_code == 403, endereco


def test_aluno_e_barrado_em_todas_as_acoes(student_client_logado, rotas):
    for endereco in rotas["acao"]:
        assert student_client_logado.post(endereco).status_code == 403, endereco


def test_anonimo_e_redirecionado_nas_telas(client, rotas):
    for endereco in rotas["leitura"]:
        resposta = client.get(endereco)
        assert resposta.status_code == 302, endereco
        assert "/login/" in resposta.url, endereco


def test_anonimo_e_redirecionado_nas_acoes(client, rotas):
    for endereco in rotas["acao"]:
        resposta = client.post(endereco)
        assert resposta.status_code == 302, endereco
        assert "/login/" in resposta.url, endereco


def test_aluno_nao_publica(student_client_logado, prova_pronta):
    resposta = student_client_logado.post(
        "/admin-panel/provas/{}/publicar/".format(prova_pronta.pk)
    )
    assert resposta.status_code == 403

    prova_pronta.refresh_from_db()
    assert prova_pronta.status == ExamStatus.DRAFT


def test_aluno_nao_duplica(student_client_logado, prova_publicada):
    resposta = student_client_logado.post(
        "/admin-panel/provas/{}/duplicar/".format(prova_publicada.pk)
    )
    assert resposta.status_code == 403
    assert Exam.objects.count() == 1


def test_aluno_nao_alcanca_o_gabarito(student_client_logado, prova_pronta):
    resposta = student_client_logado.get(
        "/admin-panel/provas/{}/gabarito/".format(prova_pronta.pk)
    )
    assert resposta.status_code == 403


def test_anonimo_nao_alcanca_o_gabarito(client, prova_pronta):
    resposta = client.get("/admin-panel/provas/{}/gabarito/".format(prova_pronta.pk))
    assert resposta.status_code == 302
    assert "/login/" in resposta.url


def test_area_do_aluno_continua_sem_provas(student_client_logado, prova_publicada, matricula):
    """
    A exposicao da prova ao aluno e da Etapa 4. Publicar uma prova agora nao
    pode fazer nada aparecer para ele.
    """
    resposta = student_client_logado.get(
        "/aluno/modulos/{}/".format(prova_publicada.module_id)
    )
    assert resposta.status_code == 200
    assert list(resposta.context["provas"]) == []

    conteudo = resposta.content.decode()
    assert prova_publicada.title not in conteudo


# ---------------------------------------------------------------------------
# Metodo HTTP
# ---------------------------------------------------------------------------


def test_nenhuma_acao_aceita_get(admin_client_logado, rotas):
    for endereco in rotas["acao"]:
        assert admin_client_logado.get(endereco).status_code == 405, endereco


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


def test_post_sem_token_csrf_e_recusado(admin_user, prova_pronta):
    cliente = Client(enforce_csrf_checks=True)
    cliente.force_login(admin_user)

    resposta = cliente.post("/admin-panel/provas/{}/publicar/".format(prova_pronta.pk))
    assert resposta.status_code == 403

    prova_pronta.refresh_from_db()
    assert prova_pronta.status == ExamStatus.DRAFT


def test_post_com_token_csrf_e_aceito(admin_user, prova_pronta):
    cliente = Client(enforce_csrf_checks=True)
    cliente.force_login(admin_user)

    pagina = cliente.get("/admin-panel/provas/{}/".format(prova_pronta.pk))
    token = pagina.context["csrf_token"]

    resposta = cliente.post(
        "/admin-panel/provas/{}/publicar/".format(prova_pronta.pk),
        {"csrfmiddlewaretoken": token},
    )
    assert resposta.status_code == 302

    prova_pronta.refresh_from_db()
    assert prova_pronta.status == ExamStatus.PUBLISHED


# ---------------------------------------------------------------------------
# Mass assignment
# ---------------------------------------------------------------------------


def _dados_de_prova(modulo, **extras):
    from datetime import timedelta

    from django.utils import timezone

    agora = timezone.now()
    dados = {
        "module": modulo.pk,
        "title": "Prova Forjada",
        "description": "",
        "instructions": "",
        "open_at": timezone.localtime(agora + timedelta(days=1)).strftime(
            "%Y-%m-%dT%H:%M"
        ),
        "close_at": timezone.localtime(agora + timedelta(days=2)).strftime(
            "%Y-%m-%dT%H:%M"
        ),
        "duration_minutes": 60,
        "passing_score": "8.00",
        "max_attempts": 1,
        "failure_message": "",
    }
    dados.update(extras)
    return dados


def test_post_nao_consegue_publicar_pelo_campo_status(admin_client_logado, modulo):
    """
    Mudanca de estado nao pode ser um campo de formulario. Publicar tem
    servico proprio, que valida antes.
    """
    admin_client_logado.post(
        "/admin-panel/provas/nova/",
        _dados_de_prova(modulo, status=ExamStatus.PUBLISHED),
    )
    prova = Exam.objects.get(title="Prova Forjada")

    assert prova.status == ExamStatus.DRAFT
    assert prova.published_at is None


def test_post_nao_consegue_definir_o_total_de_pontos(admin_client_logado, modulo):
    admin_client_logado.post(
        "/admin-panel/provas/nova/", _dados_de_prova(modulo, total_points="999.00")
    )
    prova = Exam.objects.get(title="Prova Forjada")

    from decimal import Decimal

    assert prova.total_points == Decimal("0.00")


def test_post_nao_consegue_definir_a_versao_nem_a_linhagem(
    admin_client_logado, modulo, prova
):
    admin_client_logado.post(
        "/admin-panel/provas/nova/",
        _dados_de_prova(
            modulo, version="999", parent_exam=prova.pk, root_exam=prova.pk
        ),
    )
    forjada = Exam.objects.get(title="Prova Forjada")

    assert forjada.version == 1
    assert forjada.parent_exam_id is None
    assert forjada.root_exam_id is None


def test_post_nao_consegue_definir_o_hash_da_senha(admin_client_logado, modulo):
    admin_client_logado.post(
        "/admin-panel/provas/nova/",
        _dados_de_prova(modulo, access_password_hash="hash-injetado"),
    )
    prova = Exam.objects.get(title="Prova Forjada")

    assert prova.access_password_hash == ""
    assert prova.tem_senha is False


def test_post_nao_consegue_definir_datas_de_publicacao(admin_client_logado, modulo):
    from django.utils import timezone

    agora = timezone.now().isoformat()
    admin_client_logado.post(
        "/admin-panel/provas/nova/",
        _dados_de_prova(modulo, published_at=agora, closed_at=agora),
    )
    prova = Exam.objects.get(title="Prova Forjada")

    assert prova.published_at is None
    assert prova.closed_at is None


def test_post_nao_consegue_trocar_quem_criou(
    admin_client_logado, modulo, student_user, admin_user
):
    admin_client_logado.post(
        "/admin-panel/provas/nova/", _dados_de_prova(modulo, created_by=student_user.pk)
    )
    prova = Exam.objects.get(title="Prova Forjada")

    assert prova.created_by_id == admin_user.pk


def test_edicao_nao_consegue_mudar_status_nem_versao(
    admin_client_logado, prova, modulo
):
    resposta = admin_client_logado.post(
        "/admin-panel/provas/{}/editar/".format(prova.pk),
        _dados_de_prova(
            modulo, title="Editada", status=ExamStatus.CLOSED, version="42"
        ),
    )
    assert resposta.status_code == 302

    prova.refresh_from_db()
    assert prova.title == "Editada"
    assert prova.status == ExamStatus.DRAFT
    assert prova.version == 1


# ---------------------------------------------------------------------------
# IDOR
# ---------------------------------------------------------------------------


def test_questao_de_outra_prova_responde_404_na_edicao(
    admin_client_logado, prova_pronta, outro_modulo, admin_user
):
    """
    /provas/10/questoes/50/editar/ com a questao 50 pertencendo a outra
    prova precisa responder 404, e nao editar.
    """
    outra_prova = create_exam(module=outro_modulo, title="Outra", actor=admin_user)
    alheia = create_question(
        outra_prova,
        type=QuestionType.ESSAY,
        text="Questao alheia",
        points="1.00",
        actor=admin_user,
    )

    resposta = admin_client_logado.get(
        "/admin-panel/provas/{}/questoes/{}/editar/".format(prova_pronta.pk, alheia.pk)
    )
    assert resposta.status_code == 404


def test_questao_de_outra_prova_nao_pode_ser_editada_por_post(
    admin_client_logado, prova_pronta, outro_modulo, admin_user
):
    outra_prova = create_exam(module=outro_modulo, title="Outra", actor=admin_user)
    alheia = create_question(
        outra_prova,
        type=QuestionType.ESSAY,
        text="Questao alheia",
        points="1.00",
        actor=admin_user,
    )

    resposta = admin_client_logado.post(
        "/admin-panel/provas/{}/questoes/{}/editar/".format(prova_pronta.pk, alheia.pk),
        {"type": QuestionType.ESSAY, "text": "Adulterada", "points": "9.00"},
    )
    assert resposta.status_code == 404

    alheia.refresh_from_db()
    assert alheia.text == "Questao alheia"


def test_questao_de_outra_prova_nao_pode_ser_excluida(
    admin_client_logado, prova_pronta, outro_modulo, admin_user
):
    outra_prova = create_exam(module=outro_modulo, title="Outra", actor=admin_user)
    alheia = create_question(
        outra_prova,
        type=QuestionType.ESSAY,
        text="Questao alheia",
        points="1.00",
        actor=admin_user,
    )

    resposta = admin_client_logado.post(
        "/admin-panel/provas/{}/questoes/{}/excluir/".format(
            prova_pronta.pk, alheia.pk
        )
    )
    assert resposta.status_code == 404
    assert Question.objects.filter(pk=alheia.pk).exists()


def test_prova_inexistente_responde_404(admin_client_logado):
    for sufixo in ["", "editar/", "gabarito/", "preview/", "questoes/"]:
        resposta = admin_client_logado.get("/admin-panel/provas/999999/" + sufixo)
        assert resposta.status_code == 404, sufixo


def test_modulo_inexistente_no_post_e_recusado(admin_client_logado, modulo):
    resposta = admin_client_logado.post(
        "/admin-panel/provas/nova/", _dados_de_prova(modulo, module=999999)
    )
    assert resposta.status_code == 200
    assert not Exam.objects.filter(title="Prova Forjada").exists()


def test_modulo_inativo_no_post_e_recusado(
    admin_client_logado, modulo, modulo_inativo
):
    """
    O queryset do formulario ja exclui modulos inativos; o que importa e que
    nenhuma prova seja criada, qualquer que seja o caminho da recusa.
    """
    resposta = admin_client_logado.post(
        "/admin-panel/provas/nova/", _dados_de_prova(modulo, module=modulo_inativo.pk)
    )
    assert resposta.status_code == 200
    assert not Exam.objects.filter(title="Prova Forjada").exists()


# ---------------------------------------------------------------------------
# Django Admin
# ---------------------------------------------------------------------------


def test_django_admin_nao_permite_escrita_em_provas(admin_user):
    """
    O Django Admin nao pode ser um caminho paralelo capaz de publicar uma
    prova sem gabarito valido ou alterar o gabarito de uma prova aplicada.
    """
    from django.contrib import admin as django_admin

    from exams.models import QuestionOption

    for modelo in (Exam, Question, QuestionOption):
        registro = django_admin.site._registry[modelo]
        assert registro.has_add_permission(None) is False, modelo
        assert registro.has_change_permission(None) is False, modelo
        assert registro.has_delete_permission(None) is False, modelo


def test_django_admin_nao_expoe_o_hash_da_senha():
    from django.contrib import admin as django_admin

    registro = django_admin.site._registry[Exam]
    assert "access_password_hash" in registro.exclude
    assert "access_password_hash" not in registro.get_readonly_fields(None)
