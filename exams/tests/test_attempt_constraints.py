"""
O que o PostgreSQL garante sozinho sobre a situacao da tentativa.

A service layer ja mantem status, submitted_at e expired_at coerentes, e ha
testes disso. Estes aqui sao outra pergunta: o que acontece quando ninguem
passa pela service layer.

Nao e hipotese remota. Um shell de producao para consertar um caso pontual,
uma migration de dados, um comando de gestao escrito as pressas numa noite de
prova — todos escrevem com objects.update, que nao chama save(), nao chama
full_clean() e nao sabe que choices existe. Se o banco nao recusar, a linha
entra.

E uma linha incoerente aqui nao e so feia. Uma tentativa SUBMITTED sem
submitted_at mente sobre quando o aluno entregou. Uma com status "HACKED"
some do sistema: nao esta em andamento, nao esta encerrada, o comando de
expiracao nao a encontra e o limite de tentativas nao a conta.

Por isso todo ataque deste arquivo usa objects.create ou QuerySet.update.
Atacar pelo servico provaria que o servico esta certo hoje, e nao que o banco
recusa amanha.
"""

from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from exams.models import AttemptStatus, ExamAttempt

pytestmark = pytest.mark.django_db


STATUS_E_TIMESTAMPS = "tentativa_status_e_timestamps_coerentes"
ENVIO_APOS_INICIO = "tentativa_envio_nao_anterior_ao_inicio"
EXPIRACAO_APOS_INICIO = "tentativa_expiracao_nao_anterior_ao_inicio"
SITUACAO_CONHECIDA = "tentativa_situacao_conhecida"


@contextmanager
def recusado_por(*nomes):
    """
    Exige que o bloco falhe por uma constraint especifica.

    Conferir apenas IntegrityError seria fraco: ExamAttempt tem dez
    constraints, e um teste mal montado passaria por esbarrar em outra. Ja
    aconteceu neste projeto, em test_lineage.py.

    psycopg expoe o nome violado em diag.constraint_name. E o unico caminho
    que nao depende do idioma do servidor — a mensagem de texto do PostgreSQL
    vem traduzida.

    Aceita mais de um nome porque existem escritas que quebram duas
    invariantes ao mesmo tempo; qual delas dispara primeiro e ordem interna
    de verificacao do PostgreSQL, detalhe que nao vale fixar num teste.
    """
    with pytest.raises(IntegrityError) as erro:
        with transaction.atomic():
            yield

    origem = erro.value.__cause__
    violada = getattr(getattr(origem, "diag", None), "constraint_name", None)
    assert violada in nomes, "esperava {}, veio {}".format(" ou ".join(nomes), violada)


def criar(aluno, prova, **campos):
    """Escreve direto na tabela, sem passar por start_attempt."""
    agora = timezone.now()
    padroes = {
        "attempt_number": 1,
        "started_at": agora,
        "expires_at": agora + timedelta(minutes=60),
        "total_points_snapshot": Decimal("10.00"),
        "passing_score_snapshot": Decimal("8.00"),
    }
    padroes.update(campos)
    return ExamAttempt.objects.create(student=aluno, exam=prova, **padroes)


def forcar(tentativa, **campos):
    """
    UPDATE cru na linha da tentativa.

    Deliberadamente por QuerySet: nao dispara save(), nao dispara signal e nao
    valida nada. E o que um shell de producao faz.
    """
    return ExamAttempt.objects.filter(pk=tentativa.pk).update(**campos)


# ---------------------------------------------------------------------------
# tentativa_status_e_timestamps_coerentes
#
#   IN_PROGRESS  submitted_at NULL      e expired_at NULL
#   SUBMITTED    submitted_at PREENCHIDO e expired_at NULL
#   EXPIRED      submitted_at NULL      e expired_at PREENCHIDO
#   RESET        sem exigencia de formato
#
# A situacao e os dois carimbos precisam contar a mesma historia.
# ---------------------------------------------------------------------------


def test_enviada_sem_carimbo_de_envio_e_recusada(tentativa):
    """
    O caso mais perigoso dos seis.

    A tentativa se diz enviada e nao tem quando. Toda pergunta posterior sobre
    prazo — entregou dentro do tempo? entregou antes de fechar? — passa a nao
    ter resposta, e uma futura correcao ordenaria por um campo nulo.
    """
    with recusado_por(STATUS_E_TIMESTAMPS):
        forcar(tentativa, status=AttemptStatus.SUBMITTED, submitted_at=None)


