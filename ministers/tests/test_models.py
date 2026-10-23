"""Modelo, normalizacao e restricoes do cadastro de ministros."""

import datetime

import pytest
from django.db import IntegrityError, transaction

from audit.models import AuditEvent, AuditLog
from common.exceptions import DomainError
from ministers import services
from ministers.models import (
    Minister,
    MinisterAccount,
    MinisterialTitle,
    MinisterSource,
)
from ministers.normalizacao import normalizar_nome, normalizar_registro
from ministers.tests.conftest import SENHA_MINISTRO, dados_de_ministro

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Normalizacao
# ---------------------------------------------------------------------------


def test_nome_perde_espacos_e_mantem_caixa_e_acento():
    assert normalizar_nome("  JOÃO   da  Conceição ") == "JOÃO da Conceição"


def test_registro_numerico_do_excel_vira_texto_sem_ponto_zero():
    assert normalizar_registro(132921) == "132921"
    assert normalizar_registro(132921.0) == "132921"


def test_registro_preserva_zeros_a_esquerda_e_remove_espacos():
    assert normalizar_registro(" 007764 ") == "007764"
    assert normalizar_registro("132 921") == "132921"
    assert normalizar_registro("ab-12") == "AB-12"


def test_registro_vazio():
    assert normalizar_registro(None) == ""
    assert normalizar_registro("   ") == ""


# ---------------------------------------------------------------------------
# Criacao
# ---------------------------------------------------------------------------


def test_criacao_normaliza_e_guarda_matriculas_como_texto():
    ministro = services.create_minister(
        dados=dados_de_ministro(
            full_name="  Rodrigo   Exemplo  ",
            conamad_registration=" 017899 ",
            field_registration="007764",
        )
    )
    ministro.refresh_from_db()

    assert ministro.full_name == "Rodrigo Exemplo"
    assert ministro.conamad_registration == "017899"
    assert ministro.field_registration == "007764"
    assert ministro.is_active is True
    assert ministro.source == MinisterSource.MANUAL


def test_criacao_audita(admin_user):
    ministro = services.create_minister(dados=dados_de_ministro(), actor=admin_user)
    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_CREATED)

    assert evento.entity_type == "Minister"
    assert evento.entity_id == str(ministro.pk)
    assert evento.actor == admin_user
    assert evento.student is None


def test_campos_obrigatorios():
    with pytest.raises(DomainError) as erro:
        services.create_minister(
            dados=dados_de_ministro(full_name=" ", conamad_registration="", ministerial_title="")
        )
    texto = str(erro.value)
    assert "nome" in texto
    assert "CONAMAD" in texto
    assert "título" in texto
    assert Minister.objects.count() == 0


def test_inscricao_com_caractere_invalido_e_recusada():
    with pytest.raises(DomainError):
        services.create_minister(dados=dados_de_ministro(conamad_registration="12<script>"))


def test_ano_de_consagracao_fora_do_intervalo():
    with pytest.raises(DomainError):
        services.create_minister(dados=dados_de_ministro(consecration_year=1800))


def test_nascimento_no_futuro_e_recusado():
    with pytest.raises(DomainError):
        services.create_minister(
            dados=dados_de_ministro(birth_date=datetime.date.today() + datetime.timedelta(days=1))
        )


# ---------------------------------------------------------------------------
# Unicidade
# ---------------------------------------------------------------------------


def test_inscricao_conamad_e_unica_no_servico(ministro):
    with pytest.raises(DomainError):
        services.create_minister(
            dados=dados_de_ministro(full_name="Outro", field_registration="")
        )


def test_inscricao_conamad_e_unica_no_banco(ministro):
    with pytest.raises(IntegrityError), transaction.atomic():
        Minister.objects.create(
            full_name="Outro",
            conamad_registration="17899",
            ministerial_title=MinisterialTitle.EVANGELISTA,
        )


