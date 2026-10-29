"""Comprovantes de pagamento: modelo e servico (Fase 2E)."""

import datetime
import os
import threading
from pathlib import Path

import pytest
from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from audit.models import AuditEvent, AuditLog
from common.exceptions import DomainError
from ministers import cobranca, comprovantes, services
from ministers import mensalidades as financeiro
from ministers.models import (
    PASTA_DOS_COMPROVANTES,
    MinisterPaymentProof,
    MonthlyFeeStatus,
    PaymentMethod,
    ProofStatus,
)
from ministers.tests.conftest import (
    PIX_DE_TESTE,
    arquivo_enviado,
    dados_de_ministro,
    imagem_de_teste,
    pdf_de_teste,
)

pytestmark = pytest.mark.django_db

HOJE = timezone.localdate()
ONTEM = HOJE - datetime.timedelta(days=1)
DAQUI_A_10 = HOJE + datetime.timedelta(days=10)


def nova(ministro, competencia="2026-10", valor="35.00", vencimento=DAQUI_A_10, cobrar=True):
    mensalidade = financeiro.create_fee(
        minister=ministro, reference_month=competencia, due_date=vencimento, amount=valor
    )
    if cobrar:
        cobranca.gerar_cobranca(mensalidade)
    return mensalidade


def enviar(mensalidade, conteudo=None, nome="comprovante.pdf"):
    return comprovantes.enviar_comprovante(
        mensalidade, arquivo_enviado(conteudo or pdf_de_teste(), nome)
    )


def arquivos_no_disco():
    pasta = Path(settings.MEDIA_ROOT) / PASTA_DOS_COMPROVANTES
    return sorted(p.name for p in pasta.iterdir()) if pasta.exists() else []


@pytest.fixture
def pendente(ministro, configuracao_pix):
    return nova(ministro)


# ---------------------------------------------------------------------------
# Envio
# ---------------------------------------------------------------------------


def test_envio_cria_um_submitted_com_metadados(pendente):
    conteudo = pdf_de_teste()
    antes = timezone.now()
    c = enviar(pendente, conteudo, nome="Comprovante Outubro.pdf")
    c.refresh_from_db()
    assert c.status == ProofStatus.SUBMITTED
    assert c.fee_id == pendente.pk
    assert c.mime_type == "application/pdf"
    assert c.file_size == len(conteudo)
    assert len(c.sha256) == 64
    assert c.original_filename == "Comprovante Outubro.pdf"
    assert antes <= c.submitted_at <= timezone.now()
    assert c.reviewed_by is None and c.reviewed_at is None and c.rejection_reason == ""
    assert c.created_at and c.updated_at


def test_arquivo_tem_nome_aleatorio_e_fica_na_pasta_dos_comprovantes(pendente):
    c = enviar(pendente, nome="../../etc/joao outubro.pdf")
    pasta, nome = c.file.name.rsplit("/", 1)
    assert pasta == PASTA_DOS_COMPROVANTES
    assert len(nome) == len("0" * 32 + ".pdf") and nome.endswith(".pdf")
    assert "joao" not in c.file.name and ".." not in c.file.name
    caminho = Path(c.file.path)
    assert caminho.is_file()
    assert caminho.resolve().is_relative_to(Path(settings.MEDIA_ROOT).resolve())
    assert caminho.read_bytes()[:5] == b"%PDF-"


def test_arquivo_gravado_sem_leitura_para_outros(pendente):
    c = enviar(pendente)
    storage = MinisterPaymentProof._meta.get_field("file").storage
    assert storage.file_permissions_mode == 0o640
    assert storage.directory_permissions_mode == 0o750
    # No Windows do desenvolvimento o modo POSIX nao existe; no servidor, sim.
    if os.name == "posix":
        assert os.stat(c.file.path).st_mode & 0o777 == 0o640
        assert os.stat(os.path.dirname(c.file.path)).st_mode & 0o777 == 0o750


def test_extensao_interna_vem_do_conteudo(pendente):
    c = enviar(pendente, imagem_de_teste("PNG"), nome="print.jpg")
    assert c.mime_type == "image/png" and c.file.name.endswith(".png")


