"""
Tela de logs: leitura, e a ausencia de tudo o mais.

Boa parte deste arquivo verifica que certas rotas NAO existem. Uma trilha
alteravel pela mesma interface que ela audita nao serve para investigar nada:
quem quisesse esconder uma acao apagaria a linha logo depois de executa-la.
"""

import pytest
from django.urls import NoReverseMatch, reverse

from audit.models import AuditEvent, AuditLog

pytestmark = pytest.mark.django_db


def url(nome, *args):
    return reverse("admin_panel:{}".format(nome), args=args)


@pytest.fixture
def eventos(db, admin_user, student_user):
    """Alguns registros de tipos diferentes, para exercitar os filtros."""
    from audit.services import record

    criados = []
    for indice in range(5):
        criados.append(
            record(
                AuditEvent.STUDENT_CREATED,
                actor=admin_user,
                student=student_user,
                entity_type="User",
                entity_id=student_user.pk,
                metadata={"origem": "teste", "indice": indice},
            )
        )
    criados.append(
        record(
            AuditEvent.LOGIN_FAILED,
            entity_type="User",
            entity_id="",
            metadata={"tentativas": 1},
        )
    )
    return criados


# ---------------------------------------------------------------------------
# Acesso
# ---------------------------------------------------------------------------


def test_o_admin_ve_a_trilha(admin_client_logado, eventos):
    resposta = admin_client_logado.get(url("audit_log_list"))
    corpo = resposta.content.decode("utf-8")

    assert resposta.status_code == 200
    assert "Aluno criado" in corpo


def test_aluno_recebe_403(student_client_logado, eventos):
    assert student_client_logado.get(url("audit_log_list")).status_code == 403


def test_anonimo_vai_para_o_login(client, eventos):
    resposta = client.get(url("audit_log_list"))
    assert resposta.status_code == 302
    assert "/login/" in resposta["Location"]


def test_detalhe_exige_admin(student_client_logado, eventos):
    assert (
        student_client_logado.get(url("audit_log_detail", eventos[0].pk)).status_code
        == 403
    )


def test_registro_inexistente_responde_404(admin_client_logado):
    assert admin_client_logado.get(url("audit_log_detail", 999999)).status_code == 404


# ---------------------------------------------------------------------------
# Somente leitura
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "nome", ["audit_log_delete", "audit_log_update", "audit_log_clear"]
)
def test_nao_existe_rota_de_escrita(nome):
    with pytest.raises(NoReverseMatch):
        reverse("admin_panel:{}".format(nome), args=[1])


def test_post_na_listagem_nao_e_aceito(admin_client_logado, eventos):
    """ListView so responde GET; um POST recebe 405."""
    assert admin_client_logado.post(url("audit_log_list")).status_code == 405


def test_post_no_detalhe_nao_e_aceito(admin_client_logado, eventos):
    assert (
        admin_client_logado.post(url("audit_log_detail", eventos[0].pk)).status_code
        == 405
    )


def test_a_tela_nao_apaga_nada(admin_client_logado, eventos):
    antes = AuditLog.objects.count()
    admin_client_logado.get(url("audit_log_list"))
    admin_client_logado.get(url("audit_log_detail", eventos[0].pk))

    assert AuditLog.objects.count() == antes


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------


def test_filtro_por_evento(admin_client_logado, eventos):
    resposta = admin_client_logado.get(
        url("audit_log_list"), {"evento": AuditEvent.LOGIN_FAILED}
    )
    # A conferencia e sobre as LINHAS, e nao sobre o corpo inteiro: o <select>
    # do filtro lista todos os rotulos de evento, inclusive os excluidos.
    eventos_na_pagina = {r.event for r in resposta.context["pagina"]}

    assert eventos_na_pagina == {AuditEvent.LOGIN_FAILED}


def test_filtro_por_ator(admin_client_logado, eventos, admin_user):
    corpo = admin_client_logado.get(
        url("audit_log_list"), {"ator": admin_user.email}
    ).content.decode("utf-8")

    assert "Aluno criado" in corpo


def test_filtro_por_ator_sem_resultado(admin_client_logado, eventos):
    corpo = admin_client_logado.get(
        url("audit_log_list"), {"ator": "ninguem@lugar.nenhum"}
    ).content.decode("utf-8")

    assert "Nenhum registro encontrado" in corpo


def test_filtro_por_entidade(admin_client_logado, eventos):
    resposta = admin_client_logado.get(url("audit_log_list"), {"entidade": "User"})
    assert resposta.status_code == 200


