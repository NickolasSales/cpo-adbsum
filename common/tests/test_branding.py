"""
Identidade da aplicacao.

Antes desta etapa o nome estava escrito a mao em 43 templates, no formato
`{{ INSTITUTION_NAME }} Provas`. Trocar a identidade exigia editar arquivo por
arquivo, e bastava esquecer um para a aplicacao aparecer com dois nomes ao
mesmo tempo — provavelmente na tela que ninguem abre com frequencia.

Agora ha um lugar so. Estes testes existem para que continue havendo um lugar
so.
"""

import io
import pathlib

import pytest
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db

RAIZ = pathlib.Path(__file__).resolve().parents[2]

NOME = "CPO AD Brás Sumaré"
SUBTITULO = (
    "Sistema de avaliação para o Curso de Preparação de Obreiros"
)


@pytest.fixture(autouse=True)
def identidade(settings):
    """
    Fixa a identidade oficial para os testes.

    O ambiente de desenvolvimento pode ter outro valor no .env, e o teste
    verifica o comportamento do sistema — nao a configuracao da maquina de
    quem o roda.
    """
    settings.APP_NAME = NOME
    settings.APP_SUBTITLE = SUBTITULO
    settings.INSTITUTION_NAME = NOME
    return settings


# ---------------------------------------------------------------------------
# Onde a identidade mora
# ---------------------------------------------------------------------------


def test_os_tres_valores_existem_em_settings():
    for chave in ("APP_NAME", "APP_SUBTITLE", "INSTITUTION_NAME"):
        assert hasattr(settings, chave), chave


def test_o_context_processor_expoe_a_identidade(client):
    from common.context_processors import institution

    contexto = institution(None)

    assert contexto["APP_NAME"] == NOME
    assert contexto["APP_SUBTITLE"] == SUBTITULO
    assert contexto["INSTITUTION_NAME"] == NOME


def test_nenhum_template_escreve_o_nome_a_mao():
    """
    Guarda contra a regressao que motivou a centralizacao.

    Se alguem colar "CPO Provas" ou reintroduzir o padrao
    "{{ INSTITUTION_NAME }} Provas", este teste falha.
    """
    ofensores = []
    for caminho in (RAIZ / "templates").rglob("*.html"):
        texto = io.open(caminho, encoding="utf-8").read()
        if "CPO Provas" in texto or "INSTITUTION_NAME }} Provas" in texto:
            ofensores.append(caminho.name)

    assert not ofensores, "identidade escrita a mao em: {}".format(ofensores)


def test_o_nome_nao_esta_fixo_no_codigo_da_aplicacao():
    """
    O valor vem do ambiente. Nenhum modulo de aplicacao pode conte-lo.

    settings.py e a excecao: e ali que fica o padrao.
    """
    ofensores = []
    for pasta in ("accounts", "audit", "certificates", "common", "courses", "exams", "students"):
        for caminho in (RAIZ / pasta).rglob("*.py"):
            if "/tests/" in caminho.as_posix() or "\\tests\\" in str(caminho):
                continue
            texto = io.open(caminho, encoding="utf-8").read()
            if NOME in texto:
                ofensores.append(str(caminho.relative_to(RAIZ)))

    assert not ofensores, "nome fixo no codigo: {}".format(ofensores)


def test_a_identidade_antiga_sumiu_do_codigo_de_aplicacao():
    """
    "CPO Provas" era o nome anterior.

    Ele sobrevivia em lugares que ninguem revisa: docstring de modulo,
    comentario de configuracao, cabecalho do Django Admin. O ultimo era o pior
    dos tres, porque e uma pagina que abre no navegador — tecnica, mas uma
    pagina.

    A varredura ignora testes (este arquivo cita o nome antigo de proposito)
    e o README, que registra a historia do projeto e precisa poder falar do
    nome que existia antes.
    """
    ofensores = []
    for pasta in (
        "accounts",
        "audit",
        "certificates",
        "common",
        "config",
        "courses",
        "exams",
        "students",
    ):
        for caminho in (RAIZ / pasta).rglob("*.py"):
            if "/tests/" in caminho.as_posix() or "\\tests\\" in str(caminho):
                continue
            if "CPO Provas" in io.open(caminho, encoding="utf-8").read():
                ofensores.append(str(caminho.relative_to(RAIZ)))

    assert not ofensores, "identidade antiga no codigo: {}".format(ofensores)


def test_o_django_admin_usa_a_identidade_atual(settings):
    """
    O Django Admin e ferramenta tecnica, mas ainda e uma pagina que abre.

    O cabecalho e o titulo vinham escritos a mao com o nome anterior — o unico
    lugar onde ninguem procuraria.
    """
    from django.contrib import admin

    assert settings.APP_NAME in admin.site.site_header
    assert admin.site.site_title == settings.APP_NAME
    assert "CPO Provas" not in admin.site.site_header


# ---------------------------------------------------------------------------
# Onde a identidade aparece
# ---------------------------------------------------------------------------


def test_o_login_mostra_nome_e_subtitulo(client):
    corpo = client.get(reverse("accounts:login")).content.decode("utf-8")

    assert NOME in corpo
    assert SUBTITULO in corpo