def test_mensalidade_continua_pendente_e_nos_totais(pendente):
    antes = financeiro.totais(financeiro.mensalidades_do_ministro(pendente.minister))
    enviar(pendente)
    pendente.refresh_from_db()
    assert pendente.status == MonthlyFeeStatus.PENDING
    depois = financeiro.totais(financeiro.mensalidades_do_ministro(pendente.minister))
    assert depois == antes
    assert depois["pendente"] == pendente.amount


def test_vencida_aceita(ministro, configuracao_pix):
    vencida = nova(ministro, vencimento=ONTEM)
    assert vencida.situacao == "OVERDUE"
    assert enviar(vencida).status == ProofStatus.SUBMITTED
    log = AuditLog.objects.get(event=AuditEvent.MINISTER_PAYMENT_PROOF_SUBMITTED)
    assert log.metadata["estava_vencida"] is True


def test_segundo_envio_com_um_em_analise_e_recusado_sem_deixar_arquivo(pendente):
    enviar(pendente)
    assert len(arquivos_no_disco()) == 1
    with pytest.raises(comprovantes.EnvioRecusado) as erro:
        enviar(pendente)
    assert "aguardando análise" in str(erro.value)
    assert MinisterPaymentProof.objects.count() == 1
    assert len(arquivos_no_disco()) == 1


@pytest.mark.parametrize(
    "transicao, mensagem",
    [
        (lambda m, a: financeiro.register_payment(m, paid_at=HOJE, payment_method="PIX", actor=a), "já está paga"),
        (lambda m, a: financeiro.exempt_fee(m, reason="isento", actor=a), "isenta"),
        (lambda m, a: financeiro.cancel_fee(m, reason="cancelada", actor=a), "cancelada"),
    ],
)
def test_paga_isenta_e_cancelada_nao_aceitam(pendente, admin_user, transicao, mensagem):
    transicao(pendente, admin_user)
    with pytest.raises(comprovantes.EnvioRecusado) as erro:
        enviar(pendente)
    assert mensagem in str(erro.value)
    assert not MinisterPaymentProof.objects.exists()
    assert arquivos_no_disco() == []


def test_sem_cobranca_nao_aceita(ministro, configuracao_pix):
    sem = nova(ministro, cobrar=False)
    with pytest.raises(comprovantes.EnvioRecusado) as erro:
        enviar(sem)
    assert "ainda não foi emitida" in str(erro.value)


def test_arquivo_invalido_nao_grava_nada(pendente):
    with pytest.raises(DomainError):
        enviar(pendente, b"MZ\x90\x00" + b"\x00" * 100, nome="comprovante.jpg")
    assert not MinisterPaymentProof.objects.exists()
    assert arquivos_no_disco() == []
    assert not AuditLog.objects.filter(event=AuditEvent.MINISTER_PAYMENT_PROOF_SUBMITTED).exists()


def test_auditoria_do_envio_sem_caminho_nem_conteudo(pendente):
    conteudo = pdf_de_teste()
    c = enviar(pendente, conteudo, nome="segredo-do-joao.pdf")
    log = AuditLog.objects.get(event=AuditEvent.MINISTER_PAYMENT_PROOF_SUBMITTED)
    assert log.actor is None
    assert log.entity_type == "MinisterMonthlyFee" and log.entity_id == str(pendente.pk)
    assert log.metadata["competencia"] == "2026-10"
    assert log.metadata["file_size"] == len(conteudo)
    assert log.metadata["mime_type"] == "application/pdf"
    assert log.metadata["comprovante"] == c.pk
    texto = str(log.metadata)
    assert c.file.name not in texto and "segredo" not in texto and "payment_proofs" not in texto
    assert c.sha256 not in texto and "000201" not in texto


# ---------------------------------------------------------------------------
# Constraint
# ---------------------------------------------------------------------------


def test_banco_recusa_dois_submitted_para_a_mesma_mensalidade(pendente):
    c = enviar(pendente)
    with pytest.raises(IntegrityError), transaction.atomic():
        MinisterPaymentProof.objects.create(
            fee=pendente, file="x.pdf", mime_type=c.mime_type, file_size=10, sha256=c.sha256
        )


def test_banco_recusa_recusa_sem_motivo_e_aprovacao_sem_data(pendente):
    c = enviar(pendente)
    with pytest.raises(IntegrityError), transaction.atomic():
        MinisterPaymentProof.objects.filter(pk=c.pk).update(status=ProofStatus.REJECTED, reviewed_at=timezone.now())
    with pytest.raises(IntegrityError), transaction.atomic():
        MinisterPaymentProof.objects.filter(pk=c.pk).update(status=ProofStatus.APPROVED)


