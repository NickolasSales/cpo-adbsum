"""
O Controle de Ministros nao toca no CPO academico.

Criar, importar, editar, bloquear e inativar ministros nao pode alterar
nenhum aluno, perfil, modulo, matricula, prova, tentativa ou certificado — e
o codigo do dominio de ministros nao pode nem importar esses modelos.
"""

import ast
import pathlib

import pytest

from accounts.models import User, UserRole
from certificates.models import Certificate
from courses.models import Enrollment, Module
from exams.models import Exam, ExamAttempt
from ministers import services
from ministers.importers import confirmar, ler_arquivo
from ministers.models import Minister, MinisterAccount
from ministers.tests.conftest import SENHA_MINISTRO, dados_de_ministro, entrar_no_portal
from ministers.tests.planilhas import planilha_relacao
from students.models import StudentProfile

pytestmark = pytest.mark.django_db

RAIZ = pathlib.Path(__file__).resolve().parents[1]
APPS_ACADEMICAS = ("students", "courses", "exams", "certificates")


def retrato_do_cpo():
    alunos = User.objects.filter(role=UserRole.STUDENT)
    return {
        "usuarios": User.objects.count(),
        "alunos": list(alunos.order_by("pk").values_list("pk", "email", "is_active", "password")),
        "perfis": list(StudentProfile.objects.order_by("pk").values_list("pk", "notes", "source")),
        "modulos": list(Module.objects.order_by("pk").values_list("pk", "is_active")),
        "matriculas": list(Enrollment.objects.order_by("pk").values_list("pk", "status")),
        "provas": list(Exam.objects.order_by("pk").values_list("pk", "status")),
        "tentativas": list(ExamAttempt.objects.order_by("pk").values_list("pk", "status")),
        "certificados": list(Certificate.objects.order_by("pk").values_list("pk", "status")),
    }


def test_operacoes_de_ministro_nao_alteram_o_cpo(tentativa, admin_user, client):
    antes = retrato_do_cpo()
    assert antes["alunos"] and antes["matriculas"] and antes["tentativas"]

    ministro = services.create_minister(
        dados=dados_de_ministro(), password=SENHA_MINISTRO, actor=admin_user
    )
    services.update_minister(
        ministro, dados=dados_de_ministro(congregation="Outra"), actor=admin_user
    )
    services.block_account(ministro, actor=admin_user)
    services.unblock_account(ministro, actor=admin_user)
    services.reset_password(ministro, new_password="Redefinida#2026x", actor=admin_user)
    entrar_no_portal(client, senha="Redefinida#2026x")
    services.deactivate_minister(ministro, actor=admin_user)
    confirmar(ler_arquivo(planilha_relacao())["linhas"], criar_acesso=True, actor=admin_user)

    assert Minister.objects.count() > 1
    assert retrato_do_cpo() == antes


def test_ministro_nao_cria_usuario_do_cpo():
    usuarios = User.objects.count()
    confirmar(ler_arquivo(planilha_relacao())["linhas"], criar_acesso=True)
    assert User.objects.count() == usuarios


def test_acesso_do_ministro_nao_aponta_para_user():
    relacionados = {
        campo.related_model
        for modelo in (Minister, MinisterAccount)
        for campo in modelo._meta.get_fields()
        if campo.is_relation and campo.related_model is not None
    }
    assert User not in relacionados


def test_codigo_de_ministros_nao_importa_apps_academicas():
    """Analise estatica: nenhum modulo de producao do app importa o CPO."""
    violacoes = []
    for arquivo in RAIZ.rglob("*.py"):
        if "tests" in arquivo.parts or "migrations" in arquivo.parts:
            continue
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if isinstance(no, ast.ImportFrom) and no.module:
                raiz = no.module.split(".")[0]
            elif isinstance(no, ast.Import):
                raiz = no.names[0].name.split(".")[0]
            else:
                continue
            if raiz in APPS_ACADEMICAS:
                violacoes.append("{}: {}".format(arquivo.name, raiz))
    assert violacoes == []


def test_migration_de_ministros_nao_depende_do_cpo():
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(None, ignore_no_migrations=True)
    for (app, _nome), migracao in loader.disk_migrations.items():
        if app != "ministers":
            continue
        for dependencia, _ in migracao.dependencies:
            assert dependencia not in APPS_ACADEMICAS


