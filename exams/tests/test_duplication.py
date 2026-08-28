"""
Duplicacao e versionamento.

A propriedade que importa e a independencia: depois de duplicar, mexer numa
versao nao pode alterar a outra. Um FK compartilhado de questoes passaria em
qualquer teste de "conteudo igual" e falharia exatamente aqui.
"""

from decimal import Decimal

import pytest

from audit.models import AuditEvent, AuditLog
from exams.models import Exam, ExamStatus, QuestionType
from exams.services import (
    close_exam,
    delete_question,
    duplicate_exam,
    publish_exam,
    set_exam_password,
    update_question,
)

pytestmark = pytest.mark.django_db


def assinatura(prova):
    """Conteudo da prova, sem as PKs. Serve para comparar duas versoes."""
    return [
        (
            questao.type,
            questao.text,
            questao.points,
            questao.required,
            questao.order,
            questao.active,
            questao.internal_explanation,
            sorted(
                (opcao.text, opcao.is_correct, opcao.order)
                for opcao in questao.options.all()
            ),
        )
        for questao in prova.questions.prefetch_related("options").order_by("order", "id")
    ]


# ---------------------------------------------------------------------------
# A copia
# ---------------------------------------------------------------------------


def test_duplicacao_cria_prova_nova(prova_publicada, admin_user):
    copia = duplicate_exam(prova_publicada, actor=admin_user)

    assert copia.pk != prova_publicada.pk
    assert copia.status == ExamStatus.DRAFT
    assert copia.version == 2
    assert copia.published_at is None
    assert copia.closed_at is None


def test_copia_aponta_origem_e_raiz(prova_publicada, admin_user):
    copia = duplicate_exam(prova_publicada, actor=admin_user)

    assert copia.parent_exam_id == prova_publicada.pk
    assert copia.root_exam_id == prova_publicada.pk


def test_copia_traz_a_configuracao(prova_publicada, admin_user):
    copia = duplicate_exam(prova_publicada, actor=admin_user)

    assert copia.title == prova_publicada.title
    assert copia.module_id == prova_publicada.module_id
    assert copia.instructions == prova_publicada.instructions
    assert copia.duration_minutes == prova_publicada.duration_minutes
    assert copia.passing_score == prova_publicada.passing_score
    assert copia.max_attempts == prova_publicada.max_attempts
    assert copia.open_at == prova_publicada.open_at
    assert copia.close_at == prova_publicada.close_at


def test_copia_nao_herda_o_snapshot_de_pontos(prova_publicada, admin_user):
    """
    total_points e o retrato de uma publicacao. A copia e rascunho e ainda
    pode mudar, entao carregar o numero antigo so enganaria quem le a tela.
    """
    assert prova_publicada.total_points == Decimal("10.00")

    copia = duplicate_exam(prova_publicada, actor=admin_user)

    assert copia.total_points == Decimal("0.00")
    assert copia.pontos_vigentes == Decimal("10.00")  # soma corrente das questoes


def test_copia_traz_questoes_e_alternativas(prova_publicada, admin_user):
    copia = duplicate_exam(prova_publicada, actor=admin_user)

    assert copia.questions.count() == prova_publicada.questions.count() == 5
    assert assinatura(copia) == assinatura(prova_publicada)


def test_copia_traz_o_gabarito(prova_publicada, admin_user):
    copia = duplicate_exam(prova_publicada, actor=admin_user)

    original = prova_publicada.questions.get(type=QuestionType.SINGLE_CHOICE)
    copiada = copia.questions.get(type=QuestionType.SINGLE_CHOICE)

    assert (
        sorted(original.options.filter(is_correct=True).values_list("text", flat=True))
        == sorted(copiada.options.filter(is_correct=True).values_list("text", flat=True))
    )


def test_copia_traz_a_explicacao_interna(prova_publicada, admin_user):
    copia = duplicate_exam(prova_publicada, actor=admin_user)
    copiada = copia.questions.get(type=QuestionType.SINGLE_CHOICE)
    assert "apostila" in copiada.internal_explanation