@pytest.mark.parametrize(
    "campos",
    [
        {"file_size": 0},
        {"file_size": 5 * 1024 * 1024 + 1},
        {"mime_type": "image/svg+xml"},
        {"sha256": "nao-e-hash"},
    ],
)
def test_banco_recusa_metadados_invalidos(pendente, campos):
    c = enviar(pendente)
    with pytest.raises(IntegrityError), transaction.atomic():
        MinisterPaymentProof.objects.filter(pk=c.pk).update(**campos)


def test_comprovante_nao_e_excluido(pendente):
    c = enviar(pendente)
    with pytest.raises(ValueError):
        c.delete()


# ---------------------------------------------------------------------------
# Aprovacao
# ---------------------------------------------------------------------------


def test_aprovar_marca_paga_pelo_mesmo_servico_da_baixa_manual(pendente, admin_user):
    c = enviar(pendente)
    pago_em = HOJE - datetime.timedelta(days=0)
    comprovantes.aprovar_comprovante(c, paid_at=pago_em, actor=admin_user)

    c.refresh_from_db()
    pendente.refresh_from_db()
    assert c.status == ProofStatus.APPROVED
    assert c.reviewed_by == admin_user and c.reviewed_at is not None
    assert pendente.status == MonthlyFeeStatus.PAID
    assert pendente.paid_at == pago_em
    assert pendente.payment_method == PaymentMethod.PIX
    assert pendente.payment_reference == "Comprovante do portal nº {}".format(c.pk)
    assert "payment_proofs" not in pendente.payment_reference

    # O evento financeiro e o da 2A, uma vez so; o da analise e separado.
    assert AuditLog.objects.filter(event=AuditEvent.MINISTER_FEE_PAYMENT_REGISTERED).count() == 1
    log = AuditLog.objects.get(event=AuditEvent.MINISTER_PAYMENT_PROOF_APPROVED)
    assert log.actor == admin_user
    assert log.metadata["competencia"] == "2026-10"
    assert log.metadata["payment_method"] == "PIX"


def test_totais_so_mudam_depois_da_aprovacao(pendente, admin_user):
    consulta = financeiro.mensalidades_do_ministro(pendente.minister)
    c = enviar(pendente)
    assert financeiro.totais(consulta)["recebido"] == 0
    comprovantes.aprovar_comprovante(c, paid_at=HOJE, actor=admin_user)
    totais = financeiro.totais(consulta)
    assert totais["recebido"] == pendente.amount and totais["pendente"] == 0


def test_data_do_pagamento_pode_ser_anterior_ao_envio(ministro, configuracao_pix, admin_user):
    vencida = nova(ministro, vencimento=ONTEM - datetime.timedelta(days=10))
    c = enviar(vencida)
    dias_antes = HOJE - datetime.timedelta(days=15)
    comprovantes.aprovar_comprovante(c, paid_at=dias_antes, actor=admin_user)
    vencida.refresh_from_db()
    assert vencida.paid_at == dias_antes


def test_data_do_pagamento_posterior_ao_envio_e_recusada(pendente, admin_user):
    c = enviar(pendente)
    MinisterPaymentProof.objects.filter(pk=c.pk).update(submitted_at=timezone.now() - datetime.timedelta(days=3))
    c.refresh_from_db()
    with pytest.raises(DomainError) as erro:
        comprovantes.aprovar_comprovante(c, paid_at=HOJE, actor=admin_user)
    assert "posterior ao envio" in str(erro.value)
    c.refresh_from_db()
    pendente.refresh_from_db()
    assert c.status == ProofStatus.SUBMITTED and pendente.status == MonthlyFeeStatus.PENDING


def test_data_futura_e_recusada_pela_regra_da_2a(pendente, admin_user):
    c = enviar(pendente)
    MinisterPaymentProof.objects.filter(pk=c.pk).update(submitted_at=timezone.now() + datetime.timedelta(days=5))
    c.refresh_from_db()
    with pytest.raises(DomainError) as erro:
        comprovantes.aprovar_comprovante(c, paid_at=HOJE + datetime.timedelta(days=2), actor=admin_user)
    assert "futuro" in str(erro.value)
    c.refresh_from_db()
    assert c.status == ProofStatus.SUBMITTED


