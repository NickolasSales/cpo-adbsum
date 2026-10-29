"""conferir_ministros: consistencia do Controle de Ministros (Fase 2F)."""

import datetime
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone

from ministers import cobranca, comprovantes, services
from ministers import mensalidades as financeiro
from ministers.models import (
    PASTA_DOS_COMPROVANTES,
    MinisterAccount,
    MinisterFeeCharge,
    MinisterPaymentProof,
    MonthlyFeeStatus,
)
from ministers.tests.conftest import SENHA_MINISTRO, arquivo_enviado, dados_de_ministro, pdf_de_teste

pytestmark = pytest.mark.django_db

DAQUI_A_10 = timezone.localdate() + datetime.timedelta(days=10)


def conferir():
    saida = StringIO()
    call_command("conferir_ministros", stdout=saida)
    return saida.getvalue()


def conferir_com_erro():
    saida = StringIO()
    with pytest.raises(CommandError):
        call_command("conferir_ministros", stdout=saida)
    return saida.getvalue()


@pytest.fixture
def cenario(configuracao_pix):
    ministro = services.create_minister(dados=dados_de_ministro(), password=SENHA_MINISTRO)
    m = financeiro.create_fee(minister=ministro, reference_month="2026-10", due_date=DAQUI_A_10, amount="35.00")
    cobranca.gerar_cobranca(m)
    comprovantes.enviar_comprovante(m, arquivo_enviado(pdf_de_teste()))
    return m


def test_tudo_certo(cenario):
    texto = conferir()
    assert "Ministros 1 | acessos 1 | mensalidades 1 | cobrancas 1 | comprovantes 1" in texto
    assert "[ERRO]" not in texto
    assert "Nenhuma inconsistencia." in texto


def test_banco_vazio_tambem_confere():
    assert "Nenhuma inconsistencia." in conferir()


def test_acusa_cobranca_vigente_desatualizada(cenario):
    # So e possivel por fora do servico (update direto): e isso que a
    # conferencia existe para pegar.
    with connection.cursor() as cursor:
        cursor.execute("UPDATE ministers_ministermonthlyfee SET amount = 99 WHERE id = %s", [cenario.pk])
    texto = conferir_com_erro()
    assert "[ERRO] cobranca vigente com valor ou vencimento diferente da mensalidade pendente: 1" in texto


def test_acusa_comprovante_em_analise_de_mensalidade_paga(cenario):
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE ministers_ministermonthlyfee SET status='PAID', paid_at=%s, payment_method='PIX' WHERE id = %s",
            [timezone.localdate(), cenario.pk],
        )
    assert "[ERRO] comprovante aguardando analise de mensalidade que nao esta pendente: 1" in conferir_com_erro()


def test_acusa_arquivo_faltando_e_orfao(cenario):
    comprovante = MinisterPaymentProof.objects.get()
    Path(comprovante.file.path).unlink()
    (Path(settings.MEDIA_ROOT) / PASTA_DOS_COMPROVANTES / ("e" * 32 + ".pdf")).write_bytes(b"%PDF-1.4")
    texto = conferir_com_erro()
    assert "[ERRO] comprovante sem arquivo no disco: 1" in texto
    assert "[ERRO] arquivo na pasta de comprovantes sem registro: 1" in texto


def test_acusa_login_diferente_da_inscricao(cenario):
    MinisterAccount.objects.update(login_identifier="OUTRO")
    assert "[ERRO] acesso com login diferente da inscricao CONAMAD: 1" in conferir_com_erro()


def test_conferir_nao_escreve(cenario):
    antes = (MinisterPaymentProof.objects.count(), MinisterFeeCharge.objects.count())
    conferir()
    assert (MinisterPaymentProof.objects.count(), MinisterFeeCharge.objects.count()) == antes
    assert cenario.__class__.objects.get(pk=cenario.pk).status == MonthlyFeeStatus.PENDING
