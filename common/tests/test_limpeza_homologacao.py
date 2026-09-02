"""
Comando limpar_dados_homologacao (Etapa 9).

Este e o unico codigo do sistema que apaga historico academico de verdade, e
por isso os testes se concentram menos em "ele apaga?" e mais em "ele se
recusa a apagar o que nao foi pedido?".

Quatro barreiras precisam cair juntas para uma linha sumir: filtro
especifico, --execute, a frase de confirmacao exata, e uma transacao que ou
vai inteira ou nao vai.
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from audit.models import AuditEvent, AuditLog
from courses.models import Enrollment, EnrollmentStatus
from exams.models import Answer, AnswerOption, AttemptQuestion, ExamAttempt

pytestmark = pytest.mark.django_db

FRASE = "APAGAR-DADOS-DE-HOMOLOGACAO"


def executar(**opcoes):
    saida = StringIO()
    call_command("limpar_dados_homologacao", stdout=saida, **opcoes)
    return saida.getvalue()


@pytest.fixture
def tentativa_respondida(tentativa, tokens):
    """Tentativa com respostas gravadas, para que haja o que contar."""
    from exams.services import autosave_answer
    from exams.models import QuestionType

    questao, alternativas = tokens[QuestionType.SINGLE_CHOICE]
    autosave_answer(
        tentativa, question_token=questao, option_tokens=[alternativas[0]]
    )
    questao, _ = tokens[QuestionType.ESSAY]
    autosave_answer(tentativa, question_token=questao, text="Resposta de teste.")
    return tentativa


# ---------------------------------------------------------------------------
# Barreiras
# ---------------------------------------------------------------------------


def test_dry_run_e_o_padrao_e_nao_altera_nada(tentativa_respondida):
    aluno = tentativa_respondida.student
    modulo = tentativa_respondida.exam.module

    saida = executar(student_email=aluno.email, module_code=modulo.code)

    assert "DRY RUN" in saida
    assert ExamAttempt.objects.filter(pk=tentativa_respondida.pk).exists()
    assert Answer.objects.exists()


def test_dry_run_informa_as_quantidades_reais(tentativa_respondida):
    """
    Os numeros vem da contagem de agora, e nao de uma execucao anterior.

    Um relatorio com numero errado seria pior do que nenhum relatorio: ele
    autorizaria uma execucao sobre um escopo que o operador nao conferiu.
    """
    aluno = tentativa_respondida.student
    modulo = tentativa_respondida.exam.module

    saida = executar(student_email=aluno.email, module_code=modulo.code)

    respostas = Answer.objects.filter(
        attempt_question__attempt=tentativa_respondida
    ).count()

    assert "Tentativas .................... 1" in saida
    assert "Respostas ..................... {}".format(respostas) in saida


def test_execute_sem_confirm_e_recusado(tentativa_respondida):
    aluno = tentativa_respondida.student
    modulo = tentativa_respondida.exam.module

    with pytest.raises(CommandError) as erro:
        executar(
            student_email=aluno.email, module_code=modulo.code, execute=True
        )

    assert FRASE in str(erro.value)
    assert ExamAttempt.objects.filter(pk=tentativa_respondida.pk).exists()


def test_confirm_parecido_nao_serve(tentativa_respondida):
    """A frase e exata: nem minuscula, nem com espaco a mais."""
    aluno = tentativa_respondida.student
    modulo = tentativa_respondida.exam.module

    for quase in (FRASE.lower(), FRASE + " ", "APAGAR", ""):
        with pytest.raises(CommandError):
            executar(
                student_email=aluno.email,
                module_code=modulo.code,
                execute=True,
                confirm=quase,
            )

    assert ExamAttempt.objects.filter(pk=tentativa_respondida.pk).exists()


def test_execute_sem_filtro_de_modulo_ou_prova_e_recusado(tentativa_respondida):
    """
    A barreira que impede o comando de virar TRUNCATE.

    Com --execute e a frase certa, mas sem alvo, ele apagaria o historico da
    instituicao inteira e teria funcionado exatamente como pedido.
    """
    aluno = tentativa_respondida.student

    with pytest.raises(CommandError) as erro:
        executar(student_email=aluno.email, execute=True, confirm=FRASE)

    assert "Filtro insuficiente" in str(erro.value)
    assert ExamAttempt.objects.filter(pk=tentativa_respondida.pk).exists()


def test_dry_run_sem_filtro_tambem_e_recusado(tentativa_respondida):
    """A validacao acontece antes de qualquer consulta, inclusive no dry run."""
    with pytest.raises(CommandError):
        executar(student_email=tentativa_respondida.student.email)


def test_email_sem_dono_e_recusado():
    with pytest.raises(CommandError) as erro:
        executar(student_email="ninguem@exemplo.invalid", module_code="MOD1")

    assert "Nenhum usuario" in str(erro.value)


def test_modulo_inexistente_e_recusado(student_user):
    with pytest.raises(CommandError) as erro:
        executar(student_email=student_user.email, module_code="NAOEXISTE")

    assert "Nenhum modulo" in str(erro.value)


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------


def test_execucao_remove_tentativas_respostas_e_marcacoes(tentativa_respondida):
    aluno = tentativa_respondida.student
    modulo = tentativa_respondida.exam.module
    pk = tentativa_respondida.pk

    assert Answer.objects.exists()

    executar(
        student_email=aluno.email,
        module_code=modulo.code,
        execute=True,
        confirm=FRASE,
    )

    assert not ExamAttempt.objects.filter(pk=pk).exists()
    assert not AttemptQuestion.objects.filter(attempt_id=pk).exists()
    assert not Answer.objects.exists()
    assert not AnswerOption.objects.exists()


def test_execucao_remove_o_certificado_de_teste(tentativa, admin_user):
    from certificates.models import Certificate
    from exams.services import expire_attempt

    expire_attempt(tentativa)
    Certificate.objects.create(
        attempt=tentativa,
        student_name_snapshot=tentativa.student.full_name,
        module_name_snapshot=tentativa.exam.module.name,
        exam_title_snapshot=tentativa.exam.title,
        institution_name_snapshot="CPO AD Bras Sumare",
    )

    executar(
        student_email=tentativa.student.email,
        module_code=tentativa.exam.module.code,
        execute=True,
        confirm=FRASE,
    )

    assert not Certificate.objects.exists()
    assert not ExamAttempt.objects.filter(pk=tentativa.pk).exists()


def test_filtro_por_titulo_da_prova_restringe(tentativa_respondida):
    aluno = tentativa_respondida.student
    modulo = tentativa_respondida.exam.module

    executar(
        student_email=aluno.email,
        module_code=modulo.code,
        exam_title="Prova Que Nao Existe",
        execute=True,
        confirm=FRASE,
    )

    assert ExamAttempt.objects.filter(pk=tentativa_respondida.pk).exists()

    executar(
        student_email=aluno.email,
        module_code=modulo.code,
        exam_title=tentativa_respondida.exam.title,
        execute=True,
        confirm=FRASE,
    )

    assert not ExamAttempt.objects.filter(pk=tentativa_respondida.pk).exists()


def test_nada_a_remover_nao_e_erro(matricula):
    saida = executar(
        student_email=matricula.student.email,
        module_code=matricula.module.code,
        execute=True,
        confirm=FRASE,
    )

    assert "Nada a remover." in saida


# ---------------------------------------------------------------------------
# Escopo: o aluno B fica intacto
# ---------------------------------------------------------------------------


def test_purge_nao_toca_no_aluno_que_nao_e_alvo(
    prova_aberta, matricula, outro_student, admin_user
):
    """
    Dois alunos na mesma prova. Um e alvo, o outro nao.

    O conjunto alvo comeca em student= e so estreita dali: nao existe
    combinacao de opcoes que faca ele incluir a tentativa de outra pessoa.
    """
    from courses.services import create_enrollment
    from exams.services import start_attempt

    alvo = matricula.student
    create_enrollment(student=outro_student, module=prova_aberta.module)

    tentativa_do_alvo = start_attempt(alvo, prova_aberta)
    tentativa_do_outro = start_attempt(outro_student, prova_aberta)

    executar(
        student_email=alvo.email,
        module_code=prova_aberta.module.code,
        execute=True,
        confirm=FRASE,
    )

    assert not ExamAttempt.objects.filter(pk=tentativa_do_alvo.pk).exists()
    assert ExamAttempt.objects.filter(pk=tentativa_do_outro.pk).exists()


def test_purge_nao_toca_no_certificado_de_outro_aluno(
    prova_aberta, matricula, outro_student
):
    from certificates.models import Certificate
    from courses.services import create_enrollment
    from exams.services import expire_attempt, start_attempt

    alvo = matricula.student
    create_enrollment(student=outro_student, module=prova_aberta.module)

    do_alvo = start_attempt(alvo, prova_aberta)
    do_outro = start_attempt(outro_student, prova_aberta)
    expire_attempt(do_alvo)
    expire_attempt(do_outro)

    for tentativa in (do_alvo, do_outro):
        Certificate.objects.create(
            attempt=tentativa,
            student_name_snapshot=tentativa.student.full_name,
            module_name_snapshot=tentativa.exam.module.name,
            exam_title_snapshot=tentativa.exam.title,
            institution_name_snapshot="CPO AD Bras Sumare",
        )

    executar(
        student_email=alvo.email,
        module_code=prova_aberta.module.code,
        execute=True,
        confirm=FRASE,
    )

    assert not Certificate.objects.filter(attempt=do_alvo).exists()
    assert Certificate.objects.filter(attempt=do_outro).exists()


def test_purge_nao_toca_em_outro_modulo_do_mesmo_aluno(
    tentativa_respondida, outro_modulo, admin_user
):
    from courses.services import create_enrollment
    from decimal import Decimal
    from exams.services import create_exam, publish_exam, start_attempt

    aluno = tentativa_respondida.student
    create_enrollment(student=aluno, module=outro_modulo)

    agora = timezone.now()
    from datetime import timedelta

    outra_prova = create_exam(
        module=outro_modulo,
        title="Avaliacao Modulo 2",
        open_at=agora - timedelta(hours=1),
        close_at=agora + timedelta(hours=3),
        duration_minutes=60,
        passing_score=Decimal("8.00"),
        max_attempts=1,
        actor=admin_user,
    )
    from exams.models import QuestionType
    from exams.services import create_question

    create_question(
        outra_prova,
        type=QuestionType.SINGLE_CHOICE,
        text="Pergunta do modulo 2?",
        points=Decimal("10.00"),
        order=1,
        opcoes=[
            {"text": "Certa", "is_correct": True},
            {"text": "Errada", "is_correct": False},
        ],
        actor=admin_user,
    )
    outra_prova = publish_exam(outra_prova, actor=admin_user)
    do_outro_modulo = start_attempt(aluno, outra_prova)

    executar(
        student_email=aluno.email,
        module_code=tentativa_respondida.exam.module.code,
        execute=True,
        confirm=FRASE,
    )

    assert not ExamAttempt.objects.filter(pk=tentativa_respondida.pk).exists()
    assert ExamAttempt.objects.filter(pk=do_outro_modulo.pk).exists()


# ---------------------------------------------------------------------------
# O que o comando nunca apaga
# ---------------------------------------------------------------------------


def test_auditlog_e_preservado(tentativa_respondida):
    """
    A trilha e append-only, e os eventos de teste ficam como evidencia de que
    os testes aconteceram.
    """
    aluno = tentativa_respondida.student
    antes = AuditLog.objects.count()
    assert antes > 0

    executar(
        student_email=aluno.email,
        module_code=tentativa_respondida.exam.module.code,
        execute=True,
        confirm=FRASE,
    )

    # Cresceu, e nao encolheu: o evento da propria limpeza entrou.
    assert AuditLog.objects.count() == antes + 1
    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_STARTED).exists()


def test_aluno_modulo_e_prova_sobrevivem(tentativa_respondida):
    from courses.models import Module
    from exams.models import Exam

    aluno = tentativa_respondida.student
    modulo = tentativa_respondida.exam.module
    prova = tentativa_respondida.exam

    executar(
        student_email=aluno.email,
        module_code=modulo.code,
        execute=True,
        confirm=FRASE,
    )

    aluno.refresh_from_db()
    assert Module.objects.filter(pk=modulo.pk).exists()
    assert Exam.objects.filter(pk=prova.pk).exists()
    assert Enrollment.objects.filter(student=aluno, module=modulo).exists()


def test_evento_da_limpeza_registra_o_que_foi_removido(tentativa_respondida):
    """
    Actor nulo de proposito: um comando de gestao nao tem sessao web, e
    atribuir a acao a um usuario qualquer registraria um autor que nao clicou
    em nada. A metadata tambem nao guarda o e-mail — student_id ja identifica.
    """
    aluno = tentativa_respondida.student
    modulo = tentativa_respondida.exam.module

    executar(
        student_email=aluno.email,
        module_code=modulo.code,
        execute=True,
        confirm=FRASE,
    )

    evento = AuditLog.objects.filter(
        event=AuditEvent.HOMOLOGATION_DATA_PURGED
    ).first()
    assert evento is not None
    assert evento.actor is None
    assert evento.student_id == aluno.pk
    assert evento.metadata["module_code"] == modulo.code
    assert evento.metadata["attempts_removed"] == 1
    assert aluno.email not in str(evento.metadata)


def test_prova_nao_e_apagada_mas_passa_a_ser_apagavel(tentativa_respondida):
    """
    O comando nao apaga a prova. Depois da limpeza ela pode passar a atender
    as regras de exclusao, e ai o administrador a remove pela interface — que
    e o caminho auditado.
    """
    from exams.services import can_delete_exam

    prova = tentativa_respondida.exam
    assert can_delete_exam(prova)

    executar(
        student_email=tentativa_respondida.student.email,
        module_code=prova.module.code,
        execute=True,
        confirm=FRASE,
    )

    prova.refresh_from_db()
    assert can_delete_exam(prova) == []


# ---------------------------------------------------------------------------
# Transacao
# ---------------------------------------------------------------------------


def test_erro_no_meio_desfaz_tudo(tentativa_respondida, monkeypatch):
    """
    Rollback integral, o evento de auditoria incluido.

    A alternativa seria uma trilha afirmando uma limpeza que o banco desfez.
    """
    from common.management.commands import limpar_dados_homologacao as comando

    def explodir(*args, **kwargs):
        raise RuntimeError("falha simulada depois do delete")

    # O alvo e o nome ligado DENTRO do comando, e nao audit.services.record.
    # O comando faz `from audit.services import record` no topo, entao trocar
    # o atributo em audit.services nao alcanca a referencia ja resolvida — e
    # o teste passaria ou falharia conforme a ordem de importacao.
    monkeypatch.setattr(comando, "record", explodir)

    aluno = tentativa_respondida.student
    antes = AuditLog.objects.count()

    with pytest.raises(RuntimeError):
        executar(
            student_email=aluno.email,
            module_code=tentativa_respondida.exam.module.code,
            execute=True,
            confirm=FRASE,
        )

    assert ExamAttempt.objects.filter(pk=tentativa_respondida.pk).exists()
    assert Answer.objects.exists()
    assert AuditLog.objects.count() == antes


# ---------------------------------------------------------------------------
# Reativacao da matricula
# ---------------------------------------------------------------------------


def _com_certificado_e_matricula_concluida(tentativa, admin_user):
    from certificates.models import Certificate
    from courses.services import complete_enrollment
    from exams.services import expire_attempt

    expire_attempt(tentativa)
    Certificate.objects.create(
        attempt=tentativa,
        student_name_snapshot=tentativa.student.full_name,
        module_name_snapshot=tentativa.exam.module.name,
        exam_title_snapshot=tentativa.exam.title,
        institution_name_snapshot="CPO AD Bras Sumare",
    )
    matricula = Enrollment.objects.get(
        student=tentativa.student, module=tentativa.exam.module
    )
    return complete_enrollment(matricula, encerrar_acesso=True, actor=admin_user)


def test_matricula_nao_e_reativada_sem_a_opcao(tentativa, admin_user):
    """Nunca acontece silenciosamente."""
    matricula = _com_certificado_e_matricula_concluida(tentativa, admin_user)

    executar(
        student_email=tentativa.student.email,
        module_code=tentativa.exam.module.code,
        execute=True,
        confirm=FRASE,
    )

    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.COMPLETED
    assert matricula.access_enabled is False


def test_matricula_e_reativada_com_a_opcao(tentativa, admin_user):
    matricula = _com_certificado_e_matricula_concluida(tentativa, admin_user)

    saida = executar(
        student_email=tentativa.student.email,
        module_code=tentativa.exam.module.code,
        execute=True,
        confirm=FRASE,
        reactivate_enrollment=True,
    )

    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.ACTIVE
    assert matricula.access_enabled is True
    assert "Matricula sera reativada ...... SIM" in saida


def test_dry_run_informa_se_a_matricula_seria_reativada(tentativa, admin_user):
    _com_certificado_e_matricula_concluida(tentativa, admin_user)

    com = executar(
        student_email=tentativa.student.email,
        module_code=tentativa.exam.module.code,
        reactivate_enrollment=True,
    )
    sem = executar(
        student_email=tentativa.student.email,
        module_code=tentativa.exam.module.code,
    )

    assert "Matricula sera reativada ...... SIM" in com
    assert "Matricula sera reativada ...... NAO" in sem


def test_nao_reativa_se_restar_outro_certificado_ativo(
    prova_aberta, matricula, admin_user
):
    """
    A condicao que mais importa.

    Se o aluno tiver um segundo certificado valido no modulo, ele continua com
    comprovacao de conclusao, e reabrir o modulo contradiria o documento que
    ele tem em maos.
    """
    from datetime import timedelta

    from certificates.models import Certificate
    from courses.services import complete_enrollment
    from exams.models import Exam
    from exams.services import expire_attempt, start_attempt

    aluno = matricula.student
    primeira = start_attempt(aluno, prova_aberta)
    expire_attempt(primeira)
    Certificate.objects.create(
        attempt=primeira,
        student_name_snapshot=aluno.full_name,
        module_name_snapshot=prova_aberta.module.name,
        exam_title_snapshot=prova_aberta.title,
        institution_name_snapshot="CPO AD Bras Sumare",
    )

    # Segunda prova no mesmo modulo, com o proprio certificado. Ela NAO entra
    # no alvo, porque o filtro de titulo aponta so para a primeira.
    segunda = Exam.objects.create(
        module=prova_aberta.module,
        title="Outra Avaliacao do Modulo 1",
        status=prova_aberta.status,
        open_at=prova_aberta.open_at,
        close_at=prova_aberta.close_at,
        duration_minutes=60,
        passing_score=prova_aberta.passing_score,
        total_points=prova_aberta.total_points,
        max_attempts=1,
    )
    outra_tentativa = ExamAttempt.objects.create(
        student=aluno,
        exam=segunda,
        attempt_number=1,
        status="EXPIRED",
        started_at=timezone.now() - timedelta(hours=2),
        # expires_at precisa ser posterior a started_at por constraint.
        expires_at=timezone.now() - timedelta(hours=1),
        expired_at=timezone.now() - timedelta(hours=1),
        total_points_snapshot=segunda.total_points,
        passing_score_snapshot=segunda.passing_score,
    )
    Certificate.objects.create(
        attempt=outra_tentativa,
        student_name_snapshot=aluno.full_name,
        module_name_snapshot=segunda.module.name,
        exam_title_snapshot=segunda.title,
        institution_name_snapshot="CPO AD Bras Sumare",
    )

    complete_enrollment(matricula, encerrar_acesso=True, actor=admin_user)

    executar(
        student_email=aluno.email,
        module_code=prova_aberta.module.code,
        exam_title=prova_aberta.title,
        execute=True,
        confirm=FRASE,
        reactivate_enrollment=True,
    )

    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.COMPLETED
    assert matricula.access_enabled is False


def test_nao_reativa_matricula_que_nao_esta_concluida(tentativa, admin_user):
    from certificates.models import Certificate
    from exams.services import expire_attempt

    expire_attempt(tentativa)
    Certificate.objects.create(
        attempt=tentativa,
        student_name_snapshot=tentativa.student.full_name,
        module_name_snapshot=tentativa.exam.module.name,
        exam_title_snapshot=tentativa.exam.title,
        institution_name_snapshot="CPO AD Bras Sumare",
    )

    matricula = Enrollment.objects.get(
        student=tentativa.student, module=tentativa.exam.module
    )
    assert matricula.status == EnrollmentStatus.ACTIVE

    executar(
        student_email=tentativa.student.email,
        module_code=tentativa.exam.module.code,
        execute=True,
        confirm=FRASE,
        reactivate_enrollment=True,
    )

    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.ACTIVE


def test_sem_module_code_nao_ha_reativacao(tentativa_respondida, admin_user):
    """
    Adivinhar o modulo a partir do titulo da prova seria decidir sobre acesso
    academico por heuristica.
    """
    matricula = _com_certificado_e_matricula_concluida(
        tentativa_respondida, admin_user
    )

    saida = executar(
        student_email=tentativa_respondida.student.email,
        exam_title=tentativa_respondida.exam.title,
        execute=True,
        confirm=FRASE,
        reactivate_enrollment=True,
    )

    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.COMPLETED
    assert "Matricula sera reativada ...... NAO" in saida