def test_aprovar_de_novo_e_decisao_ja_tomada(pendente, admin_user):
    c = enviar(pendente)
    comprovantes.aprovar_comprovante(c, paid_at=HOJE, actor=admin_user)
    with pytest.raises(comprovantes.DecisaoJaTomada):
        comprovantes.aprovar_comprovante(c, paid_at=HOJE, actor=admin_user)
    with pytest.raises(comprovantes.DecisaoJaTomada):
        comprovantes.recusar_comprovante(c, motivo="tarde demais", actor=admin_user)
    assert AuditLog.objects.filter(event=AuditEvent.MINISTER_FEE_PAYMENT_REGISTERED).count() == 1


def test_paga_por_comprovante_nao_aceita_outro(pendente, admin_user):
    comprovantes.aprovar_comprovante(enviar(pendente), paid_at=HOJE, actor=admin_user)
    with pytest.raises(comprovantes.EnvioRecusado):
        enviar(pendente)


# ---------------------------------------------------------------------------
# Recusa, reenvio e historico
# ---------------------------------------------------------------------------


def test_recusar_exige_motivo(pendente, admin_user):
    c = enviar(pendente)
    for vazio in ("", "   ", "\n\t"):
        with pytest.raises(DomainError) as erro:
            comprovantes.recusar_comprovante(c, motivo=vazio, actor=admin_user)
        assert str(erro.value) == "Informe o motivo da recusa."
    with pytest.raises(DomainError):
        comprovantes.recusar_comprovante(c, motivo="x" * 1001, actor=admin_user)
    c.refresh_from_db()
    assert c.status == ProofStatus.SUBMITTED


def test_recusar_nao_marca_paga(pendente, admin_user):
    c = enviar(pendente)
    comprovantes.recusar_comprovante(c, motivo="  Imagem ilegível.  ", actor=admin_user)
    c.refresh_from_db()
    pendente.refresh_from_db()
    assert c.status == ProofStatus.REJECTED
    assert c.rejection_reason == "Imagem ilegível."
    assert c.reviewed_by == admin_user and c.reviewed_at is not None
    assert pendente.status == MonthlyFeeStatus.PENDING
    assert pendente.paid_at is None
    log = AuditLog.objects.get(event=AuditEvent.MINISTER_PAYMENT_PROOF_REJECTED)
    assert log.metadata["rejected"] is True and log.metadata["competencia"] == "2026-10"
    assert "Imagem ilegível" not in str(log.metadata)
    assert not AuditLog.objects.filter(event=AuditEvent.MINISTER_FEE_PAYMENT_REGISTERED).exists()


def test_reenvio_depois_da_recusa_preserva_o_historico(pendente, admin_user):
    conteudo = pdf_de_teste("mesmo arquivo")
    primeiro = enviar(pendente, conteudo)
    comprovantes.recusar_comprovante(primeiro, motivo="Imagem ilegível.", actor=admin_user)
    retrato = MinisterPaymentProof.objects.filter(pk=primeiro.pk).values().get()
    arquivo_do_primeiro = Path(primeiro.file.path).read_bytes()

    # O mesmo arquivo de novo e permitido: SHA igual nao e fraude.
    segundo = enviar(pendente, conteudo)
    assert segundo.pk != primeiro.pk
    assert segundo.sha256 == primeiro.sha256
    assert segundo.file.name != primeiro.file.name
    comprovantes.aprovar_comprovante(segundo, paid_at=HOJE, actor=admin_user)

    assert MinisterPaymentProof.objects.filter(pk=primeiro.pk).values().get() == retrato
    assert Path(primeiro.file.path).read_bytes() == arquivo_do_primeiro
    historico = comprovantes.do_portal(pendente)
    assert [c.status for c in historico] == [ProofStatus.REJECTED, ProofStatus.APPROVED]
    assert len(arquivos_no_disco()) == 2


def test_portal_nao_carrega_dados_internos(pendente, admin_user):
    comprovantes.recusar_comprovante(enviar(pendente), motivo="Valor não corresponde.", actor=admin_user)
    (item,) = comprovantes.do_portal(pendente)
    adiados = item.get_deferred_fields()
    for campo in ("reviewed_by_id", "sha256", "file", "original_filename", "file_size"):
        assert campo in adiados, campo


