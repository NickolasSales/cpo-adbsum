"""
Os portoes do inicio da prova.

Todo teste aqui chama start_attempt diretamente, e nao a rota. A rota tem os
proprios testes; estes precisam provar que a regra vale mesmo para quem nao
passa pela tela — um comando de gestao, um script, uma view futura.

Bordas de tempo sao testadas nos dois lados. "Antes de fechar" e "exatamente
na hora de fechar" sao casos diferentes, e e sempre o segundo que quebra em
producao.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from common.exceptions import DomainError
from courses.models import Enrollment, EnrollmentStatus
from exams.models import AttemptStatus, ExamAttempt
from exams.services import close_exam, set_exam_password, start_attempt
from exams.services.attempt import MENSAGEM_SENHA_INVALIDA, SemAcessoAProva

pytestmark = pytest.mark.django_db


def mover_janela(prova, *, abertura, encerramento):
    """
    Reposiciona a janela de uma prova ja publicada, escrevendo direto.

    A prova publicada e imutavel pelos servicos, de proposito. Para testar as
    bordas de tempo sem esperar horas, o jeito honesto e mexer no relogio da
    prova pela tabela e deixar claro que isso e artificio de teste.
    """
    from exams.models import Exam

    Exam.objects.filter(pk=prova.pk).update(
        open_at=abertura, close_at=encerramento
    )
    prova.refresh_from_db()
    return prova


# ---------------------------------------------------------------------------
# Matricula
# ---------------------------------------------------------------------------


def test_inicia_com_matricula_liberada(prova_aberta, aluno_matriculado):
    tentativa = start_attempt(aluno_matriculado, prova_aberta)

    assert tentativa.status == AttemptStatus.IN_PROGRESS
    assert tentativa.student_id == aluno_matriculado.pk
    assert tentativa.exam_id == prova_aberta.pk


def test_sem_matricula_nao_inicia(prova_aberta, outro_student):
    with pytest.raises(SemAcessoAProva):
        start_attempt(outro_student, prova_aberta)

    assert ExamAttempt.objects.count() == 0


def test_matricula_inativa_nao_inicia(prova_aberta, aluno_matriculado, matricula):
    Enrollment.objects.filter(pk=matricula.pk).update(
        status=EnrollmentStatus.INACTIVE
    )

    with pytest.raises(SemAcessoAProva):
        start_attempt(aluno_matriculado, prova_aberta)


def test_acesso_bloqueado_nao_inicia(prova_aberta, aluno_matriculado, matricula):
    """
    O bloqueio operacional e independente da situacao academica.

    Um aluno com pendencia administrativa continua matriculado, mas nao entra
    na prova. E o caso que a coordenacao usa na vespera.
    """
    Enrollment.objects.filter(pk=matricula.pk).update(access_enabled=False)

    with pytest.raises(SemAcessoAProva):
        start_attempt(aluno_matriculado, prova_aberta)


def test_modulo_inativo_nao_inicia(prova_aberta, aluno_matriculado, modulo):
    from courses.models import Module

    Module.objects.filter(pk=modulo.pk).update(is_active=False)

    with pytest.raises(SemAcessoAProva):
        start_attempt(aluno_matriculado, prova_aberta)


# ---------------------------------------------------------------------------
# Situacao da prova
# ---------------------------------------------------------------------------


def test_prova_em_rascunho_nao_inicia(prova_pronta, aluno_matriculado):
    with pytest.raises(DomainError):
        start_attempt(aluno_matriculado, prova_pronta)

    assert ExamAttempt.objects.count() == 0


def test_prova_fechada_nao_inicia(prova_aberta, aluno_matriculado, admin_user):
    close_exam(prova_aberta, actor=admin_user)
    prova_aberta.refresh_from_db()

    with pytest.raises(DomainError):
        start_attempt(aluno_matriculado, prova_aberta)

    assert ExamAttempt.objects.count() == 0


def test_fechar_a_prova_nao_encerra_tentativa_ja_aberta(
    prova_aberta, aluno_matriculado, admin_user
):
    """
    Fechar a prova bloqueia novas tentativas, e so isso.

    Quem ja esta respondendo continua ate o prazo que foi gravado no start.
    Encerrar todo mundo no meio da prova seria uma decisao administrativa
    grave demais para acontecer como efeito colateral de fechar a prova.
    """
    tentativa = start_attempt(aluno_matriculado, prova_aberta)

    close_exam(prova_aberta, actor=admin_user)

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.IN_PROGRESS
    assert tentativa.expires_at is not None


# ---------------------------------------------------------------------------
# Janela temporal
# ---------------------------------------------------------------------------


def test_antes_da_abertura_nao_inicia(prova_aberta, aluno_matriculado):
    agora = timezone.now()
    mover_janela(
        prova_aberta,
        abertura=agora + timedelta(minutes=5),
        encerramento=agora + timedelta(hours=2),
    )

    with pytest.raises(DomainError):
        start_attempt(aluno_matriculado, prova_aberta)


def test_exatamente_na_abertura_inicia(prova_aberta, aluno_matriculado):
    """
    A janela e fechada no inicio: open_at <= agora.

    Quem clica no segundo exato da abertura precisa entrar. O caso oposto
    deixaria a turma esperando um segundo sem entender por que.
    """
    agora = timezone.now()
    mover_janela(
        prova_aberta,
        abertura=agora - timedelta(milliseconds=1),
        encerramento=agora + timedelta(hours=2),
    )

    tentativa = start_attempt(aluno_matriculado, prova_aberta)
    assert tentativa.status == AttemptStatus.IN_PROGRESS


def test_pouco_antes_do_encerramento_inicia(prova_aberta, aluno_matriculado):
    agora = timezone.now()
    mover_janela(
        prova_aberta,
        abertura=agora - timedelta(hours=1),
        encerramento=agora + timedelta(minutes=5),
    )

    tentativa = start_attempt(aluno_matriculado, prova_aberta)
    assert tentativa.status == AttemptStatus.IN_PROGRESS


def test_exatamente_no_encerramento_nao_inicia(prova_aberta, aluno_matriculado):
    """
    A janela e aberta no fim: agora < close_at.

    Exatamente em close_at a prova acabou. Aceitar esse instante criaria uma
    tentativa cujo expires_at seria igual ao started_at, que o banco recusa
    por constraint — e o aluno veria um erro tecnico em vez de "periodo
    encerrado".
    """
    agora = timezone.now()
    mover_janela(
        prova_aberta,
        abertura=agora - timedelta(hours=2),
        encerramento=agora - timedelta(milliseconds=1),
    )

    with pytest.raises(DomainError):
        start_attempt(aluno_matriculado, prova_aberta)

    assert ExamAttempt.objects.count() == 0


# ---------------------------------------------------------------------------
# Senha da prova
# ---------------------------------------------------------------------------


def test_senha_correta_inicia(prova_aberta, aluno_matriculado, admin_user):
    set_exam_password(prova_aberta, "Turma#Alpha2026", actor=admin_user)
    prova_aberta.refresh_from_db()

    tentativa = start_attempt(
        aluno_matriculado, prova_aberta, supplied_password="Turma#Alpha2026"
    )
    assert tentativa.status == AttemptStatus.IN_PROGRESS


@pytest.mark.parametrize("fornecida", ["", None, "errada", "turma#alpha2026"])
def test_senha_incorreta_nao_inicia(
    prova_aberta, aluno_matriculado, admin_user, fornecida
):
    set_exam_password(prova_aberta, "Turma#Alpha2026", actor=admin_user)
    prova_aberta.refresh_from_db()

    with pytest.raises(DomainError) as erro:
        start_attempt(aluno_matriculado, prova_aberta, supplied_password=fornecida)

    assert MENSAGEM_SENHA_INVALIDA in str(erro.value)
    assert ExamAttempt.objects.count() == 0


def test_mensagem_de_senha_nao_revela_detalhe_interno(
    prova_aberta, aluno_matriculado, admin_user
):
    """
    A recusa e sempre a mesma frase.

    Distinguir "senha vazia" de "senha errada" de "prova sem senha" daria a
    quem esta sondando um oraculo para descobrir se a prova exige senha e se
    ele chegou perto.
    """
    set_exam_password(prova_aberta, "Turma#Alpha2026", actor=admin_user)
    prova_aberta.refresh_from_db()

    with pytest.raises(DomainError) as erro:
        start_attempt(aluno_matriculado, prova_aberta, supplied_password="quase")

    texto = str(erro.value)
    assert texto == MENSAGEM_SENHA_INVALIDA
    assert "hash" not in texto.lower()
    assert prova_aberta.access_password_hash not in texto


def test_prova_sem_senha_ignora_a_senha_enviada(prova_aberta, aluno_matriculado):
    """Mandar senha para uma prova que nao tem senha nao pode barrar ninguem."""
    tentativa = start_attempt(
        aluno_matriculado, prova_aberta, supplied_password="qualquer coisa"
    )
    assert tentativa.status == AttemptStatus.IN_PROGRESS


def test_senha_nao_e_exigida_para_retomar(prova_aberta, aluno_matriculado, admin_user):
    """
    A senha serve para entrar, nao para permanecer.

    Se o aluno fechar a aba sem querer, cobrar a senha de novo o deixaria
    trancado do lado de fora da propria prova caso ele nao a tenha anotado.
    """
    set_exam_password(prova_aberta, "Turma#Alpha2026", actor=admin_user)
    prova_aberta.refresh_from_db()
    primeira = start_attempt(
        aluno_matriculado, prova_aberta, supplied_password="Turma#Alpha2026"
    )

    retomada = start_attempt(aluno_matriculado, prova_aberta, supplied_password="")

    assert retomada.pk == primeira.pk


def test_trocar_a_senha_no_meio_nao_derruba_quem_ja_comecou(
    prova_aberta, aluno_matriculado, admin_user
):
    primeira = start_attempt(aluno_matriculado, prova_aberta)

    set_exam_password(prova_aberta, "Senha#Nova2026", actor=admin_user)
    prova_aberta.refresh_from_db()

    retomada = start_attempt(aluno_matriculado, prova_aberta)
    assert retomada.pk == primeira.pk
    assert retomada.status == AttemptStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# Limite de tentativas e retomada
# ---------------------------------------------------------------------------


def test_segundo_start_retoma_a_tentativa_aberta(prova_aberta, aluno_matriculado):
    primeira = start_attempt(aluno_matriculado, prova_aberta)
    segunda = start_attempt(aluno_matriculado, prova_aberta)

    assert segunda.pk == primeira.pk
    assert ExamAttempt.objects.count() == 1


def test_retomada_nao_gera_evento_novo(prova_aberta, aluno_matriculado):
    from audit.models import AuditEvent, AuditLog

    start_attempt(aluno_matriculado, prova_aberta)
    start_attempt(aluno_matriculado, prova_aberta)

    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_STARTED).count() == 1


def test_limite_de_uma_tentativa_bloqueia_a_segunda(
    prova_aberta, aluno_matriculado, admin_user
):
    from exams.services import submit_attempt

    primeira = start_attempt(aluno_matriculado, prova_aberta)
    _responder_tudo(primeira)
    submit_attempt(primeira)

    with pytest.raises(DomainError):
        start_attempt(aluno_matriculado, prova_aberta)

    assert ExamAttempt.objects.count() == 1


def test_tentativa_expirada_consome_a_chance(prova_aberta, aluno_matriculado):
    """
    Com max_attempts=1, expirar gasta a tentativa.

    O aluno teve o tempo dele. Devolver a chance a quem deixou o prazo passar
    daria tempo extra a quem simplesmente fechasse a aba.
    """
    from exams.services import expire_attempt

    primeira = start_attempt(aluno_matriculado, prova_aberta)
    expire_attempt(primeira, agora=primeira.expires_at)

    with pytest.raises(DomainError):
        start_attempt(aluno_matriculado, prova_aberta)


def test_tentativa_aberta_vencida_e_encerrada_antes_de_avaliar_o_limite(
    prova_aberta, aluno_matriculado
):
    """
    A tentativa esquecida em aberto precisa ser fechada antes de qualquer
    decisao sobre uma nova.

    Sem isso a constraint parcial recusaria a criacao com IntegrityError, e o
    aluno veria erro tecnico em vez da mensagem certa.
    """
    primeira = start_attempt(aluno_matriculado, prova_aberta)
    # As duas datas voltam juntas. Empurrar so o expires_at para tras deixaria
    # a linha com prazo anterior ao inicio, que a constraint
    # tentativa_prazo_posterior_ao_inicio recusa — o teste falharia montando o
    # cenario, sem chegar a exercitar o que quer verificar.
    agora = timezone.now()
    ExamAttempt.objects.filter(pk=primeira.pk).update(
        started_at=agora - timedelta(hours=2),
        expires_at=agora - timedelta(hours=1),
    )

    with pytest.raises(DomainError):
        start_attempt(aluno_matriculado, prova_aberta)

    primeira.refresh_from_db()
    assert primeira.status == AttemptStatus.EXPIRED
    assert primeira.expired_at is not None


def test_com_duas_tentativas_permitidas_a_segunda_e_numerada_dois(
    prova_aberta, aluno_matriculado, admin_user
):
    from exams.models import Exam
    from exams.services import expire_attempt

    Exam.objects.filter(pk=prova_aberta.pk).update(max_attempts=2)
    prova_aberta.refresh_from_db()

    primeira = start_attempt(aluno_matriculado, prova_aberta)
    expire_attempt(primeira, agora=primeira.expires_at)

    segunda = start_attempt(aluno_matriculado, prova_aberta)

    assert segunda.attempt_number == 2
    assert segunda.pk != primeira.pk


# ---------------------------------------------------------------------------
# Prazo
# ---------------------------------------------------------------------------


def test_prazo_e_a_duracao_quando_a_prova_fecha_bem_depois(
    prova_aberta, aluno_matriculado
):
    agora = timezone.now()
    mover_janela(
        prova_aberta,
        abertura=agora - timedelta(minutes=1),
        encerramento=agora + timedelta(hours=3),
    )

    tentativa = start_attempt(aluno_matriculado, prova_aberta)

    esperado = tentativa.started_at + timedelta(minutes=60)
    assert abs((tentativa.expires_at - esperado).total_seconds()) < 1


def test_prazo_e_o_fechamento_quando_a_prova_fecha_antes(
    prova_aberta, aluno_matriculado
):
    """
    Quem comeca faltando vinte minutos tem vinte minutos, e nao a duracao
    inteira. A janela da prova vale para todos.
    """
    agora = timezone.now()
    prova_aberta = mover_janela(
        prova_aberta,
        abertura=agora - timedelta(hours=1),
        encerramento=agora + timedelta(minutes=20),
    )

    tentativa = start_attempt(aluno_matriculado, prova_aberta)

    assert tentativa.expires_at == prova_aberta.close_at


def test_prazo_nao_muda_quando_a_prova_e_fechada_depois(
    prova_aberta, aluno_matriculado, admin_user
):
    tentativa = start_attempt(aluno_matriculado, prova_aberta)
    prazo_original = tentativa.expires_at

    close_exam(prova_aberta, actor=admin_user)

    tentativa.refresh_from_db()
    assert tentativa.expires_at == prazo_original


def test_prazo_nao_muda_quando_a_janela_e_alterada_depois(
    prova_aberta, aluno_matriculado
):
    """
    O prazo do aluno e o que foi combinado com ele quando a prova abriu na
    tela. Nenhuma mudanca posterior no cadastro o encurta ou estende.
    """
    tentativa = start_attempt(aluno_matriculado, prova_aberta)
    prazo_original = tentativa.expires_at

    mover_janela(
        prova_aberta,
        abertura=timezone.now() - timedelta(hours=5),
        encerramento=timezone.now() + timedelta(minutes=1),
    )

    tentativa.refresh_from_db()
    assert tentativa.expires_at == prazo_original


def test_retomar_nao_recalcula_o_prazo(prova_aberta, aluno_matriculado):
    primeira = start_attempt(aluno_matriculado, prova_aberta)
    prazo_original = primeira.expires_at

    retomada = start_attempt(aluno_matriculado, prova_aberta)

    assert retomada.expires_at == prazo_original


# ---------------------------------------------------------------------------
# Snapshot da escala
# ---------------------------------------------------------------------------


def test_snapshot_copia_a_escala_da_prova(prova_aberta, aluno_matriculado):
    tentativa = start_attempt(aluno_matriculado, prova_aberta)

    assert tentativa.total_points_snapshot == prova_aberta.total_points
    assert tentativa.passing_score_snapshot == prova_aberta.passing_score
    assert str(tentativa.total_points_snapshot) == "10.00"


def test_snapshot_nao_muda_quando_a_prova_muda_depois(prova_aberta, aluno_matriculado):
    """
    A tentativa e registro historico proprio.

    Hoje a prova publicada e imutavel e os dois valores coincidiriam sempre.
    O teste escreve direto na tabela para provar que a independencia e real, e
    nao consequencia de a prova nao poder mudar.
    """
    from exams.models import Exam

    tentativa = start_attempt(aluno_matriculado, prova_aberta)

    Exam.objects.filter(pk=prova_aberta.pk).update(
        total_points=Decimal("99.00"), passing_score=Decimal("5.00")
    )

    tentativa.refresh_from_db()
    assert tentativa.total_points_snapshot == Decimal("10.00")
    assert tentativa.passing_score_snapshot == Decimal("8.00")


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------


def _responder_tudo(tentativa):
    """Responde todas as questoes para que o envio voluntario seja aceito."""
    from exams.models import AttemptQuestion, TIPOS_COM_ALTERNATIVAS
    from exams.services import autosave_answer

    for linha in (
        AttemptQuestion.objects.filter(attempt=tentativa)
        .select_related("question")
        .prefetch_related("options")
    ):
        if linha.question.type in TIPOS_COM_ALTERNATIVAS:
            autosave_answer(
                tentativa,
                question_token=str(linha.public_token),
                option_tokens=[str(linha.options.first().public_token)],
            )
        else:
            autosave_answer(
                tentativa, question_token=str(linha.public_token), text="Resposta."
            )
