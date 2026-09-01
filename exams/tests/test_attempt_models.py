"""
Os cinco modelos da tentativa, e o que o banco garante sozinho.

Todo ataque aqui usa objects.create e QuerySet.update. Um teste que so
tentasse pelo servico provaria que o servico esta correto hoje, e nao que o
banco recusa amanha, quando alguem escrever um comando de gestao ou uma
migration de dados.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from exams.models import (
    Answer,
    AnswerOption,
    AttemptOption,
    AttemptQuestion,
    AttemptStatus,
    ExamAttempt,
    QuestionType,
)
from exams.services import start_attempt

pytestmark = pytest.mark.django_db


def nova_tentativa(aluno, prova, **campos):
    """
    Escreve direto na tabela, sem passar por start_attempt.

    Quem pede um estado encerrado ganha o carimbo correspondente de graca —
    submitted_at, expired_at ou reset_at, conforme a situacao.
    Nao e conveniencia: sem isso o helper produziria linhas que o sistema real
    nunca produz — uma SUBMITTED sem submitted_at, por exemplo — e que a
    constraint tentativa_status_e_timestamps_coerentes recusa. Os testes que
    usam este helper falam de unicidade, de limite de tentativas e da regra do
    RESET; nenhum deles quer repetir a contabilidade de carimbos, e nenhum
    deles deveria depender de um estado impossivel para passar.

    Quem quiser atacar a coerencia entre situacao e carimbo faz isso de
    proposito, em test_attempt_constraints.py, passando o carimbo explicito.
    """
    agora = timezone.now()
    padroes = {
        "attempt_number": 1,
        "started_at": agora,
        "expires_at": agora + timedelta(minutes=60),
        "total_points_snapshot": Decimal("10.00"),
        "passing_score_snapshot": Decimal("8.00"),
    }
    padroes.update(campos)

    situacao = padroes.get("status", AttemptStatus.IN_PROGRESS)
    if situacao == AttemptStatus.SUBMITTED:
        padroes.setdefault("submitted_at", padroes["started_at"])
    elif situacao == AttemptStatus.EXPIRED:
        padroes.setdefault("expired_at", padroes["expires_at"])
    elif situacao == AttemptStatus.RESET:
        # tentativa_anulacao_coerente, desde a Etapa 7: RESET sem reset_at nao
        # diz quando a tentativa deixou de valer.
        padroes.setdefault("reset_at", padroes["started_at"])

    return ExamAttempt.objects.create(student=aluno, exam=prova, **padroes)


# ---------------------------------------------------------------------------
# ExamAttempt: valores iniciais
# ---------------------------------------------------------------------------


def test_tentativa_nasce_em_andamento(tentativa):
    assert tentativa.status == AttemptStatus.IN_PROGRESS
    assert tentativa.attempt_number == 1
    assert tentativa.submitted_at is None
    assert tentativa.expired_at is None
    assert tentativa.em_andamento is True
    assert tentativa.encerrada is False


def test_public_id_e_um_uuid_aleatorio(tentativa):
    """
    O identificador publico precisa ser opaco de verdade.

    Se fosse derivado do pk — codificado, somado, hasheado com segredo — um
    aluno com duas tentativas conseguiria inferir a relacao e enumerar as dos
    colegas.

    A garantia verificavel e a versao: UUID4 sao 122 bits aleatorios, sem
    relacao nenhuma com a chave interna. Nao da para testar isso procurando o
    pk dentro do texto do UUID — um pk de um digito aparece como digito
    hexadecimal em quase todo UUID, e o teste passaria a falhar por
    coincidencia. E exatamente a armadilha que test_attempt_leak.py evita ao
    inspecionar atributos em vez de procurar numeros soltos no HTML.
    """
    assert isinstance(tentativa.public_id, uuid.UUID)
    assert tentativa.public_id.version == 4
    assert tentativa.public_id.variant == uuid.RFC_4122


def test_public_id_e_unico_entre_tentativas(prova_aberta, aluno_matriculado, outro_student, modulo):
    from courses.services import create_enrollment

    create_enrollment(student=outro_student, module=modulo)
    primeira = start_attempt(aluno_matriculado, prova_aberta)
    segunda = start_attempt(outro_student, prova_aberta)

    assert primeira.public_id != segunda.public_id


def test_snapshots_sao_decimal_e_nao_float(tentativa):
    """
    A escala da tentativa e Decimal do inicio ao fim, como a da prova.

    Um float aqui reintroduziria o erro de arredondamento exatamente na
    tabela que a Etapa 5 vai usar para calcular nota.
    """
    assert isinstance(tentativa.total_points_snapshot, Decimal)
    assert isinstance(tentativa.passing_score_snapshot, Decimal)
    assert str(tentativa.total_points_snapshot) == "10.00"
    assert str(tentativa.passing_score_snapshot) == "8.00"


def test_datas_sao_timezone_aware(tentativa):
    assert timezone.is_aware(tentativa.started_at)
    assert timezone.is_aware(tentativa.expires_at)


# ---------------------------------------------------------------------------
# ExamAttempt: constraints
# ---------------------------------------------------------------------------


def test_numero_de_tentativa_repetido_e_recusado(prova_aberta, aluno_matriculado):
    """
    Confere o NOME da constraint, e nao so que houve IntegrityError.

    ExamAttempt tem dez constraints. Um IntegrityError generico passaria
    tambem se a escrita esbarrasse em outra coisa — e foi o que aconteceu
    aqui: enquanto o helper criava SUBMITTED sem submitted_at, este teste
    estava a um passo de passar pelo motivo errado.
    """
    nova_tentativa(
        aluno_matriculado, prova_aberta, attempt_number=1, status=AttemptStatus.SUBMITTED
    )

    with pytest.raises(IntegrityError) as erro:
        with transaction.atomic():
            nova_tentativa(
                aluno_matriculado,
                prova_aberta,
                attempt_number=1,
                status=AttemptStatus.EXPIRED,
            )

    origem = erro.value.__cause__
    violada = getattr(getattr(origem, "diag", None), "constraint_name", None)
    assert violada == "tentativa_numero_unico_por_aluno_e_prova"


def test_duas_tentativas_em_andamento_sao_recusadas_pelo_banco(
    prova_aberta, aluno_matriculado
):
    """
    A defesa final contra tentativa duplicada.

    start_attempt ja serializa os starts do aluno com select_for_update, mas
    essa trava depende de alguem lembrar de usar a transacao certa. Esta
    constraint parcial nao depende de ninguem: o indice unico so existe para
    linhas IN_PROGRESS.
    """
    nova_tentativa(aluno_matriculado, prova_aberta, attempt_number=1)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            nova_tentativa(aluno_matriculado, prova_aberta, attempt_number=2)


def test_varias_tentativas_encerradas_sao_permitidas(prova_aberta, aluno_matriculado):
    """
    O outro lado da constraint parcial: ela nao pode barrar historico.

    Uma prova com tres tentativas permitidas termina com tres linhas do mesmo
    aluno na mesma prova. So nao pode haver duas abertas ao mesmo tempo.
    """
    nova_tentativa(
        aluno_matriculado, prova_aberta, attempt_number=1, status=AttemptStatus.SUBMITTED
    )
    nova_tentativa(
        aluno_matriculado, prova_aberta, attempt_number=2, status=AttemptStatus.EXPIRED
    )
    nova_tentativa(aluno_matriculado, prova_aberta, attempt_number=3)

    assert ExamAttempt.objects.filter(student=aluno_matriculado).count() == 3


def test_numero_de_tentativa_zero_e_recusado(prova_aberta, aluno_matriculado):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            nova_tentativa(aluno_matriculado, prova_aberta, attempt_number=0)


def test_prazo_anterior_ao_inicio_e_recusado(prova_aberta, aluno_matriculado):
    agora = timezone.now()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            nova_tentativa(
                aluno_matriculado,
                prova_aberta,
                started_at=agora,
                expires_at=agora - timedelta(minutes=1),
            )


def test_prazo_igual_ao_inicio_e_recusado(prova_aberta, aluno_matriculado):
    """
    Uma tentativa de duracao zero nasceria vencida.

    O aluno veria a tela por um instante e ela expiraria no primeiro autosave,
    consumindo uma das chances dele sem que ele pudesse responder nada.
    """
    agora = timezone.now()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            nova_tentativa(
                aluno_matriculado, prova_aberta, started_at=agora, expires_at=agora
            )


def test_nota_minima_do_snapshot_fora_da_escala_e_recusada(
    prova_aberta, aluno_matriculado
):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            nova_tentativa(
                aluno_matriculado, prova_aberta, passing_score_snapshot=Decimal("10.01")
            )


def test_total_de_pontos_negativo_no_snapshot_e_recusado(
    prova_aberta, aluno_matriculado
):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            nova_tentativa(
                aluno_matriculado, prova_aberta, total_points_snapshot=Decimal("-1.00")
            )


# ---------------------------------------------------------------------------
# AttemptQuestion e AttemptOption
# ---------------------------------------------------------------------------


def test_montagem_cria_uma_linha_por_questao_ativa(tentativa, prova_aberta):
    assert AttemptQuestion.objects.filter(attempt=tentativa).count() == 5
    assert prova_aberta.questions.filter(active=True).count() == 5


def test_posicoes_formam_uma_sequencia_de_zero_a_n(tentativa):
    """
    display_order precisa ser uma sequencia completa, e nao apenas distinta.

    Buracos na numeracao nao quebrariam nada tecnicamente, mas fariam o aluno
    ver "Questao 1, 2, 4" — e um aluno que ve isso na prova acha que perdeu
    uma questao.
    """
    posicoes = sorted(
        AttemptQuestion.objects.filter(attempt=tentativa).values_list(
            "display_order", flat=True
        )
    )
    assert posicoes == list(range(len(posicoes)))


def test_a_mesma_questao_nao_entra_duas_vezes_na_tentativa(tentativa):
    linha = AttemptQuestion.objects.filter(attempt=tentativa).first()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AttemptQuestion.objects.create(
                attempt=tentativa, question=linha.question, display_order=99
            )


def test_duas_questoes_nao_ocupam_a_mesma_posicao(tentativa, prova_aberta, modulo, admin_user):
    linhas = list(AttemptQuestion.objects.filter(attempt=tentativa)[:2])

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AttemptQuestion.objects.filter(pk=linhas[1].pk).update(
                display_order=linhas[0].display_order
            )


def test_posicao_negativa_de_questao_e_recusada(tentativa):
    linha = AttemptQuestion.objects.filter(attempt=tentativa).first()

    with pytest.raises(Exception):
        with transaction.atomic():
            AttemptQuestion.objects.filter(pk=linha.pk).update(display_order=-1)


def test_alternativas_so_existem_para_os_tipos_que_as_usam(tentativa):
    por_tipo = {}
    for linha in (
        AttemptQuestion.objects.filter(attempt=tentativa)
        .select_related("question")
        .prefetch_related("options")
    ):
        por_tipo[linha.question.type] = linha.options.count()

    assert por_tipo[QuestionType.SINGLE_CHOICE] == 3
    assert por_tipo[QuestionType.MULTIPLE_CHOICE] == 3
    assert por_tipo[QuestionType.TRUE_FALSE] == 2
    assert por_tipo[QuestionType.SHORT_TEXT] == 0
    assert por_tipo[QuestionType.ESSAY] == 0


def test_a_mesma_alternativa_nao_entra_duas_vezes(tentativa):
    alternativa = AttemptOption.objects.filter(
        attempt_question__attempt=tentativa
    ).first()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AttemptOption.objects.create(
                attempt_question=alternativa.attempt_question,
                option=alternativa.option,
                display_order=99,
            )


def test_duas_alternativas_nao_ocupam_a_mesma_posicao(tentativa):
    alternativas = list(
        AttemptOption.objects.filter(attempt_question__attempt=tentativa).order_by(
            "attempt_question_id", "display_order"
        )[:2]
    )
    assert alternativas[0].attempt_question_id == alternativas[1].attempt_question_id

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AttemptOption.objects.filter(pk=alternativas[1].pk).update(
                display_order=alternativas[0].display_order
            )


# ---------------------------------------------------------------------------
# Answer e AnswerOption
# ---------------------------------------------------------------------------


def test_uma_questao_da_tentativa_tem_no_maximo_uma_resposta(tentativa):
    linha = AttemptQuestion.objects.filter(attempt=tentativa).first()
    Answer.objects.create(attempt_question=linha, saved_at=timezone.now())

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Answer.objects.create(attempt_question=linha, saved_at=timezone.now())


def test_a_mesma_alternativa_nao_e_marcada_duas_vezes(tentativa):
    linha = (
        AttemptQuestion.objects.filter(
            attempt=tentativa, question__type=QuestionType.SINGLE_CHOICE
        )
        .prefetch_related("options")
        .first()
    )
    resposta = Answer.objects.create(attempt_question=linha, saved_at=timezone.now())
    alternativa = linha.options.first()
    AnswerOption.objects.create(answer=resposta, attempt_option=alternativa)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AnswerOption.objects.create(answer=resposta, attempt_option=alternativa)


def test_questao_sem_answer_significa_nao_respondida(tentativa):
    """
    Nenhuma Answer nasce no start, de proposito.

    Criar cinco respostas vazias obrigaria todo codigo posterior a distinguir
    "existe mas esta vazia" de "nao existe". A ausencia da linha e a leitura
    mais simples e mais honesta de "nao respondeu".
    """
    assert Answer.objects.filter(attempt_question__attempt=tentativa).count() == 0


# ---------------------------------------------------------------------------
# Comportamento de tempo
# ---------------------------------------------------------------------------


def test_segundos_restantes_nunca_e_negativo(tentativa):
    depois = tentativa.expires_at + timedelta(hours=5)
    assert tentativa.segundos_restantes(depois) == 0


def test_segundos_restantes_de_tentativa_encerrada_e_zero(tentativa):
    tentativa.status = AttemptStatus.SUBMITTED
    assert tentativa.segundos_restantes(tentativa.started_at) == 0


def test_prazo_vencido_e_exatamente_no_instante_do_prazo(tentativa):
    """
    A comparacao e >=, e nao >.

    Exatamente em expires_at o tempo acabou. Deixar o instante final valendo
    criaria uma janela de um segundo em que a tentativa esta viva e morta ao
    mesmo tempo, dependendo de qual comparacao rodar primeiro.
    """
    assert tentativa.prazo_vencido(tentativa.expires_at - timedelta(seconds=1)) is False
    assert tentativa.prazo_vencido(tentativa.expires_at) is True


def test_estados_finais_nao_sao_editaveis(tentativa):
    for status in (AttemptStatus.SUBMITTED, AttemptStatus.EXPIRED, AttemptStatus.RESET):
        tentativa.status = status
        assert tentativa.encerrada is True
        assert tentativa.em_andamento is False


def test_reset_nao_conta_para_o_limite(prova_aberta, aluno_matriculado):
    """
    A regra foi fixada na Etapa 4 e o reset chegou na Etapa 7.

    Uma tentativa anulada nao consome a chance do aluno — anular e justamente
    devolver a chance. O numero, esse, nunca e reaproveitado.

    reset_at acompanha porque a constraint tentativa_anulacao_coerente exige:
    RESET sem data de anulacao nao diz quando a tentativa deixou de valer.
    """
    nova_tentativa(
        aluno_matriculado, prova_aberta, attempt_number=1, status=AttemptStatus.RESET
    )
    nova_tentativa(
        aluno_matriculado, prova_aberta, attempt_number=2, status=AttemptStatus.SUBMITTED
    )

    contadas = ExamAttempt.objects.filter(
        student=aluno_matriculado, exam=prova_aberta
    ).que_contam_para_o_limite()

    assert contadas.count() == 1
    assert contadas.first().attempt_number == 2