# ---------------------------------------------------------------------------
# Comprovante em analise x transicoes da 2A
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transicao",
    [
        lambda m, a: financeiro.register_payment(m, paid_at=HOJE, payment_method="CASH", actor=a),
        lambda m, a: financeiro.exempt_fee(m, reason="isento", actor=a),
        lambda m, a: financeiro.cancel_fee(m, reason="cancelada", actor=a),
    ],
)
def test_com_comprovante_em_analise_a_mensalidade_nao_sai_de_pendente(pendente, admin_user, transicao):
    enviar(pendente)
    with pytest.raises(DomainError) as erro:
        transicao(pendente, admin_user)
    assert "Aprove ou recuse o comprovante antes" in str(erro.value)
    pendente.refresh_from_db()
    assert pendente.status == MonthlyFeeStatus.PENDING


def test_depois_da_recusa_a_baixa_manual_volta_a_funcionar(pendente, admin_user):
    comprovantes.recusar_comprovante(enviar(pendente), motivo="outra competência", actor=admin_user)
    financeiro.register_payment(pendente, paid_at=HOJE, payment_method="CASH", actor=admin_user)
    pendente.refresh_from_db()
    assert pendente.status == MonthlyFeeStatus.PAID


def test_editar_valor_com_comprovante_em_analise_continua_permitido(pendente, admin_user):
    c = enviar(pendente)
    financeiro.update_fee(pendente, due_date=pendente.due_date, amount="40.00", notes="", actor=admin_user)
    c.refresh_from_db()
    assert c.status == ProofStatus.SUBMITTED


def test_estorno_depois_da_aprovacao_mantem_o_comprovante_aprovado(pendente, admin_user):
    c = enviar(pendente)
    comprovantes.aprovar_comprovante(c, paid_at=HOJE, actor=admin_user)
    financeiro.reverse_payment(pendente, reason="Pix devolvido", actor=admin_user)
    c.refresh_from_db()
    assert c.status == ProofStatus.APPROVED
    # A mensalidade voltou a pendente: o ministro pode enviar outro.
    assert enviar(pendente).status == ProofStatus.SUBMITTED


# ---------------------------------------------------------------------------
# Concorrencia (PostgreSQL de verdade, threads com conexoes proprias)
# ---------------------------------------------------------------------------


def _em_paralelo(*funcoes):
    barreira = threading.Barrier(len(funcoes))
    resultados, erros = [None] * len(funcoes), [None] * len(funcoes)

    def rodar(indice, funcao):
        try:
            barreira.wait()
            resultados[indice] = funcao()
        except Exception as exc:
            erros[indice] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=rodar, args=(i, f)) for i, f in enumerate(funcoes)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return resultados, erros


def _cenario():
    cobranca.salvar_configuracao(dict(PIX_DE_TESTE))
    ministro = services.create_minister(dados=dados_de_ministro())
    return nova(ministro)


def _admin(email):
    from accounts.models import User, UserRole

    return User.objects.create_user(email=email, password="Admin#Seguro2026", full_name="Admin", role=UserRole.ADMIN)


@pytest.mark.django_db(transaction=True)
def test_dois_envios_simultaneos_criam_um_so():
    mensalidade = _cenario()
    resultados, erros = _em_paralelo(lambda: enviar(mensalidade), lambda: enviar(mensalidade))
    assert sum(r is not None for r in resultados) == 1
    recusas = [e for e in erros if e is not None]
    assert len(recusas) == 1 and isinstance(recusas[0], comprovantes.EnvioRecusado)
    assert MinisterPaymentProof.objects.count() == 1
    assert len(arquivos_no_disco()) == 1


@pytest.mark.django_db(transaction=True)
def test_duas_aprovacoes_simultaneas_um_so_pagamento():
    mensalidade = _cenario()
    c = enviar(mensalidade)
    a, b = _admin("a@example.com"), _admin("b@example.com")
    resultados, erros = _em_paralelo(
        lambda: comprovantes.aprovar_comprovante(c, paid_at=HOJE, actor=a),
        lambda: comprovantes.aprovar_comprovante(c, paid_at=HOJE, actor=b),
    )
    assert sum(r is not None for r in resultados) == 1
    recusas = [e for e in erros if e is not None]
    assert len(recusas) == 1 and isinstance(recusas[0], comprovantes.DecisaoJaTomada)
    assert AuditLog.objects.filter(event=AuditEvent.MINISTER_FEE_PAYMENT_REGISTERED).count() == 1
    assert AuditLog.objects.filter(event=AuditEvent.MINISTER_PAYMENT_PROOF_APPROVED).count() == 1


