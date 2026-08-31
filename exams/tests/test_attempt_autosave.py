"""
Autosave: gravacao de resposta durante a prova.

Duas propriedades organizam os testes:

    substituicao  cada gravacao troca a resposta inteira daquela questao. Nao
                  ha soma nem residuo — desmarcar precisa funcionar.

    pertencimento todo token e resolvido dentro do escopo em que ele vale. Um
                  token de outra questao, de outra tentativa ou inventado
                  recebe a mesma recusa.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from common.exceptions import DomainError
from exams.models import (
    LIMITE_ESSAY,
    LIMITE_SHORT_TEXT,
    Answer,
    AnswerOption,
    AttemptOption,
    AttemptQuestion,
    AttemptStatus,
    ExamAttempt,
    QuestionType,
)
from exams.services import autosave_answer, expire_attempt, start_attempt, submit_attempt
from exams.services.attempt import TentativaNaoEditavel, TokenInvalido

pytestmark = pytest.mark.django_db


def marcadas(tentativa, token_da_questao):
    """Textos das alternativas marcadas naquela questao."""
    return sorted(
        AnswerOption.objects.filter(
            answer__attempt_question__attempt=tentativa,
            answer__attempt_question__public_token=token_da_questao,
        ).values_list("attempt_option__option__text", flat=True)
    )


def texto_salvo(tentativa, token_da_questao):
    resposta = Answer.objects.filter(
        attempt_question__attempt=tentativa,
        attempt_question__public_token=token_da_questao,
    ).first()
    return resposta.text_answer if resposta else None


# ---------------------------------------------------------------------------
# Escolha unica
# ---------------------------------------------------------------------------


def test_escolha_unica_grava_a_alternativa(tentativa, tokens):
    questao, alternativas = tokens[QuestionType.SINGLE_CHOICE]

    resultado = autosave_answer(
        tentativa, question_token=questao, option_tokens=[alternativas[0]]
    )

    assert resultado["saved"] is True
    assert isinstance(resultado["remaining_seconds"], int)
    assert len(marcadas(tentativa, questao)) == 1


def test_escolha_unica_substitui_a_marcacao_anterior(tentativa, tokens):
    """
    Trocar de A para B nao pode deixar as duas marcadas.

    Este e o teste que pega a implementacao ingenua: quem apenas adiciona a
    nova selecao acumula respostas e, na Etapa 5, o aluno apareceria tendo
    marcado tudo.
    """
    questao, alternativas = tokens[QuestionType.SINGLE_CHOICE]

    autosave_answer(tentativa, question_token=questao, option_tokens=[alternativas[0]])
    primeira = marcadas(tentativa, questao)

    autosave_answer(tentativa, question_token=questao, option_tokens=[alternativas[1]])
    segunda = marcadas(tentativa, questao)

    assert len(segunda) == 1
    assert segunda != primeira
    assert AnswerOption.objects.count() == 1


def test_escolha_unica_aceita_desmarcar(tentativa, tokens):
    questao, alternativas = tokens[QuestionType.SINGLE_CHOICE]
    autosave_answer(tentativa, question_token=questao, option_tokens=[alternativas[0]])

    autosave_answer(tentativa, question_token=questao, option_tokens=[])

    assert marcadas(tentativa, questao) == []
    # A Answer continua existindo, mas sem selecao: a questao volta a contar
    # como nao respondida.
    assert Answer.objects.filter(attempt_question__attempt=tentativa).count() == 1


def test_escolha_unica_recusa_duas_alternativas(tentativa, tokens):
    questao, alternativas = tokens[QuestionType.SINGLE_CHOICE]

    with pytest.raises(TokenInvalido):
        autosave_answer(
            tentativa,
            question_token=questao,
            option_tokens=[alternativas[0], alternativas[1]],
        )

    assert AnswerOption.objects.count() == 0


def test_verdadeiro_falso_segue_a_mesma_regra(tentativa, tokens):
    questao, alternativas = tokens[QuestionType.TRUE_FALSE]

    autosave_answer(tentativa, question_token=questao, option_tokens=[alternativas[0]])
    assert len(marcadas(tentativa, questao)) == 1

    with pytest.raises(TokenInvalido):
        autosave_answer(
            tentativa, question_token=questao, option_tokens=alternativas[:2]
        )


# ---------------------------------------------------------------------------
# Multiplas respostas
# ---------------------------------------------------------------------------


def test_multipla_grava_varias_alternativas(tentativa, tokens):
    questao, alternativas = tokens[QuestionType.MULTIPLE_CHOICE]

    autosave_answer(
        tentativa,
        question_token=questao,
        option_tokens=[alternativas[0], alternativas[2]],
    )

    assert len(marcadas(tentativa, questao)) == 2


def test_multipla_reduzir_a_selecao_nao_deixa_residuo(tentativa, tokens):
    """
    A + C, depois so A. O resultado precisa ser exatamente A.

    Uma implementacao que calculasse diferenca em vez de substituir o conjunto
    inteiro erraria justamente aqui.
    """
    questao, alternativas = tokens[QuestionType.MULTIPLE_CHOICE]

    autosave_answer(
        tentativa,
        question_token=questao,
        option_tokens=[alternativas[0], alternativas[2]],
    )
    assert len(marcadas(tentativa, questao)) == 2

    autosave_answer(tentativa, question_token=questao, option_tokens=[alternativas[0]])

    restantes = marcadas(tentativa, questao)
    assert len(restantes) == 1
    assert AnswerOption.objects.count() == 1


def test_multipla_ignora_token_repetido(tentativa, tokens):
    """
    Marcar a mesma alternativa duas vezes e a mesma coisa que marcar uma.

    Normalizar o conjunto e mais util que recusar: o navegador pode mandar
    duplicado por um clique duplo, e isso nao e erro do aluno.
    """
    questao, alternativas = tokens[QuestionType.MULTIPLE_CHOICE]

    resultado = autosave_answer(
        tentativa,
        question_token=questao,
        option_tokens=[alternativas[0], alternativas[0], alternativas[0]],
    )

    assert resultado["saved"] is True
    assert len(marcadas(tentativa, questao)) == 1


def test_multipla_aceita_esvaziar(tentativa, tokens):
    questao, alternativas = tokens[QuestionType.MULTIPLE_CHOICE]
    autosave_answer(
        tentativa, question_token=questao, option_tokens=alternativas[:2]
    )

    autosave_answer(tentativa, question_token=questao, option_tokens=[])

    assert marcadas(tentativa, questao) == []


# ---------------------------------------------------------------------------
# Respostas textuais
# ---------------------------------------------------------------------------


def test_resposta_curta_dentro_do_limite(tentativa, tokens):
    questao, _ = tokens[QuestionType.SHORT_TEXT]

    autosave_answer(tentativa, question_token=questao, text="Cerrado")

    assert texto_salvo(tentativa, questao) == "Cerrado"


def test_resposta_curta_acima_do_limite_e_recusada(tentativa, tokens):
    questao, _ = tokens[QuestionType.SHORT_TEXT]

    with pytest.raises(DomainError):
        autosave_answer(
            tentativa, question_token=questao, text="x" * (LIMITE_SHORT_TEXT + 1)
        )

    assert Answer.objects.count() == 0


def test_resposta_curta_exatamente_no_limite_e_aceita(tentativa, tokens):
    questao, _ = tokens[QuestionType.SHORT_TEXT]

    autosave_answer(tentativa, question_token=questao, text="x" * LIMITE_SHORT_TEXT)

    assert len(texto_salvo(tentativa, questao)) == LIMITE_SHORT_TEXT


def test_dissertativa_tem_limite_maior(tentativa, tokens):
    questao, _ = tokens[QuestionType.ESSAY]

    autosave_answer(tentativa, question_token=questao, text="y" * LIMITE_ESSAY)

    assert len(texto_salvo(tentativa, questao)) == LIMITE_ESSAY


def test_dissertativa_acima_do_limite_e_recusada(tentativa, tokens):
    questao, _ = tokens[QuestionType.ESSAY]

    with pytest.raises(DomainError):
        autosave_answer(
            tentativa, question_token=questao, text="y" * (LIMITE_ESSAY + 1)
        )


def test_acentuacao_e_unicode_sao_preservados(tentativa, tokens):
    questao, _ = tokens[QuestionType.ESSAY]
    original = "Avaliação: coesão, ênfase e conclusão — não é trivial. 中文 🇧🇷"

    autosave_answer(tentativa, question_token=questao, text=original)

    assert texto_salvo(tentativa, questao) == original


def test_o_texto_nao_sofre_strip(tentativa, tokens):
    """
    Numa dissertativa, o recuo de paragrafo e a linha em branco sao do autor.

    Um .strip() no que e gravado pareceria inofensivo e destruiria formatacao
    que o aluno escolheu — inclusive a indentacao de um trecho de codigo.
    """
    questao, _ = tokens[QuestionType.ESSAY]
    original = "    Primeiro paragrafo com recuo.\n\n    Segundo.\n\n"

    autosave_answer(tentativa, question_token=questao, text=original)

    assert texto_salvo(tentativa, questao) == original


def test_quebras_de_linha_do_windows_sao_normalizadas(tentativa, tokens):
    """
    Unica normalizacao aplicada: \\r\\n vira \\n.

    Sem isso o mesmo texto ocuparia tamanhos diferentes conforme o sistema do
    aluno, e o limite de caracteres puniria quem responde do Windows.
    """
    questao, _ = tokens[QuestionType.ESSAY]

    autosave_answer(tentativa, question_token=questao, text="linha um\r\nlinha dois")

    assert texto_salvo(tentativa, questao) == "linha um\nlinha dois"


def test_texto_vazio_e_aceito(tentativa, tokens):
    """Apagar o que escreveu e uma acao legitima."""
    questao, _ = tokens[QuestionType.SHORT_TEXT]
    autosave_answer(tentativa, question_token=questao, text="Cerrado")

    autosave_answer(tentativa, question_token=questao, text="")

    assert texto_salvo(tentativa, questao) == ""


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------


def test_salvar_a_mesma_resposta_duas_vezes_nao_cria_duas(tentativa, tokens):
    questao, _ = tokens[QuestionType.ESSAY]

    autosave_answer(tentativa, question_token=questao, text="Mesmo texto")
    autosave_answer(tentativa, question_token=questao, text="Mesmo texto")
    autosave_answer(tentativa, question_token=questao, text="Mesmo texto")

    assert Answer.objects.filter(attempt_question__attempt=tentativa).count() == 1


def test_salvar_a_mesma_selecao_duas_vezes_nao_duplica(tentativa, tokens):
    questao, alternativas = tokens[QuestionType.MULTIPLE_CHOICE]

    autosave_answer(
        tentativa, question_token=questao, option_tokens=alternativas[:2]
    )
    autosave_answer(
        tentativa, question_token=questao, option_tokens=alternativas[:2]
    )

    assert AnswerOption.objects.count() == 2


def test_autosave_atualiza_a_ultima_atividade(tentativa, tokens):
    questao, _ = tokens[QuestionType.ESSAY]
    assert tentativa.last_activity_at is None

    autosave_answer(tentativa, question_token=questao, text="Texto")

    tentativa.refresh_from_db()
    assert tentativa.last_activity_at is not None


def test_autosave_nao_estende_o_prazo(tentativa, tokens):
    """
    last_activity_at e informacao operacional, nao relogio.

    Renovar o prazo a cada gravacao transformaria a duracao da prova em tempo
    de inatividade: quem digitasse sem parar nunca expiraria.
    """
    questao, _ = tokens[QuestionType.ESSAY]
    prazo = tentativa.expires_at

    autosave_answer(tentativa, question_token=questao, text="Texto")

    tentativa.refresh_from_db()
    assert tentativa.expires_at == prazo


# ---------------------------------------------------------------------------
# Tokens forjados
# ---------------------------------------------------------------------------


def test_token_de_questao_inexistente_e_recusado(tentativa):
    import uuid as _uuid

    with pytest.raises(TokenInvalido):
        autosave_answer(tentativa, question_token=str(_uuid.uuid4()), text="x")

    assert Answer.objects.count() == 0


def test_token_que_nem_e_uuid_e_recusado(tentativa):
    for lixo in ["", "abc", "1", "../../etc/passwd", "' OR 1=1 --"]:
        with pytest.raises(TokenInvalido):
            autosave_answer(tentativa, question_token=lixo, text="x")

    assert Answer.objects.count() == 0


def test_alternativa_de_outra_questao_e_recusada(tentativa, tokens):
    """
    O token existe, e da mesma tentativa e do mesmo aluno — mas e de outra
    questao. O filtro por attempt_question e o que fecha essa porta.
    """
    questao_unica, _ = tokens[QuestionType.SINGLE_CHOICE]
    _, alternativas_da_multipla = tokens[QuestionType.MULTIPLE_CHOICE]

    with pytest.raises(TokenInvalido):
        autosave_answer(
            tentativa,
            question_token=questao_unica,
            option_tokens=[alternativas_da_multipla[0]],
        )

    assert AnswerOption.objects.count() == 0


def test_token_de_outra_tentativa_e_recusado(
    prova_aberta, aluno_matriculado, outro_student, modulo
):
    """
    O ataque que os tokens por tentativa existem para impedir: um aluno passa
    o token dele para o colega.
    """
    from courses.services import create_enrollment

    create_enrollment(student=outro_student, module=modulo)
    minha = start_attempt(aluno_matriculado, prova_aberta)
    dele = start_attempt(outro_student, prova_aberta)

    token_alheio = str(
        AttemptQuestion.objects.filter(attempt=dele).first().public_token
    )

    with pytest.raises(TokenInvalido):
        autosave_answer(minha, question_token=token_alheio, text="x")

    assert Answer.objects.count() == 0


def test_alternativa_de_outra_tentativa_e_recusada(
    prova_aberta, aluno_matriculado, outro_student, modulo
):
    from courses.services import create_enrollment
    from exams.models import QuestionType as QT

    create_enrollment(student=outro_student, module=modulo)
    minha = start_attempt(aluno_matriculado, prova_aberta)
    dele = start_attempt(outro_student, prova_aberta)

    minha_questao = AttemptQuestion.objects.get(
        attempt=minha, question__type=QT.SINGLE_CHOICE
    )
    alternativa_alheia = AttemptOption.objects.filter(
        attempt_question__attempt=dele
    ).first()

    with pytest.raises(TokenInvalido):
        autosave_answer(
            minha,
            question_token=str(minha_questao.public_token),
            option_tokens=[str(alternativa_alheia.public_token)],
        )

    assert AnswerOption.objects.count() == 0


def test_um_token_valido_junto_de_um_forjado_recusa_tudo(tentativa, tokens):
    """
    Recusa o lote inteiro, e nao apenas o token invalido.

    Salvar a parte valida e descartar o resto daria ao aluno a impressao de
    que tudo foi gravado. Melhor recusar e deixar a tela avisar.
    """
    import uuid as _uuid

    questao, alternativas = tokens[QuestionType.MULTIPLE_CHOICE]

    with pytest.raises(TokenInvalido):
        autosave_answer(
            tentativa,
            question_token=questao,
            option_tokens=[alternativas[0], str(_uuid.uuid4())],
        )

    assert AnswerOption.objects.count() == 0


# ---------------------------------------------------------------------------
# Estado da tentativa
# ---------------------------------------------------------------------------


def test_autosave_apos_o_prazo_expira_e_recusa(tentativa, tokens):
    from audit.models import AuditEvent, AuditLog

    questao, _ = tokens[QuestionType.ESSAY]
    agora = timezone.now()
    ExamAttempt.objects.filter(pk=tentativa.pk).update(
        started_at=agora - timedelta(hours=2), expires_at=agora - timedelta(hours=1)
    )

    with pytest.raises(TentativaNaoEditavel) as erro:
        autosave_answer(tentativa, question_token=questao, text="tarde demais")

    assert erro.value.status_da_tentativa == AttemptStatus.EXPIRED
    assert Answer.objects.count() == 0

    tentativa.refresh_from_db()
    assert tentativa.status == AttemptStatus.EXPIRED
    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_EXPIRED).count() == 1


def test_autosave_apos_o_prazo_nao_expira_duas_vezes(tentativa, tokens):
    from audit.models import AuditEvent, AuditLog

    questao, _ = tokens[QuestionType.ESSAY]
    agora = timezone.now()
    ExamAttempt.objects.filter(pk=tentativa.pk).update(
        started_at=agora - timedelta(hours=2), expires_at=agora - timedelta(hours=1)
    )

    for _ in range(3):
        with pytest.raises(TentativaNaoEditavel):
            autosave_answer(tentativa, question_token=questao, text="x")

    assert AuditLog.objects.filter(event=AuditEvent.ATTEMPT_EXPIRED).count() == 1


def test_autosave_apos_o_envio_e_recusado(tentativa, tokens):
    questao, _ = tokens[QuestionType.ESSAY]
    # Responde tudo primeiro e so entao grava o texto sentinela: _responder_todas
    # passa por todas as questoes, inclusive esta, e sobrescreveria o valor.
    _responder_todas(tentativa)
    autosave_answer(tentativa, question_token=questao, text="Resposta original")
    submit_attempt(tentativa)

    with pytest.raises(TentativaNaoEditavel) as erro:
        autosave_answer(tentativa, question_token=questao, text="Depois do envio")

    assert erro.value.status_da_tentativa == AttemptStatus.SUBMITTED
    assert texto_salvo(tentativa, questao) == "Resposta original"


def test_autosave_em_tentativa_expirada_nao_altera_resposta(tentativa, tokens):
    questao, _ = tokens[QuestionType.ESSAY]
    autosave_answer(tentativa, question_token=questao, text="Salvo a tempo")

    expire_attempt(tentativa, agora=tentativa.expires_at)

    with pytest.raises(TentativaNaoEditavel):
        autosave_answer(tentativa, question_token=questao, text="Tarde demais")

    assert texto_salvo(tentativa, questao) == "Salvo a tempo"


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------


def _responder_todas(tentativa):
    from exams.models import TIPOS_COM_ALTERNATIVAS

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