def test_enviada_com_carimbo_de_expiracao_e_recusada(tentativa):
    """Enviou ou o tempo acabou. As duas coisas ao mesmo tempo, nao."""
    agora = timezone.now()
    with recusado_por(STATUS_E_TIMESTAMPS):
        forcar(
            tentativa,
            status=AttemptStatus.SUBMITTED,
            submitted_at=agora,
            expired_at=agora,
        )


def test_expirada_sem_carimbo_de_expiracao_e_recusada(tentativa):
    with recusado_por(STATUS_E_TIMESTAMPS):
        forcar(tentativa, status=AttemptStatus.EXPIRED, expired_at=None)


def test_expirada_com_carimbo_de_envio_e_recusada(tentativa):
    """
    O espelho do caso anterior, e igualmente mentiroso: diria que o aluno
    entregou e que o tempo acabou sem entrega.
    """
    agora = timezone.now()
    with recusado_por(STATUS_E_TIMESTAMPS):
        forcar(
            tentativa,
            status=AttemptStatus.EXPIRED,
            expired_at=agora,
            submitted_at=agora,
        )


def test_em_andamento_com_carimbo_de_envio_e_recusada(tentativa):
    """
    Uma tentativa em andamento com submitted_at aceitaria autosave depois do
    envio: o servico olha o status, e o status diria que ainda esta aberta.
    """
    with recusado_por(STATUS_E_TIMESTAMPS):
        forcar(tentativa, submitted_at=timezone.now())


def test_em_andamento_com_carimbo_de_expiracao_e_recusada(tentativa):
    with recusado_por(STATUS_E_TIMESTAMPS):
        forcar(tentativa, expired_at=timezone.now())


def test_a_criacao_tambem_e_recusada_e_nao_so_o_update(aluno_matriculado, prova_aberta):
    """
    A constraint vale na entrada da linha, e nao apenas na alteracao.

    Uma migration de dados que copiasse tentativas de outro sistema usaria
    create ou bulk_create, nao update.
    """
    with recusado_por(STATUS_E_TIMESTAMPS):
        criar(
            aluno_matriculado,
            prova_aberta,
            status=AttemptStatus.SUBMITTED,
            submitted_at=None,
        )


# --- e os estados que devem ser aceitos ------------------------------------


def test_em_andamento_sem_carimbo_nenhum_e_aceita(tentativa):
    assert tentativa.status == AttemptStatus.IN_PROGRESS
    assert tentativa.submitted_at is None
    assert tentativa.expired_at is None


def test_enviada_com_carimbo_de_envio_e_aceita(tentativa):
    agora = timezone.now()

    forcar(tentativa, status=AttemptStatus.SUBMITTED, submitted_at=agora)

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.SUBMITTED
    assert tentativa.submitted_at is not None
    assert tentativa.expired_at is None


def test_expirada_com_carimbo_de_expiracao_e_aceita(tentativa):
    agora = timezone.now()

    forcar(tentativa, status=AttemptStatus.EXPIRED, expired_at=agora)

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.EXPIRED
    assert tentativa.expired_at is not None
    assert tentativa.submitted_at is None


def test_anulada_aceita_qualquer_combinacao_de_carimbos(tentativa):
    """
    RESET fica de fora da exigencia de formato, e isso e escolha, nao omissao.

    O reset administrativo ainda nao existe. Quando existir, a decisao mais
    provavel e preservar o carimbo da tentativa anulada — apagar o
    submitted_at destruiria justamente a informacao que explica por que a
    anulacao foi necessaria. Fixar o formato agora seria decidir, sem
    discussao, a regra de uma etapa futura.

    O teste cobre as quatro combinacoes para que a folga fique explicita: se
    alguem um dia apertar a constraint, ele falha e a conversa acontece.
    """
    agora = timezone.now()
    combinacoes = [
        {"submitted_at": None, "expired_at": None},
        {"submitted_at": agora, "expired_at": None},
        {"submitted_at": None, "expired_at": agora},
        {"submitted_at": agora, "expired_at": agora},
    ]

    for carimbos in combinacoes:
        assert forcar(tentativa, status=AttemptStatus.RESET, **carimbos) == 1

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.RESET