def test_o_subtitulo_e_um_texto_unico(client):
    """
    A quebra em duas linhas no celular e do navegador, e nao do markup.

    Escrever a quebra a mao partiria o texto semantico, e um leitor de tela
    anunciaria dois fragmentos soltos.
    """
    corpo = client.get(reverse("accounts:login")).content.decode("utf-8")

    assert "<span>{}</span>".format(SUBTITULO) in corpo


def test_o_titulo_da_aba_usa_o_nome(client):
    corpo = client.get(reverse("accounts:login")).content.decode("utf-8")

    assert "<title>Entrar - {}</title>".format(NOME) in corpo


def test_a_lateral_administrativa_mostra_o_nome(admin_client_logado):
    corpo = admin_client_logado.get(
        reverse("admin_panel:dashboard")
    ).content.decode("utf-8")

    assert NOME in corpo
    assert "Painel administrativo" in corpo


def test_o_topo_do_aluno_mostra_o_nome(student_client_logado):
    corpo = student_client_logado.get(
        reverse("student:dashboard")
    ).content.decode("utf-8")

    assert NOME in corpo
    assert "Area do aluno" in corpo


TELAS = [
    ("admin_panel:dashboard", "admin"),
    ("admin_panel:student_list", "admin"),
    ("admin_panel:admin_user_list", "admin"),
    ("admin_panel:attempt_list", "admin"),
    ("admin_panel:certificate_list", "admin"),
    ("admin_panel:audit_log_list", "admin"),
    ("student:dashboard", "aluno"),
    # A tela citada no §4 da Etapa 8 como exemplo de onde a identidade antiga
    # ainda aparecia.
    ("student:certificate_list", "aluno"),
]


@pytest.mark.parametrize("nome,quem", TELAS)
def test_nenhuma_tela_mostra_a_identidade_antiga(
    admin_client_logado, student_client_logado, nome, quem
):
    cliente = admin_client_logado if quem == "admin" else student_client_logado
    corpo = cliente.get(reverse(nome)).content.decode("utf-8")

    assert "CPO Provas" not in corpo
    assert "Sistema de avaliacao por modulos" not in corpo


# ---------------------------------------------------------------------------
# Certificados: identidade e um snapshot
# ---------------------------------------------------------------------------


def certificado_direto(student_user, codigo="BRND1"):
    """
    Certificado construido sem passar pelo fluxo de emissao.

    O assunto destes testes e de ONDE vem o texto da instituicao, e nao como a
    emissao funciona — isso tem arquivo proprio em certificates/tests.
    """
    from decimal import Decimal

    from django.utils import timezone

    from certificates.models import Certificate
    from courses.models import Module
    from exams.models import Exam, ExamAttempt, ExamStatus

    modulo = Module.objects.create(name="Modulo teste", code=codigo)
    agora = timezone.now()
    prova = Exam.objects.create(
        module=modulo,
        title="Prova teste",
        duration_minutes=30,
        open_at=agora,
        close_at=agora + timezone.timedelta(hours=2),
        passing_score=Decimal("6.00"),
        status=ExamStatus.PUBLISHED,
    )
    tentativa = ExamAttempt.objects.create(
        student=student_user,
        exam=prova,
        attempt_number=1,
        started_at=agora,
        expires_at=agora + timezone.timedelta(hours=1),
        total_points_snapshot=Decimal("10.00"),
        passing_score_snapshot=Decimal("6.00"),
    )
    return Certificate.objects.create(
        attempt=tentativa,
        student_name_snapshot="Aluno",
        module_name_snapshot=modulo.name,
        exam_title_snapshot=prova.title,
        institution_name_snapshot=settings.INSTITUTION_NAME,
    )


def test_o_certificado_novo_usa_o_nome_atual(db, student_user, settings):
    """
    INSTITUTION_NAME e separado de APP_NAME de proposito: um e a interface, o
    outro e o que vai IMPRESSO no documento.
    """
    settings.INSTITUTION_NAME = NOME

    certificado = certificado_direto(student_user)

    assert certificado.institution_name_snapshot == NOME


def test_trocar_a_identidade_nao_altera_certificado_antigo(
    db, student_user, settings
):
    """
    A regra do §7: nenhuma migration reescreve institution_name_snapshot.

    Um certificado emitido sob o nome antigo continua com o nome antigo — e
    isso e o correto, porque foi o que constava no documento entregue.
    """
    settings.INSTITUTION_NAME = NOME
    certificado = certificado_direto(student_user)
    antigo = certificado.institution_name_snapshot

    settings.INSTITUTION_NAME = "Outra Instituicao Qualquer"
    certificado.refresh_from_db()

    assert certificado.institution_name_snapshot == antigo
    assert antigo == NOME


def test_nenhuma_migration_reescreve_o_nome_da_instituicao():
    """
    Varredura direta nas migrations.

    Uma RunPython que atualizasse institution_name_snapshot reescreveria
    documentos ja emitidos. O §7 proibe, e este teste e a guarda.
    """
    ofensores = []
    for caminho in (RAIZ / "certificates" / "migrations").glob("*.py"):
        texto = io.open(caminho, encoding="utf-8").read()
        if "institution_name_snapshot" in texto and "RunPython" in texto:
            ofensores.append(caminho.name)

    assert not ofensores, "migration mexendo no snapshot: {}".format(ofensores)
