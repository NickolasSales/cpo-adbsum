"""
O comando de gestao que encerra tentativas orfas.

A expiracao ja acontece sozinha no proximo request do aluno. O que sobra para
o comando sao as tentativas que ninguem mais abriu: a aba fechada, o notebook
que dormiu, quem nunca mais voltou. Sem ele essas linhas ficariam
IN_PROGRESS para sempre e a constraint parcial impediria o aluno de comecar
outra tentativa.

O teste que mais importa aqui e o de nao-duplicacao da regra: o comando
precisa chegar ao mesmo lugar que o acesso web, senao um dia os dois vao
discordar sobre o que EXPIRED significa.
"""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from audit.models import AuditEvent, AuditLog
from exams.models import AttemptStatus, ExamAttempt, QuestionType
from exams.services import autosave_answer, start_attempt

pytestmark = pytest.mark.django_db


def vencer(tentativa, *, ha=timedelta(hours=1)):
    agora = timezone.now()
    ExamAttempt.objects.filter(pk=tentativa.pk).update(
        started_at=agora - ha - timedelta(hours=1), expires_at=agora - ha
    )
    tentativa.refresh_from_db()
    return tentativa


def rodar(**opcoes):
    saida = StringIO()
    call_command("expirar_tentativas", stdout=saida, **opcoes)
    return saida.getvalue()


def montar_turma(prova, alunos):
    """Uma tentativa em andamento por aluno."""
    return [start_attempt(aluno, prova) for aluno in alunos]


@pytest.fixture
def turma(db, prova_aberta, modulo, django_user_model):
    """Cinco alunos matriculados, para separar vencidas de validas."""
    from courses.services import create_enrollment

    alunos = []
    for indice in range(5):
        aluno = django_user_model.objects.create_user(
            email="aluno{}@escola.test".format(indice),
            full_name="Aluno {}".format(indice),
            password="Senha#Turma2026",
            role="STUDENT",
        )
        aluno.must_change_password = False
        aluno.save(update_fields=["must_change_password"])
        create_enrollment(student=aluno, module=modulo)
        alunos.append(aluno)
    return alunos


# ---------------------------------------------------------------------------
# O caso central
# ---------------------------------------------------------------------------


def test_expira_as_vencidas_e_preserva_as_validas(prova_aberta, turma):
    tentativas = montar_turma(prova_aberta, turma)
    for tentativa in tentativas[:3]:
        vencer(tentativa)

    saida = rodar()

    for tentativa in tentativas[:3]:
        tentativa.refresh_from_db()
        assert tentativa.status == AttemptStatus.EXPIRED
        assert tentativa.expired_at is not None
        assert tentativa.submitted_at is None

    for tentativa in tentativas[3:]:
        tentativa.refresh_from_db()
        assert tentativa.status == AttemptStatus.IN_PROGRESS

    assert "3" in saida


def test_rodar_de_novo_nao_encontra_nada(prova_aberta, turma):
    tentativas = montar_turma(prova_aberta, turma)
    for tentativa in tentativas[:3]:
        vencer(tentativa)

    rodar()
    saida = rodar()

    assert "Nenhuma tentativa vencida" in saida
    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_EXPIRED).count() == 3


def test_nao_duplica_auditoria_em_execucoes_repetidas(prova_aberta, turma):
    tentativas = montar_turma(prova_aberta, turma)
    vencer(tentativas[0])

    for _ in range(4):
        rodar()

    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_EXPIRED).count() == 1


def test_nao_toca_em_tentativas_ja_encerradas(prova_aberta, turma):
    from exams.services import expire_attempt

    tentativas = montar_turma(prova_aberta, turma)
    vencer(tentativas[0])
    expire_attempt(tentativas[0])
    momento = ExamAttempt.objects.get(pk=tentativas[0].pk).expired_at

    rodar()

    assert ExamAttempt.objects.get(pk=tentativas[0].pk).expired_at == momento


def test_tentativa_exatamente_no_prazo_e_expirada(prova_aberta, aluno_matriculado):
    """
    A consulta usa expires_at <= agora, coerente com prazo_vencido, que usa >=.

    Se o comando usasse < e o acesso web usasse <=, uma tentativa exatamente
    no limite seria expirada por um caminho e nao pelo outro.
    """
    tentativa = start_attempt(aluno_matriculado, prova_aberta)
    agora = timezone.now()
    ExamAttempt.objects.filter(pk=tentativa.pk).update(
        started_at=agora - timedelta(hours=1), expires_at=agora
    )

    rodar()

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.EXPIRED