def test_o_servico_produz_apenas_estados_aceitos(tentativa, prova_aberta, aluno_matriculado):
    """
    Contraprova das constraints acima.

    Uma constraint que recusasse tambem o caminho normal seria pior que
    nenhuma: derrubaria o envio do aluno em vez de proteger o dado. Este teste
    faz o percurso real e confirma que nada estoura.
    """
    from exams.services import expire_attempt, submit_attempt

    responder_todas(tentativa)
    enviada = submit_attempt(tentativa)
    assert enviada.status == AttemptStatus.SUBMITTED

    outra = criar(aluno_matriculado, prova_aberta, attempt_number=2)
    expirada = expire_attempt(outra, agora=outra.expires_at)
    assert expirada.status == AttemptStatus.EXPIRED


def responder_todas(tentativa):
    """Preenche as obrigatorias para que o envio nao seja barrado."""
    from exams.services import autosave_answer

    for linha in tentativa.questions.select_related("question").all():
        if linha.question.type in {"SHORT_TEXT", "ESSAY"}:
            autosave_answer(
                tentativa, question_token=str(linha.public_token), text="resposta"
            )
        else:
            alternativa = linha.options.order_by("display_order").first()
            autosave_answer(
                tentativa,
                question_token=str(linha.public_token),
                option_tokens=[str(alternativa.public_token)],
            )


# ---------------------------------------------------------------------------
# Coerencia temporal
#
#   submitted_at IS NULL OR submitted_at >= started_at
#   expired_at   IS NULL OR expired_at   >= started_at
#
# Nada termina antes de comecar.
# ---------------------------------------------------------------------------


def test_envio_anterior_ao_inicio_e_recusado(tentativa):
    agora = timezone.now()

    with recusado_por(ENVIO_APOS_INICIO):
        forcar(
            tentativa,
            status=AttemptStatus.SUBMITTED,
            started_at=agora,
            expires_at=agora + timedelta(minutes=60),
            submitted_at=agora - timedelta(minutes=1),
        )


def test_expiracao_anterior_ao_inicio_e_recusada(tentativa):
    agora = timezone.now()

    with recusado_por(EXPIRACAO_APOS_INICIO):
        forcar(
            tentativa,
            status=AttemptStatus.EXPIRED,
            started_at=agora,
            expires_at=agora + timedelta(minutes=60),
            expired_at=agora - timedelta(minutes=1),
        )


def test_envio_no_mesmo_instante_do_inicio_e_aceito(tentativa):
    """
    A comparacao e >=, e nao >.

    Uma prova de um minuto respondida por um script de teste pode gravar os
    dois carimbos no mesmo microssegundo. Recusar isso quebraria um caso
    legitimo sem proteger nada: o problema real e o envio ANTES do inicio.
    """
    agora = timezone.now()

    assert (
        forcar(
            tentativa,
            status=AttemptStatus.SUBMITTED,
            started_at=agora,
            expires_at=agora + timedelta(minutes=60),
            submitted_at=agora,
        )
        == 1
    )


def test_expiracao_no_mesmo_instante_do_inicio_e_aceita(tentativa):
    agora = timezone.now()

    assert (
        forcar(
            tentativa,
            status=AttemptStatus.EXPIRED,
            started_at=agora,
            expires_at=agora + timedelta(minutes=60),
            expired_at=agora,
        )
        == 1
    )


def test_envio_depois_do_prazo_e_aceito_pelo_banco(tentativa):
    """
    O que deliberadamente NAO foi transformado em constraint.

    Nao existe exigencia de submitted_at <= expires_at. Uma requisicao pode
    entrar dentro do prazo e so obter o lock da linha alguns milissegundos
    depois dele; quem classifica esse caso e a service layer, com o relogio do
    servidor, e ela ja o transforma em EXPIRED.

    Uma check nessa comparacao recusaria uma linha que o proprio codigo
    produz e derrubaria o envio de um aluno por causa de uma disputa de lock.
    Este teste existe para que a folga seja intencional e visivel, e nao um
    esquecimento que alguem "conserte" depois.
    """
    agora = timezone.now()

    assert (
        forcar(
            tentativa,
            status=AttemptStatus.SUBMITTED,
            started_at=agora - timedelta(hours=2),
            expires_at=agora - timedelta(hours=1),
            submitted_at=agora,
        )
        == 1
    )