def test_copia_mantem_a_senha_configurada(prova_pronta, admin_user):
    set_exam_password(prova_pronta, "Senha#Turma2026", actor=admin_user)
    prova_pronta.refresh_from_db()

    copia = duplicate_exam(prova_pronta, actor=admin_user)

    assert copia.tem_senha is True
    assert copia.access_password_hash == prova_pronta.access_password_hash


# ---------------------------------------------------------------------------
# Independencia
# ---------------------------------------------------------------------------


def test_questoes_e_alternativas_tem_pks_proprias(prova_publicada, admin_user):
    copia = duplicate_exam(prova_publicada, actor=admin_user)

    ids_originais = set(prova_publicada.questions.values_list("pk", flat=True))
    ids_copia = set(copia.questions.values_list("pk", flat=True))
    assert ids_originais.isdisjoint(ids_copia)

    opcoes_originais = set(
        prova_publicada.questions.first().options.values_list("pk", flat=True)
    )
    opcoes_copia = set(copia.questions.first().options.values_list("pk", flat=True))
    assert opcoes_originais.isdisjoint(opcoes_copia)


def test_editar_a_copia_nao_altera_o_original(prova_publicada, admin_user):
    """
    A propriedade central do versionamento. Uma prova ja aplicada precisa
    continuar descrevendo exatamente o que o aluno respondeu.
    """
    antes = assinatura(prova_publicada)
    copia = duplicate_exam(prova_publicada, actor=admin_user)

    questao = copia.questions.get(type=QuestionType.SINGLE_CHOICE)
    update_question(
        questao,
        type=QuestionType.SINGLE_CHOICE,
        text="Enunciado completamente diferente",
        points="7.00",
        opcoes=[
            {"text": "Nova A", "is_correct": False},
            {"text": "Nova B", "is_correct": True},
        ],
        actor=admin_user,
    )

    prova_publicada.refresh_from_db()
    assert assinatura(prova_publicada) == antes


def test_excluir_questao_da_copia_nao_altera_o_original(prova_publicada, admin_user):
    copia = duplicate_exam(prova_publicada, actor=admin_user)
    quantas = prova_publicada.questions.count()

    delete_question(copia.questions.first(), actor=admin_user)

    assert prova_publicada.questions.count() == quantas
    assert copia.questions.count() == quantas - 1


def test_publicar_a_copia_nao_altera_o_original(prova_publicada, admin_user):
    copia = duplicate_exam(prova_publicada, actor=admin_user)
    publish_exam(copia, actor=admin_user)

    prova_publicada.refresh_from_db()
    copia.refresh_from_db()

    assert prova_publicada.status == ExamStatus.PUBLISHED
    assert copia.status == ExamStatus.PUBLISHED
    assert prova_publicada.published_at != copia.published_at


# ---------------------------------------------------------------------------
# Numeracao da linhagem
# ---------------------------------------------------------------------------


def test_versoes_seguem_a_sequencia(prova_publicada, admin_user):
    v2 = duplicate_exam(prova_publicada, actor=admin_user)
    v3 = duplicate_exam(v2, actor=admin_user)

    assert (v2.version, v3.version) == (2, 3)
    assert v3.root_exam_id == prova_publicada.pk
    assert v3.parent_exam_id == v2.pk


def test_duplicar_uma_versao_antiga_gera_a_proxima_da_linhagem(
    prova_publicada, admin_user
):
    """
    O caso citado no requisito: existem v1, v2 e v3; duplicar a v1 precisa
    gerar a v4, e nao uma segunda v2.
    """
    v2 = duplicate_exam(prova_publicada, actor=admin_user)
    duplicate_exam(v2, actor=admin_user)  # v3

    v4 = duplicate_exam(prova_publicada, actor=admin_user)

    assert v4.version == 4
    assert v4.parent_exam_id == prova_publicada.pk
    assert v4.root_exam_id == prova_publicada.pk