@pytest.mark.django_db(transaction=True)
def test_aprovar_e_recusar_ao_mesmo_tempo_uma_decisao_vale():
    mensalidade = _cenario()
    c = enviar(mensalidade)
    a, b = _admin("a@example.com"), _admin("b@example.com")
    resultados, erros = _em_paralelo(
        lambda: comprovantes.aprovar_comprovante(c, paid_at=HOJE, actor=a),
        lambda: comprovantes.recusar_comprovante(c, motivo="ilegível", actor=b),
    )
    assert sum(r is not None for r in resultados) == 1
    assert sum(isinstance(e, comprovantes.DecisaoJaTomada) for e in erros) == 1
    c.refresh_from_db()
    mensalidade.refresh_from_db()
    if c.status == ProofStatus.APPROVED:
        assert mensalidade.status == MonthlyFeeStatus.PAID and c.rejection_reason == ""
    else:
        assert c.status == ProofStatus.REJECTED and mensalidade.status == MonthlyFeeStatus.PENDING
    decisoes = AuditLog.objects.filter(
        event__in=[AuditEvent.MINISTER_PAYMENT_PROOF_APPROVED, AuditEvent.MINISTER_PAYMENT_PROOF_REJECTED]
    )
    assert decisoes.count() == 1


@pytest.mark.django_db(transaction=True)
def test_envio_e_baixa_manual_ao_mesmo_tempo_ficam_consistentes():
    mensalidade = _cenario()
    a = _admin("a@example.com")
    resultados, erros = _em_paralelo(
        lambda: enviar(mensalidade),
        lambda: financeiro.register_payment(mensalidade, paid_at=HOJE, payment_method="CASH", actor=a),
    )
    assert sum(r is not None for r in resultados) == 1
    assert sum(e is not None for e in erros) == 1
    mensalidade.refresh_from_db()
    em_analise = MinisterPaymentProof.objects.filter(status=ProofStatus.SUBMITTED).count()
    if mensalidade.status == MonthlyFeeStatus.PAID:
        # A baixa ganhou: nenhum comprovante nasceu depois dela.
        assert em_analise == 0 and arquivos_no_disco() == []
    else:
        # O envio ganhou: a baixa manual foi recusada.
        assert em_analise == 1


# ---------------------------------------------------------------------------
# conferir_comprovantes (verificacao de restore)
# ---------------------------------------------------------------------------


def _conferir():
    from io import StringIO

    from django.core.management import call_command

    saida = StringIO()
    call_command("conferir_comprovantes", stdout=saida)
    return saida.getvalue()


def test_conferir_comprovantes_ok(pendente, admin_user):
    comprovantes.recusar_comprovante(enviar(pendente), motivo="x", actor=admin_user)
    enviar(pendente, imagem_de_teste("PNG"), "print.png")
    saida = _conferir()
    assert "Comprovantes no banco: 2" in saida
    assert "Conferidos (arquivo presente e SHA-256 igual): 2" in saida
    assert "OK:" in saida


def test_conferir_comprovantes_acusa_arquivo_faltando_e_alterado(pendente, admin_user):
    from django.core.management.base import CommandError

    primeiro = enviar(pendente)
    comprovantes.recusar_comprovante(primeiro, motivo="x", actor=admin_user)
    segundo = enviar(pendente)
    os.remove(primeiro.file.path)
    Path(segundo.file.path).write_bytes(b"%PDF-1.4 adulterado")
    with pytest.raises(CommandError):
        _conferir()


def test_conferir_comprovantes_conta_orfao_sem_falhar(pendente):
    enviar(pendente)
    pasta = Path(settings.MEDIA_ROOT) / PASTA_DOS_COMPROVANTES
    (pasta / ("f" * 32 + ".pdf")).write_bytes(b"%PDF-1.4")
    saida = _conferir()
    assert "Arquivos sem registro (orfaos): 1" in saida and "OK:" in saida
    # So le: o orfao continua la.
    assert (pasta / ("f" * 32 + ".pdf")).exists()