def test_data_invalida_nao_derruba_a_tela(admin_client_logado, eventos):
    resposta = admin_client_logado.get(
        url("audit_log_list"), {"de": "31/02", "ate": "xx"}
    )
    assert resposta.status_code == 200


# ---------------------------------------------------------------------------
# Paginacao e consultas
# ---------------------------------------------------------------------------


def test_a_listagem_e_paginada(admin_client_logado, admin_user, student_user):
    from audit.services import record

    for indice in range(60):
        record(
            AuditEvent.STUDENT_UPDATED,
            actor=admin_user,
            student=student_user,
            entity_type="User",
            entity_id=student_user.pk,
            metadata={"i": indice},
        )

    resposta = admin_client_logado.get(url("audit_log_list"))

    # "pagina" e a lista da pagina atual; quem tem o paginador e page_obj.
    assert resposta.context["page_obj"].paginator.num_pages > 1
    assert len(resposta.context["pagina"]) == 50


def test_a_listagem_nao_consulta_o_ator_registro_a_registro(
    admin_client_logado, admin_user, student_user, django_assert_max_num_queries
):
    """
    select_related no ator e no aluno.

    Sem ele, uma pagina de 50 eventos faria ate 100 consultas extras so para
    escrever nomes — e a trilha e a tabela que mais cresce no sistema.
    """
    from audit.services import record

    for indice in range(50):
        record(
            AuditEvent.STUDENT_UPDATED,
            actor=admin_user,
            student=student_user,
            entity_type="User",
            entity_id=student_user.pk,
            metadata={"i": indice},
        )

    with django_assert_max_num_queries(12):
        admin_client_logado.get(url("audit_log_list"))


def test_a_ordem_e_do_mais_recente_para_o_mais_antigo(admin_client_logado, eventos):
    pagina = admin_client_logado.get(url("audit_log_list")).context["pagina"]
    datas = [registro.timestamp for registro in pagina]

    assert datas == sorted(datas, reverse=True)


# ---------------------------------------------------------------------------
# Metadata como dado
# ---------------------------------------------------------------------------


def test_a_metadata_aparece_no_detalhe(admin_client_logado, eventos):
    corpo = admin_client_logado.get(
        url("audit_log_detail", eventos[0].pk)
    ).content.decode("utf-8")

    assert "origem" in corpo
    assert "teste" in corpo


def test_metadata_com_script_e_escapada(admin_client_logado, admin_user):
    """
    Parte da metadata vem de campo que uma pessoa preencheu. Um <script>
    gravado meses atras executaria justamente na tela de quem esta
    investigando aquele evento.
    """
    from audit.services import record

    evento = record(
        AuditEvent.MODULE_UPDATED,
        actor=admin_user,
        entity_type="Module",
        entity_id=1,
        metadata={"nome": "<script>alert(1)</script>"},
    )

    corpo = admin_client_logado.get(
        url("audit_log_detail", evento.pk)
    ).content.decode("utf-8")

    assert "<script>alert(1)</script>" not in corpo
    assert "&lt;script&gt;" in corpo


def test_metadata_aninhada_e_achatada(admin_client_logado, admin_user):
    from audit.services import record

    evento = record(
        AuditEvent.MODULE_UPDATED,
        actor=admin_user,
        entity_type="Module",
        entity_id=1,
        metadata={"dentro": {"chave": "valor"}, "lista": [1, 2]},
    )

    corpo = admin_client_logado.get(
        url("audit_log_detail", evento.pk)
    ).content.decode("utf-8")

    assert "dentro.chave" in corpo
    assert "lista[0]" in corpo


def test_evento_sem_metadata_nao_quebra(admin_client_logado, admin_user):
    from audit.services import record

    evento = record(AuditEvent.LOGIN_SUCCESS, actor=admin_user)

    resposta = admin_client_logado.get(url("audit_log_detail", evento.pk))
    assert resposta.status_code == 200
    assert "nao possui metadados" in resposta.content.decode("utf-8")


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------


def test_logs_aparece_no_menu_e_nao_como_promessa(admin_client_logado):
    corpo = admin_client_logado.get(
        reverse("admin_panel:dashboard")
    ).content.decode("utf-8")

    # Uma vez na lateral, outra no offcanvas do celular.
    assert corpo.count(">Logs</a>") == 2
    # A lista de itens futuros ficou vazia: nada no menu promete tela que nao
    # existe. A legenda so aparece quando ha item futuro.
    assert "cpo-lateral__legenda" not in corpo