def test_matricula_do_campo_unica_so_quando_preenchida():
    services.create_minister(
        dados=dados_de_ministro(conamad_registration="1", field_registration="")
    )
    services.create_minister(
        dados=dados_de_ministro(conamad_registration="2", field_registration="")
    )
    services.create_minister(
        dados=dados_de_ministro(conamad_registration="3", field_registration="55")
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Minister.objects.create(
            full_name="X",
            conamad_registration="4",
            field_registration="55",
            ministerial_title=MinisterialTitle.PASTOR,
        )


def test_inscricao_vazia_e_barrada_pelo_banco():
    with pytest.raises(IntegrityError), transaction.atomic():
        Minister.objects.create(
            full_name="Sem inscricao", conamad_registration="", ministerial_title="PASTOR"
        )


# ---------------------------------------------------------------------------
# Ativo / inativo
# ---------------------------------------------------------------------------


def test_ministro_nao_e_excluido_fisicamente(ministro):
    with pytest.raises(ValueError):
        ministro.delete()
    assert Minister.objects.filter(pk=ministro.pk).exists()


def test_inativar_e_reativar_auditam(ministro, admin_user):
    services.deactivate_minister(ministro, actor=admin_user)
    ministro.refresh_from_db()
    assert ministro.is_active is False

    services.reactivate_minister(ministro, actor=admin_user)
    ministro.refresh_from_db()
    assert ministro.is_active is True

    eventos = list(
        AuditLog.objects.filter(entity_id=str(ministro.pk))
        .order_by("id")
        .values_list("event", flat=True)
    )
    assert AuditEvent.MINISTER_BLOCKED in eventos
    assert AuditEvent.MINISTER_UNBLOCKED in eventos


def test_inativar_duas_vezes_nao_duplica_evento(ministro):
    services.deactivate_minister(ministro)
    services.deactivate_minister(ministro)
    assert AuditLog.objects.filter(event=AuditEvent.MINISTER_BLOCKED).count() == 1


# ---------------------------------------------------------------------------
# Acesso
# ---------------------------------------------------------------------------


def test_senha_fica_somente_como_hash(ministro_com_acesso):
    acesso = MinisterAccount.objects.get(minister=ministro_com_acesso)

    assert acesso.password_hash != SENHA_MINISTRO
    assert SENHA_MINISTRO not in acesso.password_hash
    assert acesso.password_hash.startswith("pbkdf2_sha256$")
    assert acesso.check_password(SENHA_MINISTRO)
    assert not acesso.check_password("errada")
    assert acesso.login_identifier == "17899"


def test_senha_contendo_a_inscricao_e_recusada():
    with pytest.raises(DomainError):
        services.create_minister(dados=dados_de_ministro(), password="Senha17899x")
    assert Minister.objects.count() == 0


def test_senha_fraca_nao_cria_nem_o_ministro():
    with pytest.raises(DomainError):
        services.create_minister(dados=dados_de_ministro(), password="123")
    assert Minister.objects.count() == 0
    assert MinisterAccount.objects.count() == 0


def test_editar_inscricao_atualiza_o_login(ministro_com_acesso):
    services.update_minister(
        ministro_com_acesso, dados=dados_de_ministro(conamad_registration="18000")
    )
    acesso = MinisterAccount.objects.get(minister=ministro_com_acesso)
    assert acesso.login_identifier == "18000"

    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_UPDATED)
    assert evento.metadata["changed_fields"] == ["conamad_registration"]
    assert evento.metadata["login_identifier_alterado"] is True


def test_edicao_sem_mudanca_nao_audita(ministro):
    services.update_minister(ministro, dados=dados_de_ministro())
    assert not AuditLog.objects.filter(event=AuditEvent.MINISTER_UPDATED).exists()


def test_criar_acesso_duas_vezes_e_recusado(ministro_com_acesso):
    with pytest.raises(DomainError):
        services.create_account(ministro_com_acesso, password="Outra#Senha2026")


def test_acesso_bloqueado_e_ministro_ativo_sao_independentes(ministro_com_acesso):
    services.block_account(ministro_com_acesso)
    ministro_com_acesso.refresh_from_db()
    acesso = MinisterAccount.objects.get(minister=ministro_com_acesso)

    assert ministro_com_acesso.is_active is True
    assert acesso.is_active is False
    assert acesso.pode_entrar is False