# ---------------------------------------------------------------------------
# tentativa_situacao_conhecida
#
#   status IN (IN_PROGRESS, SUBMITTED, EXPIRED, RESET)
#
# choices e validacao de formulario. O banco nunca ouviu falar dela.
# ---------------------------------------------------------------------------


def test_situacao_inventada_e_recusada(tentativa):
    """
    Sem esta constraint, "HACKED" entra e a tentativa fica invisivel.

    Nao esta em andamento, entao o autosave a recusa. Nao esta encerrada. O
    comando de expiracao filtra por IN_PROGRESS e nao a encontra, entao ela
    nunca expira. E que_contam_para_o_limite so exclui RESET, entao ela ainda
    ocupa uma das tentativas do aluno — para sempre.
    """
    with recusado_por(SITUACAO_CONHECIDA):
        forcar(tentativa, status="HACKED")


def test_situacao_vazia_e_recusada(tentativa):
    with recusado_por(SITUACAO_CONHECIDA):
        forcar(tentativa, status="")


def test_situacao_com_caixa_diferente_e_recusada(tentativa):
    """
    A comparacao do PostgreSQL e sensivel a caixa, e precisa continuar sendo:
    "submitted" nao casaria com nenhum filtro do codigo, que compara com
    AttemptStatus.SUBMITTED.
    """
    with recusado_por(STATUS_E_TIMESTAMPS, SITUACAO_CONHECIDA):
        forcar(tentativa, status="submitted")


def test_criacao_com_situacao_inventada_tambem_e_recusada(
    aluno_matriculado, prova_aberta
):
    with recusado_por(SITUACAO_CONHECIDA):
        criar(aluno_matriculado, prova_aberta, status="HACKED")


def test_as_quatro_situacoes_do_enum_sao_aceitas(tentativa):
    """
    Contraprova: a constraint nao pode ter ficado apertada demais.

    Cada situacao vai acompanhada dos carimbos que a outra constraint exige,
    para que uma eventual falha aqui aponte a constraint certa.
    """
    agora = timezone.now()
    aceitos = [
        {"status": AttemptStatus.IN_PROGRESS, "submitted_at": None, "expired_at": None},
        {"status": AttemptStatus.SUBMITTED, "submitted_at": agora, "expired_at": None},
        {"status": AttemptStatus.EXPIRED, "submitted_at": None, "expired_at": agora},
        {"status": AttemptStatus.RESET, "submitted_at": None, "expired_at": None},
    ]

    for campos in aceitos:
        assert forcar(tentativa, **campos) == 1, campos


def test_o_enum_e_a_constraint_nao_saem_de_sincronia():
    """
    Guarda contra o esquecimento previsivel.

    Acrescentar uma situacao ao enum sem migration deixaria o codigo achando
    que ela vale e o banco recusando toda escrita dela — em producao, no meio
    de uma prova. Aqui isso vira uma falha de teste.

    A lista abaixo e escrita a mao de proposito. Compara-la com
    AttemptStatus.values seria comparar o enum consigo mesmo.
    """
    assert AttemptStatus.values == ["IN_PROGRESS", "SUBMITTED", "EXPIRED", "RESET"]


def test_a_constraint_esta_realmente_no_banco():
    """
    Confirma que as quatro constraints existem no PostgreSQL, e nao apenas na
    declaracao do modelo.

    Uma migration nao aplicada deixaria os testes de recusa acima falhando de
    um jeito confuso; este falha dizendo exatamente o que faltou.
    """
    from django.db import connection

    esperadas = {
        STATUS_E_TIMESTAMPS,
        ENVIO_APOS_INICIO,
        EXPIRACAO_APOS_INICIO,
        SITUACAO_CONHECIDA,
    }

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT conname
              FROM pg_constraint
             WHERE conrelid = %s::regclass
               AND contype = 'c'
            """,
            [ExamAttempt._meta.db_table],
        )
        existentes = {linha[0] for linha in cursor.fetchall()}

    assert esperadas <= existentes, esperadas - existentes