def test_preserva_as_respostas_ja_salvas(prova_aberta, aluno_matriculado, tokens):
    from exams.models import Answer

    tentativa = ExamAttempt.objects.get(student=aluno_matriculado)
    questao, _ = tokens[QuestionType.ESSAY]
    autosave_answer(tentativa, question_token=questao, text="Escrito antes do fim")
    vencer(tentativa)

    rodar()

    assert Answer.objects.get(
        attempt_question__public_token=questao
    ).text_answer == "Escrito antes do fim"


# ---------------------------------------------------------------------------
# Opcoes do comando
# ---------------------------------------------------------------------------


def test_dry_run_nao_altera_nada(prova_aberta, turma):
    tentativas = montar_turma(prova_aberta, turma)
    for tentativa in tentativas[:2]:
        vencer(tentativa)

    saida = rodar(dry_run=True)

    assert "2" in saida
    for tentativa in tentativas[:2]:
        tentativa.refresh_from_db()
        assert tentativa.status == AttemptStatus.IN_PROGRESS
    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_EXPIRED).count() == 0


def test_trabalha_em_lotes_sem_perder_nenhuma(prova_aberta, turma):
    """
    Lote pequeno de proposito: com cinco vencidas e lote de 2, o laco precisa
    dar tres voltas e nao pode parar na primeira nem repetir eternamente.
    """
    tentativas = montar_turma(prova_aberta, turma)
    for tentativa in tentativas:
        vencer(tentativa)

    rodar(lote=2)

    assert (
        ExamAttempt.objects.filter(status=AttemptStatus.EXPIRED).count() == 5
    )
    assert ExamAttempt.objects.filter(status=AttemptStatus.IN_PROGRESS).count() == 0


def test_limite_para_depois_da_quantidade_pedida(prova_aberta, turma):
    tentativas = montar_turma(prova_aberta, turma)
    for tentativa in tentativas:
        vencer(tentativa)

    rodar(limite=2)

    assert ExamAttempt.objects.filter(status=AttemptStatus.EXPIRED).count() == 2
    assert ExamAttempt.objects.filter(status=AttemptStatus.IN_PROGRESS).count() == 3


def test_lote_invalido_e_recusado(prova_aberta):
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        rodar(lote=0)


def test_limite_invalido_e_recusado(prova_aberta):
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        rodar(limite=0)


def test_sem_tentativa_nenhuma_o_comando_nao_falha(prova_aberta):
    saida = rodar()
    assert "Nenhuma tentativa vencida" in saida


# ---------------------------------------------------------------------------
# A regra nao esta duplicada
# ---------------------------------------------------------------------------


def test_o_comando_chama_o_mesmo_servico_do_acesso_web(prova_aberta, turma, monkeypatch):
    """
    Prova que a expiracao do comando passa por expire_attempt, e nao por uma
    segunda implementacao.

    Duas versoes da mesma regra e a forma mais rapida de as duas discordarem:
    uma esqueceria expired_at, ou a auditoria, ou a idempotencia — e a
    divergencia so apareceria em producao, num caso raro.
    """
    from exams.services import attempt as servico

    chamadas = []
    original = servico.expire_attempt

    def espiao(tentativa, **kwargs):
        chamadas.append(tentativa.pk)
        return original(tentativa, **kwargs)

    monkeypatch.setattr(servico, "expire_attempt", espiao)

    tentativas = montar_turma(prova_aberta, turma)
    for tentativa in tentativas[:2]:
        vencer(tentativa)

    rodar()

    assert sorted(chamadas) == sorted(t.pk for t in tentativas[:2])


def test_o_resultado_do_comando_e_igual_ao_da_expiracao_web(
    prova_aberta, turma
):
    """
    Duas tentativas identicas, uma encerrada pelo comando e outra pelo acesso
    do aluno. Os campos resultantes precisam ser os mesmos.
    """
    from exams.services import expire_attempt

    tentativas = montar_turma(prova_aberta, turma)
    pela_web, pelo_comando = tentativas[0], tentativas[1]
    vencer(pela_web)
    vencer(pelo_comando)

    expire_attempt(pela_web)
    rodar()

    pela_web.refresh_from_db()
    pelo_comando.refresh_from_db()

    assert pela_web.status == pelo_comando.status == AttemptStatus.EXPIRED
    assert pela_web.submitted_at is pelo_comando.submitted_at is None
    assert pela_web.expired_at is not None
    assert pelo_comando.expired_at is not None

    eventos = AuditLog.objects.filter(event=AuditEvent.ATTEMPT_EXPIRED)
    assert eventos.count() == 2
    assert set(eventos.values_list("entity_id", flat=True)) == {
        str(pela_web.pk),
        str(pelo_comando.pk),
    }