def test_linhagem_reune_todas_as_versoes(prova_publicada, admin_user):
    v2 = duplicate_exam(prova_publicada, actor=admin_user)
    v3 = duplicate_exam(v2, actor=admin_user)

    linhagem = set(
        Exam.objects.da_linhagem_de(v3).values_list("version", flat=True)
    )
    assert linhagem == {1, 2, 3}


def test_provas_diferentes_tem_linhagens_separadas(
    prova_publicada, outro_modulo, admin_user
):
    from exams.services import create_exam

    outra = create_exam(module=outro_modulo, title="Outra prova", actor=admin_user)
    duplicate_exam(prova_publicada, actor=admin_user)

    assert Exam.objects.da_linhagem_de(outra).count() == 1
    assert Exam.objects.da_linhagem_de(prova_publicada).count() == 2


def test_duplicar_prova_fechada_e_permitido(prova_publicada, admin_user):
    """Fechada e somente leitura, mas duplicar nao altera nada nela."""
    close_exam(prova_publicada, actor=admin_user)

    copia = duplicate_exam(prova_publicada, actor=admin_user)

    assert copia.status == ExamStatus.DRAFT
    prova_publicada.refresh_from_db()
    assert prova_publicada.status == ExamStatus.CLOSED


def test_duplicar_rascunho_e_permitido(prova_pronta, admin_user):
    copia = duplicate_exam(prova_pronta, actor=admin_user)
    assert copia.version == 2
    assert copia.questions.count() == 5


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------


def test_duplicacao_registra_auditoria(prova_publicada, admin_user):
    copia = duplicate_exam(prova_publicada, actor=admin_user)

    registro = AuditLog.objects.filter(
        event=AuditEvent.EXAM_DUPLICATED, entity_id=str(copia.pk)
    ).first()

    assert registro is not None
    assert registro.metadata["source_exam_id"] == prova_publicada.pk
    assert registro.metadata["new_version"] == 2
    assert registro.metadata["questions"] == 5


# ---------------------------------------------------------------------------
# Desempenho
# ---------------------------------------------------------------------------


def test_duplicacao_nao_faz_uma_consulta_por_alternativa(
    prova_publicada, admin_user, django_assert_max_num_queries
):
    """
    Cinco questoes com alternativas. O prefetch traz todas as opcoes numa
    consulta so; sem ele o numero cresceria com o tamanho da prova.
    """
    with django_assert_max_num_queries(30):
        duplicate_exam(prova_publicada, actor=admin_user)


# ---------------------------------------------------------------------------
# Concorrencia
# ---------------------------------------------------------------------------


def test_duplicacao_trava_a_raiz_da_linhagem(prova_publicada, admin_user):
    """
    Checagem barata e deterministica de que a trava existe.

    A raiz precisa ser lida com SELECT ... FOR UPDATE antes de o proximo
    numero de versao ser calculado. Sem isso, duas duplicacoes simultaneas
    leriam o mesmo maximo.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as consultas:
        duplicate_exam(prova_publicada, actor=admin_user)

    sql = " ".join(consulta["sql"].upper() for consulta in consultas)
    assert "FOR UPDATE" in sql


@pytest.mark.django_db(transaction=True)
def test_duas_duplicacoes_simultaneas_geram_versoes_diferentes(
    prova_publicada, admin_user
):
    """
    Teste concorrencial de verdade: duas threads duplicando a mesma prova.

    Com a trava sobre a raiz, a segunda espera e le a versao que a primeira
    acabou de gravar, entao o resultado e v2 e v3. Sem a trava, as duas
    calculariam v2 e uma cairia em IntegrityError.

    Cada thread precisa da propria conexao, e por isso fecha a sua no fim;
    e por isso tambem que o teste roda com transaction=True, sem a transacao
    unica que o pytest-django normalmente envolve em cada teste.
    """
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connection

    def duplicar():
        try:
            copia = duplicate_exam(prova_publicada, actor=admin_user)
            return copia.version
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futuros = [executor.submit(duplicar) for _ in range(2)]
        versoes = sorted(futuro.result(timeout=30) for futuro in futuros)

    assert versoes == [2, 3]
    assert Exam.objects.da_linhagem_de(prova_publicada).count() == 3
