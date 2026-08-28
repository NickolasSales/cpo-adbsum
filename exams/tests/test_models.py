"""
Modelo Exam: campos, defaults e garantias do banco.

As constraints sao testadas por escrita direta no banco, com QuerySet.update
ou Model.objects.create, e nao pelo formulario. Se um teste passasse apenas
porque o formulario recusou, ele nao provaria nada sobre bulk_create, sobre
uma migration de dados ou sobre alguem abrindo o psql.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.hashers import check_password
from django.db import IntegrityError, transaction
from django.utils import timezone

from common.exceptions import DomainError
from exams.models import Exam, ExamStatus, QuestionType
from exams.services import create_exam, create_question, set_exam_password

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Criacao e defaults
# ---------------------------------------------------------------------------


def test_prova_nasce_rascunho_versao_um_e_raiz(prova):
    assert prova.status == ExamStatus.DRAFT
    assert prova.version == 1
    assert prova.root_exam_id is None
    assert prova.parent_exam_id is None
    assert prova.published_at is None
    assert prova.closed_at is None
    assert prova.total_points == Decimal("0.00")


def test_prova_registra_quem_criou(prova, admin_user):
    assert prova.created_by_id == admin_user.pk


def test_modulo_e_obrigatorio(admin_user, janela):
    abertura, encerramento = janela
    with pytest.raises(DomainError) as erro:
        create_exam(
            module=None,
            title="Sem modulo",
            open_at=abertura,
            close_at=encerramento,
            duration_minutes=60,
            passing_score=Decimal("8.00"),
            max_attempts=1,
            actor=admin_user,
        )
    assert "modulo" in str(erro.value).lower()


def test_nao_cria_prova_em_modulo_inativo(modulo_inativo, admin_user, janela):
    abertura, encerramento = janela
    with pytest.raises(DomainError) as erro:
        create_exam(
            module=modulo_inativo,
            title="Prova",
            open_at=abertura,
            close_at=encerramento,
            duration_minutes=60,
            passing_score=Decimal("8.00"),
            max_attempts=1,
            actor=admin_user,
        )
    assert "inativo" in str(erro.value).lower()


def test_titulo_recebe_trim(modulo, admin_user, janela):
    abertura, encerramento = janela
    prova = create_exam(
        module=modulo,
        title="   Avaliacao Final   ",
        open_at=abertura,
        close_at=encerramento,
        duration_minutes=60,
        passing_score=Decimal("8.00"),
        max_attempts=1,
        actor=admin_user,
    )
    assert prova.title == "Avaliacao Final"


@pytest.mark.parametrize("titulo", ["", "   ", "\t\n"])
def test_titulo_so_com_espaco_e_recusado(modulo, admin_user, titulo):
    with pytest.raises(DomainError) as erro:
        create_exam(
            module=modulo,
            title=titulo,
            passing_score=Decimal("8.00"),
            max_attempts=1,
            actor=admin_user,
        )
    assert "titulo" in str(erro.value).lower()


# ---------------------------------------------------------------------------
# Nota minima
# ---------------------------------------------------------------------------


def test_passing_score_e_decimal_e_nao_float(prova):
    assert isinstance(prova.passing_score, Decimal)
    assert prova.passing_score == Decimal("8.00")


@pytest.mark.parametrize("nota", ["-0.01", "-1", "10.01", "11"])
def test_passing_score_fora_da_escala_e_recusada_pelo_servico(modulo, admin_user, nota):
    with pytest.raises(DomainError) as erro:
        create_exam(
            module=modulo,
            title="Prova",
            passing_score=Decimal(nota),
            max_attempts=1,
            actor=admin_user,
        )
    assert "0 e 10" in str(erro.value)


@pytest.mark.parametrize("nota", ["-0.01", "10.01"])
def test_passing_score_fora_da_escala_e_recusada_pelo_banco(prova, nota):
    """
    Contraprova no nivel do banco.

    O servico ja recusa, mas um update direto nao passa por ele. E a
    constraint que garante que nenhuma prova existente fica com nota fora da
    escala.
    """
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Exam.objects.filter(pk=prova.pk).update(passing_score=Decimal(nota))


@pytest.mark.parametrize("nota", ["0.00", "5.50", "10.00"])
def test_passing_score_dentro_da_escala_e_aceita(prova, nota):
    Exam.objects.filter(pk=prova.pk).update(passing_score=Decimal(nota))
    prova.refresh_from_db()
    assert prova.passing_score == Decimal(nota)


# ---------------------------------------------------------------------------
# Duracao e tentativas
# ---------------------------------------------------------------------------


def test_duracao_zero_ou_negativa_e_recusada_pelo_servico(modulo, admin_user):
    for duracao in (0, -1, -60):
        with pytest.raises(DomainError) as erro:
            create_exam(
                module=modulo,
                title="Prova",
                duration_minutes=duracao,
                passing_score=Decimal("8.00"),
                max_attempts=1,
                actor=admin_user,
            )
        assert "duracao" in str(erro.value).lower()


def test_duracao_zero_e_recusada_pelo_banco(prova):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Exam.objects.filter(pk=prova.pk).update(duration_minutes=0)


def test_duracao_pode_ficar_vazia_em_rascunho(modulo, admin_user):
    prova = create_exam(
        module=modulo,
        title="Rascunho incompleto",
        duration_minutes=None,
        passing_score=Decimal("8.00"),
        max_attempts=1,
        actor=admin_user,
    )
    assert prova.duration_minutes is None
    assert prova.status == ExamStatus.DRAFT


@pytest.mark.parametrize("tentativas", [0, -1])
def test_tentativas_menor_que_um_e_recusada_pelo_servico(modulo, admin_user, tentativas):
    with pytest.raises(DomainError) as erro:
        create_exam(
            module=modulo,
            title="Prova",
            passing_score=Decimal("8.00"),
            max_attempts=tentativas,
            actor=admin_user,
        )
    assert "tentativa" in str(erro.value).lower()


def test_tentativas_zero_e_recusada_pelo_banco(prova):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Exam.objects.filter(pk=prova.pk).update(max_attempts=0)


# ---------------------------------------------------------------------------
# Janela
# ---------------------------------------------------------------------------


def test_janela_invertida_e_recusada_pelo_servico(modulo, admin_user):
    agora = timezone.now()
    with pytest.raises(DomainError) as erro:
        create_exam(
            module=modulo,
            title="Prova",
            open_at=agora + timedelta(days=2),
            close_at=agora + timedelta(days=1),
            passing_score=Decimal("8.00"),
            max_attempts=1,
            actor=admin_user,
        )
    assert "posterior" in str(erro.value).lower()


def test_janela_invertida_e_recusada_pelo_banco(prova):
    agora = timezone.now()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Exam.objects.filter(pk=prova.pk).update(
                open_at=agora + timedelta(days=2), close_at=agora + timedelta(days=1)
            )


def test_datas_sao_timezone_aware(prova):
    assert timezone.is_aware(prova.open_at)
    assert timezone.is_aware(prova.close_at)


def test_janela_pode_ficar_vazia_em_rascunho(modulo, admin_user):
    prova = create_exam(
        module=modulo,
        title="Sem janela ainda",
        open_at=None,
        close_at=None,
        passing_score=Decimal("8.00"),
        max_attempts=1,
        actor=admin_user,
    )
    assert prova.open_at is None and prova.close_at is None


# ---------------------------------------------------------------------------
# Versionamento
# ---------------------------------------------------------------------------


def test_versao_menor_que_um_e_recusada_pelo_banco(prova):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Exam.objects.filter(pk=prova.pk).update(version=0)


def test_duas_versoes_iguais_na_mesma_linhagem_sao_recusadas(prova, modulo, admin_user):
    """
    A garantia final do versionamento.

    O servico calcula a proxima versao sob trava, mas esta constraint e o que
    protege contra qualquer caminho novo que esqueca a transacao.

    As duas linhas precisam ser coerentes em raiz e origem, senao o
    IntegrityError viria de exam_linhagem_parent_coerente e o teste passaria
    sem nunca exercitar a unicidade que ele existe para verificar.
    """
    Exam.objects.create(
        module=modulo,
        title="Copia",
        version=2,
        root_exam=prova,
        parent_exam=prova,
        passing_score=Decimal("8.00"),
        max_attempts=1,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Exam.objects.create(
                module=modulo,
                title="Outra copia",
                version=2,
                root_exam=prova,
                parent_exam=prova,
                passing_score=Decimal("8.00"),
                max_attempts=1,
            )


def test_linhagem_reune_raiz_e_versoes(prova, modulo):
    filha = Exam.objects.create(
        module=modulo,
        title="v2",
        version=2,
        root_exam=prova,
        parent_exam=prova,
        passing_score=Decimal("8.00"),
        max_attempts=1,
    )
    linhagem = set(Exam.objects.da_linhagem_de(prova).values_list("pk", flat=True))
    assert linhagem == {prova.pk, filha.pk}
    # A partir da filha o resultado precisa ser o mesmo conjunto.
    assert set(Exam.objects.da_linhagem_de(filha).values_list("pk", flat=True)) == linhagem


# ---------------------------------------------------------------------------
# Senha da prova
# ---------------------------------------------------------------------------


def test_prova_nasce_sem_senha(prova):
    assert prova.access_password_hash == ""
    assert prova.tem_senha is False


def test_senha_e_gravada_como_hash_e_nunca_em_texto(prova, admin_user):
    senha = "Prova#Turma2026"
    set_exam_password(prova, senha, actor=admin_user)
    prova.refresh_from_db()

    assert prova.tem_senha is True
    assert prova.access_password_hash != senha
    assert senha not in prova.access_password_hash
    # Um hash reconhecivel do Django, e nao o texto disfarcado.
    assert prova.access_password_hash.startswith("pbkdf2_")
    assert check_password(senha, prova.access_password_hash) is True


def test_senha_nao_aparece_em_nenhum_campo_de_texto_da_prova(prova, admin_user):
    senha = "Segredo#DaProva2026"
    set_exam_password(prova, senha, actor=admin_user)
    prova.refresh_from_db()

    campos_de_texto = [
        prova.title,
        prova.description,
        prova.instructions,
        prova.failure_message,
    ]
    for valor in campos_de_texto:
        assert senha not in (valor or "")


def test_senha_curta_e_recusada(prova, admin_user):
    with pytest.raises(DomainError):
        set_exam_password(prova, "abc", actor=admin_user)


@pytest.mark.parametrize("valor", ["", "   ", None])
def test_senha_vazia_e_recusada(prova, admin_user, valor):
    with pytest.raises(DomainError):
        set_exam_password(prova, valor, actor=admin_user)


# ---------------------------------------------------------------------------
# Pontuacao
# ---------------------------------------------------------------------------


def test_pontos_das_questoes_soma_apenas_as_ativas(prova, admin_user):
    create_question(
        prova,
        type=QuestionType.ESSAY,
        text="Ativa",
        points="4.00",
        actor=admin_user,
    )
    inativa = create_question(
        prova,
        type=QuestionType.ESSAY,
        text="Inativa",
        points="6.00",
        active=False,
        actor=admin_user,
    )
    assert inativa.active is False
    assert prova.pontos_das_questoes == Decimal("4.00")


def test_pontos_vigentes_em_rascunho_e_a_soma_corrente(prova_pronta):
    assert prova_pronta.e_rascunho is True
    assert prova_pronta.total_points == Decimal("0.00")
    assert prova_pronta.pontos_vigentes == Decimal("10.00")


def test_pontos_vigentes_apos_publicar_e_o_snapshot(prova_publicada):
    assert prova_publicada.pontos_vigentes == prova_publicada.total_points
    assert prova_publicada.total_points == Decimal("10.00")


def test_total_de_pontos_negativo_e_recusado_pelo_banco(prova):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Exam.objects.filter(pk=prova.pk).update(total_points=Decimal("-1.00"))


def test_str_da_prova_mostra_titulo_e_versao(prova):
    assert str(prova) == "Avaliacao Modulo 1 (v1)"