# ---------------------------------------------------------------------------
# Expiracao preguicosa durante um GET
#
# Um GET que altera estado merece justificativa, porque a regra geral do
# projeto e a oposta: leitura nao escreve. A excecao vale aqui porque o GET
# nao decide nada — quem decidiu foi o relogio. Assim que now >= expires_at a
# tentativa ja esta objetivamente vencida; o request apenas encontra o fato e
# o registra. Nao ha intencao do aluno envolvida, nao ha corpo de requisicao
# lido, e o resultado seria o mesmo se ninguem abrisse a pagina, porque o
# comando de expiracao chegaria la depois.
#
# O que precisa ser garantido e que esse encontro aconteca UMA vez: recarregar
# a pagina de uma prova vencida nao pode empilhar eventos de auditoria.
# ---------------------------------------------------------------------------


@pytest.fixture
def aluno_logado(client, aluno_matriculado):
    client.force_login(aluno_matriculado)
    return client


def url_tentativa(tentativa):
    from django.urls import reverse

    return reverse("student:attempt", kwargs={"public_id": tentativa.public_id})


def eventos_de_expiracao(tentativa):
    return AuditLog.objects.filter(
        event=AuditEvent.ATTEMPT_EXPIRED, entity_id=str(tentativa.pk)
    )


def test_o_get_expira_a_tentativa_vencida_e_registra_uma_vez(
    aluno_logado, tentativa
):
    vencer(tentativa)
    assert tentativa.status == AttemptStatus.IN_PROGRESS
    assert eventos_de_expiracao(tentativa).count() == 0

    resposta = aluno_logado.get(url_tentativa(tentativa))

    assert resposta.status_code == 200
    assert "Tempo encerrado" in resposta.content.decode("utf-8")

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.EXPIRED
    assert tentativa.expired_at is not None
    assert tentativa.submitted_at is None
    assert eventos_de_expiracao(tentativa).count() == 1


def test_recarregar_a_pagina_nao_empilha_eventos(aluno_logado, tentativa):
    """
    A tela de prova vencida e das mais recarregadas que existem: o aluno viu
    "Tempo encerrado" e aperta F5 achando que foi engano.

    Dez recargas nao podem virar dez linhas de auditoria — a trilha ficaria
    inutil justamente no caso que mais importa investigar depois.
    """
    vencer(tentativa)

    for _ in range(10):
        assert aluno_logado.get(url_tentativa(tentativa)).status_code == 200

    assert eventos_de_expiracao(tentativa).count() == 1

    tentativa.refresh_from_db()
    carimbo = tentativa.expired_at

    aluno_logado.get(url_tentativa(tentativa))
    tentativa.refresh_from_db()
    assert tentativa.expired_at == carimbo


def test_o_get_e_o_comando_nao_expiram_a_mesma_tentativa_duas_vezes(
    aluno_logado, tentativa
):
    """
    Os dois caminhos convivem: o aluno abre a pagina e, dois minutos depois, o
    timer roda. O segundo nao pode registrar de novo.
    """
    vencer(tentativa)

    aluno_logado.get(url_tentativa(tentativa))
    tentativa.refresh_from_db()
    carimbo = tentativa.expired_at

    saida = rodar()

    assert "Nenhuma tentativa vencida" in saida
    assert eventos_de_expiracao(tentativa).count() == 1

    tentativa.refresh_from_db()
    assert tentativa.expired_at == carimbo


def test_a_pagina_final_do_get_nao_tem_formulario(aluno_logado, tentativa):
    """
    Expirou durante o GET, entao a mesma resposta ja precisa vir sem campos.

    Devolver o formulario e so depois recusar o autosave faria o aluno digitar
    para nada.
    """
    vencer(tentativa)

    corpo = aluno_logado.get(url_tentativa(tentativa)).content.decode("utf-8")

    assert "cpo-opcao" not in corpo
    assert "cpo-texto" not in corpo
    assert "attempt_autosave" not in corpo
