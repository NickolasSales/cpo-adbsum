"""
Exclusao e arquivamento de provas (Etapa 9).

A pergunta que estes testes respondem e sempre a mesma: o sistema consegue
distinguir uma prova que nunca aconteceu de uma prova que aconteceu?

A primeira pode sumir. A segunda nao pode, sob nenhuma combinacao de status,
de janela ou de POST montado a mao.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEvent, AuditLog
from common.exceptions import DomainError
from exams import services
from exams.models import AttemptStatus, Exam, ExamAttempt, ExamStatus

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# can_delete_exam / delete_exam
# ---------------------------------------------------------------------------


def test_rascunho_sem_historico_pode_ser_excluido(prova, admin_user):
    assert services.can_delete_exam(prova) == []

    pk = prova.pk
    services.delete_exam(prova, actor=admin_user)

    assert not Exam.objects.filter(pk=pk).exists()


def test_publicada_sem_historico_pode_ser_excluida(prova_publicada, admin_user):
    """
    Publicar sem ninguem usar nao vira cicatriz permanente.

    Obrigar arquivo eterno so porque alguem clicou em publicar transformaria
    um clique em consequencia irreversivel, sem nenhum historico academico
    para justificar.
    """
    assert prova_publicada.status == ExamStatus.PUBLISHED
    assert services.can_delete_exam(prova_publicada) == []

    pk = prova_publicada.pk
    services.delete_exam(prova_publicada, actor=admin_user)

    assert not Exam.objects.filter(pk=pk).exists()


def test_fechada_sem_historico_pode_ser_excluida(prova_publicada, admin_user):
    fechada = services.close_exam(prova_publicada, actor=admin_user)
    assert fechada.status == ExamStatus.CLOSED
    assert services.can_delete_exam(fechada) == []

    pk = fechada.pk
    services.delete_exam(fechada, actor=admin_user)

    assert not Exam.objects.filter(pk=pk).exists()


def test_excluir_leva_questoes_e_alternativas_junto(prova_pronta, admin_user):
    from exams.models import Question, QuestionOption

    questoes = list(Question.objects.filter(exam=prova_pronta).values_list(
        "pk", flat=True
    ))
    assert questoes

    services.delete_exam(prova_pronta, actor=admin_user)

    assert not Question.objects.filter(pk__in=questoes).exists()
    assert not QuestionOption.objects.filter(question_id__in=questoes).exists()


@pytest.mark.parametrize(
    "situacao",
    [
        AttemptStatus.IN_PROGRESS,
        AttemptStatus.SUBMITTED,
        AttemptStatus.EXPIRED,
        AttemptStatus.RESET,
    ],
)
def test_qualquer_tentativa_impede_a_exclusao(tentativa, admin_user, situacao):
    """
    As quatro situacoes bloqueiam, inclusive RESET.

    Uma tentativa anulada continua sendo o registro de que aquele aluno fez
    aquela prova naquele dia — que e justamente o registro que justifica a
    anulacao.
    """
    agora = timezone.now()
    tentativa.status = situacao
    if situacao == AttemptStatus.SUBMITTED:
        tentativa.submitted_at = agora
    elif situacao == AttemptStatus.EXPIRED:
        tentativa.expired_at = agora
    elif situacao == AttemptStatus.RESET:
        # reset_at anda junto com o status por constraint desde a Etapa 7.
        tentativa.reset_at = agora
    tentativa.save()

    prova = tentativa.exam
    impedimentos = services.can_delete_exam(prova)
    assert impedimentos

    with pytest.raises(DomainError):
        services.delete_exam(prova, actor=admin_user)

    assert Exam.objects.filter(pk=prova.pk).exists()


def test_versao_com_descendente_nao_pode_ser_excluida(prova_publicada, admin_user):
    """v1 com v2 viva nao sai: a linhagem ficaria sem comeco."""
    services.duplicate_exam(prova_publicada, actor=admin_user)

    impedimentos = services.can_delete_exam(prova_publicada)
    assert impedimentos

    with pytest.raises(DomainError):
        services.delete_exam(prova_publicada, actor=admin_user)


def test_folha_da_linhagem_sem_historico_pode_ser_excluida(
    prova_publicada, admin_user
):
    """A ultima versao, que nao tem descendente, pode sair."""
    v2 = services.duplicate_exam(prova_publicada, actor=admin_user)

    assert services.can_delete_exam(v2) == []
    services.delete_exam(v2, actor=admin_user)

    assert not Exam.objects.filter(pk=v2.pk).exists()
    assert Exam.objects.filter(pk=prova_publicada.pk).exists()


def test_raiz_com_neto_nao_pode_ser_excluida(prova_publicada, admin_user):
    """
    v3 nasce da v2, mas aponta a raiz para a v1.

    Contar apenas parent_exam deixaria a raiz apagavel com netos vivos, e a
    numeracao da linhagem passaria a apontar para uma prova inexistente.
    """
    v2 = services.duplicate_exam(prova_publicada, actor=admin_user)
    v3 = services.duplicate_exam(v2, actor=admin_user)

    assert v3.root_exam_id == prova_publicada.pk
    assert v3.parent_exam_id == v2.pk

    services.delete_exam(v3, actor=admin_user)
    assert services.can_delete_exam(prova_publicada)


def test_certificado_impede_a_exclusao(tentativa, admin_user):
    """
    A checagem de certificado e redundante hoje, e proposital.

    Certificate.attempt e OneToOne com PROTECT: nao existe certificado sem
    tentativa, e a contagem de tentativas ja bloquearia. A verificacao
    separada sobrevive a uma mudanca futura desse desenho.
    """
    from certificates.models import Certificate

    services.expire_attempt(tentativa)
    Certificate.objects.create(
        attempt=tentativa,
        student_name_snapshot=tentativa.student.full_name,
        module_name_snapshot=tentativa.exam.module.name,
        exam_title_snapshot=tentativa.exam.title,
        institution_name_snapshot="CPO AD Bras Sumare",
    )

    impedimentos = services.can_delete_exam(tentativa.exam)
    assert any("certificado" in texto.lower() for texto in impedimentos)


def test_exclusao_registra_auditoria_antes_do_delete(prova, admin_user):
    """
    O evento sobrevive ao DELETE porque foi gravado na mesma transacao.

    A metadata leva titulo, versao e modulo — e nao as questoes: a trilha
    registra o ato, e nao e um backup obliquo do conteudo da prova.
    """
    pk = prova.pk
    titulo = prova.title

    services.delete_exam(prova, actor=admin_user)

    evento = AuditLog.objects.filter(
        event=AuditEvent.EXAM_DELETED, entity_id=str(pk)
    ).first()
    assert evento is not None
    assert evento.metadata["title"] == titulo
    assert evento.metadata["version"] == 1
    assert "questions" not in evento.metadata


def test_exclusao_recusada_nao_deixa_evento(tentativa, admin_user):
    """Trilha nunca afirma uma exclusao que nao aconteceu."""
    prova = tentativa.exam
    antes = AuditLog.objects.filter(event=AuditEvent.EXAM_DELETED).count()

    with pytest.raises(DomainError):
        services.delete_exam(prova, actor=admin_user)

    assert AuditLog.objects.filter(event=AuditEvent.EXAM_DELETED).count() == antes


# ---------------------------------------------------------------------------
# archive_exam
# ---------------------------------------------------------------------------


def test_arquivar_preserva_o_status_historico(tentativa, admin_user):
    """
    Arquivar nao sobrescreve PUBLISHED.

    O status conta o que a prova foi; arquivar diz que ela saiu da operacao.
    Fundir as duas coisas apagaria o fato de que a prova chegou a ser
    publicada e recebeu tentativas.
    """
    prova = tentativa.exam
    services.expire_attempt(tentativa)

    arquivada = services.archive_exam(
        prova, actor=admin_user, reason="Versao de homologacao."
    )

    assert arquivada.is_archived is True
    assert arquivada.status == ExamStatus.PUBLISHED
    assert arquivada.archived_at is not None
    assert arquivada.archived_by == admin_user
    assert arquivada.archive_reason == "Versao de homologacao."


def test_arquivar_preserva_o_historico_academico(tentativa, admin_user):
    prova = tentativa.exam
    services.expire_attempt(tentativa)

    services.archive_exam(prova, actor=admin_user, reason="Homologacao.")

    assert ExamAttempt.objects.filter(exam=prova).count() == 1
    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.EXPIRED


def test_arquivar_exige_motivo(prova_publicada, admin_user):
    with pytest.raises(DomainError):
        services.archive_exam(prova_publicada, actor=admin_user, reason="")
    with pytest.raises(DomainError):
        services.archive_exam(prova_publicada, actor=admin_user, reason="      ")

    prova_publicada.refresh_from_db()
    assert prova_publicada.is_archived is False


def test_arquivar_recusa_motivo_longo_demais(prova_publicada, admin_user):
    from common.texto import LIMITE_DO_MOTIVO

    with pytest.raises(DomainError):
        services.archive_exam(
            prova_publicada, actor=admin_user, reason="x" * (LIMITE_DO_MOTIVO + 1)
        )


def test_arquivar_recusa_com_tentativa_em_andamento(tentativa, admin_user):
    """
    Arquivar no meio da prova derrubaria o aluno em silencio.

    O caminho e finalizar, expirar ou resetar antes — tres acoes que deixam
    registro de quem decidiu.
    """
    assert tentativa.status == AttemptStatus.IN_PROGRESS

    with pytest.raises(DomainError) as erro:
        services.archive_exam(
            tentativa.exam, actor=admin_user, reason="Homologacao."
        )

    assert "em andamento" in str(erro.value)
    tentativa.exam.refresh_from_db()
    assert tentativa.exam.is_archived is False


def test_arquivar_duas_vezes_levanta_conflito(prova_publicada, admin_user):
    services.archive_exam(prova_publicada, actor=admin_user, reason="Homologacao.")

    with pytest.raises(services.ProvaJaArquivada):
        services.archive_exam(
            prova_publicada, actor=admin_user, reason="De novo."
        )

    assert (
        AuditLog.objects.filter(
            event=AuditEvent.EXAM_ARCHIVED, entity_id=str(prova_publicada.pk)
        ).count()
        == 1
    )


def test_auditoria_do_arquivamento_nao_repete_o_motivo(prova_publicada, admin_user):
    """O motivo ja esta em Exam.archive_reason; duas copias divergem."""
    services.archive_exam(
        prova_publicada, actor=admin_user, reason="Versao de homologacao."
    )

    evento = AuditLog.objects.filter(event=AuditEvent.EXAM_ARCHIVED).first()
    assert evento is not None
    assert "reason" not in evento.metadata
    assert "Versao de homologacao." not in str(evento.metadata)


# ---------------------------------------------------------------------------
# Efeito sobre o aluno
# ---------------------------------------------------------------------------


def test_prova_arquivada_nao_aparece_para_o_aluno(
    prova_aberta, aluno_matriculado, admin_user
):
    from exams.selectors import provas_do_modulo_para_aluno

    agora = timezone.now()
    antes = provas_do_modulo_para_aluno(
        prova_aberta.module, aluno_matriculado, agora=agora
    )
    assert antes

    services.archive_exam(prova_aberta, actor=admin_user, reason="Homologacao.")

    depois = provas_do_modulo_para_aluno(
        prova_aberta.module, aluno_matriculado, agora=agora
    )
    assert depois == []


def test_prova_arquivada_nao_inicia_tentativa(
    prova_aberta, aluno_matriculado, admin_user
):
    services.archive_exam(prova_aberta, actor=admin_user, reason="Homologacao.")

    with pytest.raises(DomainError) as erro:
        services.start_attempt(aluno_matriculado, prova_aberta)

    assert "arquivada" in str(erro.value).lower()
    assert not ExamAttempt.objects.filter(exam=prova_aberta).exists()


def test_start_reconsulta_o_arquivamento_no_banco(
    prova_aberta, aluno_matriculado, admin_user
):
    """
    Nao basta olhar o objeto recebido.

    Entre a montagem da tela e o POST alguem pode arquivar a prova, e a
    instancia que a view carregou ainda diria is_archived=False. Este teste
    reproduz exatamente esse cenario: o objeto em memoria esta desatualizado.
    """
    services.archive_exam(prova_aberta, actor=admin_user, reason="Homologacao.")

    desatualizada = Exam.objects.get(pk=prova_aberta.pk)
    desatualizada.is_archived = False  # so em memoria

    with pytest.raises(DomainError):
        services.start_attempt(aluno_matriculado, desatualizada)


def test_prova_arquivada_some_da_visao_do_aluno_pela_url(
    prova_aberta, aluno_matriculado, admin_user
):
    from exams.services import prova_visivel_ou_none

    assert prova_visivel_ou_none(aluno_matriculado, prova_aberta.pk) is not None

    services.archive_exam(prova_aberta, actor=admin_user, reason="Homologacao.")

    assert prova_visivel_ou_none(aluno_matriculado, prova_aberta.pk) is None


# ---------------------------------------------------------------------------
# unarchive_exam
# ---------------------------------------------------------------------------


def test_desarquivar_devolve_a_prova_a_operacao(prova_publicada, admin_user):
    services.archive_exam(prova_publicada, actor=admin_user, reason="Homologacao.")
    devolvida = services.unarchive_exam(prova_publicada, actor=admin_user)

    assert devolvida.is_archived is False
    assert devolvida.archived_at is None
    assert devolvida.archived_by is None
    assert devolvida.archive_reason == ""
    assert AuditLog.objects.filter(event=AuditEvent.EXAM_UNARCHIVED).count() == 1


def test_desarquivar_o_que_nao_esta_arquivado_levanta_conflito(
    prova_publicada, admin_user
):
    with pytest.raises(services.ProvaNaoArquivada):
        services.unarchive_exam(prova_publicada, actor=admin_user)


def test_desarquivar_nao_reabre_janela_encerrada(prova_pronta, admin_user):
    """
    Voltar a operacao nao e voltar a aceitar prova.

    Uma funcao chamada "desarquivar" que reabrisse a janela seria um bypass
    escondido, do mesmo tipo que o reset de tentativa evita desde a Etapa 7.
    """
    agora = timezone.now()
    services.update_exam(
        prova_pronta,
        module=prova_pronta.module,
        title=prova_pronta.title,
        description="",
        instructions="",
        open_at=agora - timedelta(days=2),
        close_at=agora - timedelta(days=1),
        duration_minutes=60,
        passing_score=prova_pronta.passing_score,
        max_attempts=1,
        failure_message="",
        randomize_questions=False,
        randomize_options=False,
        show_score_after_submission=True,
        actor=admin_user,
    )
    prova_pronta.refresh_from_db()
    publicada = services.publish_exam(prova_pronta, actor=admin_user)

    services.archive_exam(publicada, actor=admin_user, reason="Homologacao.")
    devolvida = services.unarchive_exam(publicada, actor=admin_user)

    assert devolvida.close_at < timezone.now()


# ---------------------------------------------------------------------------
# Constraint de coerencia
# ---------------------------------------------------------------------------


def test_banco_recusa_arquivada_sem_data(prova, admin_user):
    """
    A constraint cobre o que a aplicacao nao alcanca: UPDATE direto.

    Sem ela o banco aceitaria uma prova com is_archived=True e archived_at
    nulo — que a tela leria como arquivada e o relatorio nao saberia datar.
    """
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Exam.objects.filter(pk=prova.pk).update(is_archived=True)


def test_banco_recusa_data_sem_arquivamento(prova):
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Exam.objects.filter(pk=prova.pk).update(archived_at=timezone.now())


# ---------------------------------------------------------------------------
# Views: POST, CSRF, ADMIN, IDOR
# ---------------------------------------------------------------------------


ROTAS_DE_ESCRITA = [
    "admin_panel:exam_delete",
    "admin_panel:exam_archive",
    "admin_panel:exam_unarchive",
]


@pytest.mark.parametrize("rota", ROTAS_DE_ESCRITA)
def test_get_nas_rotas_de_escrita_devolve_405(admin_client_logado, prova, rota):
    resposta = admin_client_logado.get(reverse(rota, args=[prova.pk]))
    assert resposta.status_code == 405


@pytest.mark.parametrize("rota", ROTAS_DE_ESCRITA)
def test_aluno_nao_acessa_as_rotas_de_escrita(student_client_logado, prova, rota):
    resposta = student_client_logado.post(reverse(rota, args=[prova.pk]))
    assert resposta.status_code == 403


@pytest.mark.parametrize("rota", ROTAS_DE_ESCRITA)
def test_anonimo_e_mandado_para_o_login(client, prova, rota):
    resposta = client.post(reverse(rota, args=[prova.pk]))
    assert resposta.status_code == 302
    assert "/login/" in resposta["Location"]


def test_post_sem_csrf_e_recusado(admin_user, prova, senha_padrao):
    from django.test import Client

    cliente = Client(enforce_csrf_checks=True)
    cliente.force_login(admin_user)

    resposta = cliente.post(reverse("admin_panel:exam_delete", args=[prova.pk]))

    assert resposta.status_code == 403
    assert Exam.objects.filter(pk=prova.pk).exists()


def test_id_inexistente_devolve_404(admin_client_logado):
    resposta = admin_client_logado.post(
        reverse("admin_panel:exam_delete", args=[999999])
    )
    assert resposta.status_code == 404


def test_post_forjado_em_prova_com_historico_e_recusado(
    admin_client_logado, tentativa
):
    """
    Botao escondido nao e protecao.

    A lista nem oferece "Excluir" para uma prova com tentativa, mas o POST
    montado a mao precisa esbarrar no servico.
    """
    prova = tentativa.exam
    resposta = admin_client_logado.post(
        reverse("admin_panel:exam_delete", args=[prova.pk])
    )

    assert resposta.status_code == 409
    assert Exam.objects.filter(pk=prova.pk).exists()


def test_frontend_nao_escolhe_os_campos_de_arquivamento(
    admin_client_logado, prova_publicada, admin_user
):
    """
    Mass assignment: o POST manda is_archived, archived_by e archived_at, e
    nenhum dos tres chega ao banco pelo valor enviado.
    """
    outro = admin_user
    resposta = admin_client_logado.post(
        reverse("admin_panel:exam_archive", args=[prova_publicada.pk]),
        {
            "motivo": "Homologacao.",
            "is_archived": "false",
            "archived_by": outro.pk + 500,
            "archived_at": "1999-01-01T00:00:00Z",
            "status": "DRAFT",
        },
    )
    assert resposta.status_code == 302

    prova_publicada.refresh_from_db()
    assert prova_publicada.is_archived is True
    assert prova_publicada.archived_at.year >= 2024
    assert prova_publicada.archived_by_id == outro.pk
    assert prova_publicada.status == ExamStatus.PUBLISHED


def test_arquivar_sem_motivo_pela_view_nao_arquiva(
    admin_client_logado, prova_publicada
):
    resposta = admin_client_logado.post(
        reverse("admin_panel:exam_archive", args=[prova_publicada.pk]),
        {"motivo": "   "},
    )

    assert resposta.status_code == 302
    prova_publicada.refresh_from_db()
    assert prova_publicada.is_archived is False


def test_segunda_tentativa_de_arquivar_pela_view_devolve_409(
    admin_client_logado, prova_publicada
):
    dados = {"motivo": "Homologacao."}
    url = reverse("admin_panel:exam_archive", args=[prova_publicada.pk])

    assert admin_client_logado.post(url, dados).status_code == 302
    assert admin_client_logado.post(url, dados).status_code == 409


# ---------------------------------------------------------------------------
# Views: telas de confirmacao e lista
# ---------------------------------------------------------------------------


def test_tela_de_confirmacao_de_exclusao_mostra_o_formulario(
    admin_client_logado, prova
):
    resposta = admin_client_logado.get(
        reverse("admin_panel:exam_delete_confirm", args=[prova.pk])
    )
    conteudo = resposta.content.decode()

    assert resposta.status_code == 200
    assert "nao podera ser desfeita" in conteudo
    assert reverse("admin_panel:exam_delete", args=[prova.pk]) in conteudo


def test_tela_de_confirmacao_esconde_o_formulario_quando_ha_impedimento(
    admin_client_logado, tentativa
):
    """
    Sem formulario quando o servico ja recusaria.

    Mostrar um botao que so serve para produzir um 409 seria convidar o
    administrador a errar.
    """
    prova = tentativa.exam
    resposta = admin_client_logado.get(
        reverse("admin_panel:exam_delete_confirm", args=[prova.pk])
    )
    conteudo = resposta.content.decode()

    assert resposta.status_code == 200
    assert "nao esta disponivel" in conteudo
    assert reverse("admin_panel:exam_delete", args=[prova.pk]) not in conteudo


def test_confirmacao_de_arquivamento_pede_motivo(admin_client_logado, tentativa):
    prova = tentativa.exam
    services.expire_attempt(tentativa)

    resposta = admin_client_logado.get(
        reverse("admin_panel:exam_archive_confirm", args=[prova.pk])
    )
    conteudo = resposta.content.decode()

    assert 'name="motivo"' in conteudo
    assert "required" in conteudo


def test_lista_esconde_arquivadas_por_padrao(
    admin_client_logado, prova_publicada, admin_user
):
    services.archive_exam(prova_publicada, actor=admin_user, reason="Homologacao.")

    resposta = admin_client_logado.get(reverse("admin_panel:exam_list"))
    conteudo = resposta.content.decode()

    assert prova_publicada.title not in conteudo


def test_filtro_arquivadas_mostra_a_prova_com_badge(
    admin_client_logado, prova_publicada, admin_user
):
    services.archive_exam(prova_publicada, actor=admin_user, reason="Homologacao.")

    resposta = admin_client_logado.get(
        reverse("admin_panel:exam_list"), {"arquivamento": "arquivadas"}
    )
    conteudo = resposta.content.decode()

    assert prova_publicada.title in conteudo
    # Texto, e nao apenas cor.
    assert "Arquivada" in conteudo


def test_filtro_todas_mostra_as_duas(
    admin_client_logado, prova_publicada, prova, admin_user
):
    services.archive_exam(prova_publicada, actor=admin_user, reason="Homologacao.")

    resposta = admin_client_logado.get(
        reverse("admin_panel:exam_list"), {"arquivamento": "todas"}
    )
    conteudo = resposta.content.decode()

    assert prova_publicada.title in conteudo
    assert prova.title in conteudo


def test_filtro_invalido_cai_no_padrao(
    admin_client_logado, prova_publicada, admin_user
):
    """Valor inventado na querystring nao vira filtro nem erro."""
    services.archive_exam(prova_publicada, actor=admin_user, reason="Homologacao.")

    resposta = admin_client_logado.get(
        reverse("admin_panel:exam_list"), {"arquivamento": "'; DROP TABLE"}
    )

    assert resposta.status_code == 200
    assert prova_publicada.title not in resposta.content.decode()


def test_lista_oferece_excluir_para_prova_sem_historico(admin_client_logado, prova):
    resposta = admin_client_logado.get(reverse("admin_panel:exam_list"))
    conteudo = resposta.content.decode()

    assert reverse("admin_panel:exam_delete_confirm", args=[prova.pk]) in conteudo
    assert reverse("admin_panel:exam_archive_confirm", args=[prova.pk]) not in conteudo


def test_lista_oferece_arquivar_para_prova_com_historico(
    admin_client_logado, tentativa
):
    prova = tentativa.exam
    resposta = admin_client_logado.get(reverse("admin_panel:exam_list"))
    conteudo = resposta.content.decode()

    assert reverse("admin_panel:exam_archive_confirm", args=[prova.pk]) in conteudo
    assert reverse("admin_panel:exam_delete_confirm", args=[prova.pk]) not in conteudo


def test_sem_dependencias_e_falso_sem_as_anotacoes(prova):
    """
    Fail-closed: sem a anotacao da lista, a tela nao oferece a exclusao.

    Errar escondendo um botao e inofensivo; errar mostrando um botao que
    apaga, nao.
    """
    crua = Exam.objects.get(pk=prova.pk)
    assert crua.sem_dependencias is False