def test_mensalidades_nao_alteram_o_cpo(tentativa, admin_user, ministro):
    import datetime

    from ministers import mensalidades

    antes = retrato_do_cpo()
    m = mensalidades.create_fee(
        minister=ministro,
        reference_month="2026-10",
        due_date=datetime.date(2026, 10, 10),
        amount="35.00",
        actor=admin_user,
    )
    mensalidades.register_payment(
        m, paid_at=datetime.date(2026, 10, 1), payment_method="PIX", actor=admin_user
    )
    mensalidades.reverse_payment(m, reason="teste", actor=admin_user)
    mensalidades.cancel_fee(m, reason="teste", actor=admin_user)
    assert retrato_do_cpo() == antes


def test_mensalidade_nao_aponta_para_o_cpo():
    from ministers.models import MinisterMonthlyFee

    from ministers.models import MinisterFeeCharge

    relacionados = {
        campo.related_model
        for campo in MinisterMonthlyFee._meta.get_fields()
        if campo.is_relation and campo.related_model is not None
    }
    # Fase 2D: a cobranca (MinisterFeeCharge.fee) aparece como relacao
    # reversa. Continua tudo dentro do dominio de ministros.
    assert relacionados == {Minister, MinisterFeeCharge}


def test_cobranca_so_aponta_para_ministros_e_para_o_administrador():
    from django.contrib.auth import get_user_model

    from ministers.models import MinisterBillingSettings, MinisterFeeCharge, MinisterMonthlyFee

    for modelo, esperados in (
        (MinisterFeeCharge, {MinisterMonthlyFee, get_user_model()}),
        (MinisterBillingSettings, {get_user_model()}),
    ):
        relacionados = {
            campo.related_model
            for campo in modelo._meta.get_fields()
            if campo.is_relation and campo.related_model is not None
        }
        # O User e so o administrador que agiu (generated_by / updated_by),
        # como em MinisterFeeBatch.created_by — nunca um aluno ou dado academico.
        assert relacionados == esperados, modelo


def test_geracao_em_massa_nao_altera_o_cpo(tentativa, admin_user, ministro):
    import datetime

    from ministers import geracao

    antes = retrato_do_cpo()
    parametros = geracao.validar_parametros("2026-11", datetime.date(2026, 11, 10), "50.00")
    lote = geracao.gerar(parametros, token=geracao.novo_token(), actor=admin_user)
    assert lote.created_count == 1
    assert retrato_do_cpo() == antes


def test_portal_financeiro_nao_altera_o_cpo(tentativa, client):
    import datetime

    from ministers import mensalidades

    ministro = services.create_minister(dados=dados_de_ministro(), password=SENHA_MINISTRO)
    mensalidades.create_fee(
        minister=ministro, reference_month="2026-11", due_date=datetime.date(2026, 11, 10), amount="50.00"
    )
    antes = retrato_do_cpo()
    entrar_no_portal(client)
    assert client.get("/ministros/").status_code == 200
    assert client.get("/ministros/mensalidades/").status_code == 200
    assert retrato_do_cpo() == antes


def test_cobranca_pix_nao_altera_o_cpo(tentativa, client, admin_user):
    """Configurar o Pix, gerar cobranca, abrir a cobranca e baixar a guia."""
    import datetime

    from django.test import Client

    from ministers import cobranca, mensalidades
    from ministers.tests.conftest import PIX_DE_TESTE

    antes = retrato_do_cpo()

    cobranca.salvar_configuracao(dict(PIX_DE_TESTE), actor=admin_user)
    ministro = services.create_minister(dados=dados_de_ministro(), password=SENHA_MINISTRO)
    outubro = mensalidades.create_fee(
        minister=ministro, reference_month="2026-10", due_date=datetime.date(2026, 10, 31), amount="35.00"
    )
    mensalidades.create_fee(
        minister=ministro, reference_month="2026-11", due_date=datetime.date(2026, 11, 30), amount="35.00"
    )
    cobranca.gerar_cobranca(outubro, actor=admin_user)
    cobranca.gerar_cobrancas_pendentes(actor=admin_user)

    entrar_no_portal(client)
    for url in ("/ministros/mensalidades/2026/10/", "/ministros/mensalidades/2026/10/guia.pdf"):
        assert client.get(url).status_code == 200

    admin = Client()
    admin.force_login(admin_user)
    assert admin.get("/admin-panel/ministros/mensalidades/{}/guia.pdf".format(outubro.pk)).status_code == 200

    assert retrato_do_cpo() == antes
