"""
O que o banco garante sozinho sobre um certificado.

Estes testes nao passam por servico nem por view de proposito: eles gravam
direto no modelo para provar que a regra sobrevive a um UPDATE manual, a um
script de migracao e a qualquer caminho futuro que alguem invente.
"""

import uuid

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from certificates.models import (
    VERSAO_ATUAL_DO_MODELO,
    Certificate,
    CertificateStatus,
)

pytestmark = pytest.mark.django_db


def tentativa_crua(prova, aluno, numero=9):
    """
    ExamAttempt gravada direto, sem passar pelo fluxo de realizacao.

    Estes testes exercitam constraints da tabela de certificados; construir
    uma segunda tentativa completa gastaria segundos para produzir a mesma
    chave estrangeira. O fluxo de verdade e exercitado no test_issue.
    """
    from decimal import Decimal

    from django.utils import timezone

    from exams.models import ExamAttempt

    agora = timezone.now()
    return ExamAttempt.objects.create(
        student=aluno,
        exam=prova,
        attempt_number=numero,
        started_at=agora,
        expires_at=agora + timezone.timedelta(hours=1),
        total_points_snapshot=Decimal("10.00"),
        passing_score_snapshot=Decimal("6.00"),
    )


def test_uma_tentativa_gera_no_maximo_um_certificado(certificado, tentativa_aprovada):
    """
    OneToOne, e nao ForeignKey.

    Dois certificados para a mesma conclusao seriam dois documentos autenticos
    e verificaveis circulando, com codigos diferentes. O banco recusa o
    segundo mesmo que a camada de servico falhe.
    """
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Certificate.objects.create(
                attempt=tentativa_aprovada,
                student_name_snapshot="Outro",
                module_name_snapshot="Outro",
                exam_title_snapshot="Outra",
                institution_name_snapshot="Outra",
            )


def test_o_codigo_de_verificacao_e_unico(certificado, outro_student):
    """
    O unique protege contra colisao vinda de qualquer caminho.

    Nao ha como gerar a colisao pela interface — o default e UUID4 —, entao o
    teste grava o codigo repetido a mao, que e exatamente o cenario que a
    constraint existe para cobrir.
    """
    vizinha = tentativa_crua(certificado.attempt.exam, outro_student)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Certificate.objects.create(
                attempt=vizinha,
                verification_code=certificado.verification_code,
                student_name_snapshot="Outro",
                module_name_snapshot="Outro",
                exam_title_snapshot="Outra",
                institution_name_snapshot="Outra",
            )


def test_o_codigo_e_aleatorio_e_nao_sequencial(certificado, outro_student):
    """
    UUID4, e nao id sequencial nem valor derivado do aluno.

    O codigo entra em QR impresso e em URL publica sem autenticacao. Se fosse
    derivavel ou sequencial, quem tivesse um certificado conseguiria enumerar
    os outros somando um.
    """
    assert isinstance(certificado.verification_code, uuid.UUID)
    assert certificado.verification_code.version == 4
    # Variante RFC 4122: confirma que os bits aleatorios foram gerados pelo
    # algoritmo, e nao montados a mao a partir de outra coisa.
    assert certificado.verification_code.variant == uuid.RFC_4122

    vizinho = Certificate.objects.create(
        attempt=tentativa_crua(certificado.attempt.exam, outro_student),
        student_name_snapshot="Vizinho",
        module_name_snapshot="Vizinho",
        exam_title_snapshot="Vizinha",
        institution_name_snapshot="Vizinha",
    )

    distancia = abs(certificado.verification_code.int - vizinho.verification_code.int)
    # Dois inteiros de 122 bits aleatorios ficam astronomicamente distantes.
    # Um contador disfarcado de UUID daria distancia 1.
    assert distancia > 2 ** 100


def test_nasce_ativo_e_na_versao_atual(certificado):
    assert certificado.status == CertificateStatus.ACTIVE
    assert certificado.esta_valido is True
    assert certificado.revoked_at is None
    assert certificado.revoked_by is None
    assert certificado.template_version == VERSAO_ATUAL_DO_MODELO


def test_os_snapshots_guardam_o_texto_do_dia(certificado, tentativa_aprovada, settings):
    aluno = tentativa_aprovada.student
    prova = tentativa_aprovada.exam

    assert certificado.student_name_snapshot == aluno.full_name
    assert certificado.module_name_snapshot == prova.module.name
    assert certificado.exam_title_snapshot == prova.title
    assert certificado.institution_name_snapshot == settings.INSTITUTION_NAME


def test_renomear_o_modulo_nao_altera_certificado_ja_emitido(
    certificado, tentativa_aprovada
):
    """
    A razao de existir dos snapshots.

    Um certificado e um documento assinado: ele nao pode mudar de texto
    porque alguem renomeou um registro no banco dois anos depois.
    """
    modulo = tentativa_aprovada.exam.module
    nome_original = modulo.name

    modulo.name = "Formacao Basica"
    modulo.save(update_fields=["name"])

    certificado.refresh_from_db()
    assert certificado.module_name_snapshot == nome_original
    assert certificado.module_name_snapshot != modulo.name


def test_revogado_sem_data_e_recusado_pelo_banco(certificado):
    """
    A pagina publica decide "valido ou revogado" por este campo.

    Um UPDATE que mudasse so o status deixaria um certificado revogado sem
    data de revogacao — a tela diria "revogado em " e ninguem saberia quando.
    """
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Certificate.objects.filter(pk=certificado.pk).update(
                status=CertificateStatus.REVOKED
            )


def test_ativo_com_data_de_revogacao_e_recusado(certificado):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Certificate.objects.filter(pk=certificado.pk).update(
                revoked_at=timezone.now()
            )


def test_situacao_desconhecida_e_recusada(certificado):
    """
    Valor de 8 caracteres de proposito: e o limite da coluna.

    Com um texto maior o PostgreSQL recusaria pelo tipo, antes de chegar na
    CHECK — e o teste passaria sem provar que a constraint existe.
    """
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Certificate.objects.filter(pk=certificado.pk).update(status="CANCELADO"[:8])


def test_a_tentativa_nao_pode_ser_apagada_por_baixo(certificado, tentativa_aprovada):
    """PROTECT: apagar a tentativa destruiria a origem do documento."""
    from django.db.models import ProtectedError

    with pytest.raises(ProtectedError):
        with transaction.atomic():
            tentativa_aprovada.delete()
