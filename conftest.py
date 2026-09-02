"""Fixtures compartilhadas pela suite."""

import pytest

from accounts.models import User, UserRole

# Senhas usadas nos testes. Precisam passar pelos validadores do Django:
# ao menos 8 caracteres, nao puramente numericas, nao comuns e sem
# semelhanca com o e-mail ou o nome do usuario.
SENHA_VALIDA = "Prova#Segura2026"
SENHA_NOVA = "Trocada#Segura2027"

# Senha inicial dos alunos durante os testes. Nunca deve aparecer em
# template, log ou trilha de auditoria; varios testes verificam exatamente
# isso, entao o valor precisa ser distinto e reconhecivel.
SENHA_PADRAO_ALUNO = "Inicial#Aluno2026"


@pytest.fixture
def senha():
    return SENHA_VALIDA


@pytest.fixture
def senha_nova():
    return SENHA_NOVA


@pytest.fixture
def senha_padrao():
    return SENHA_PADRAO_ALUNO


@pytest.fixture(autouse=True)
def default_student_password(settings):
    """
    Deixa a senha inicial padrao configurada em toda a suite.

    E o estado normal de operacao do sistema. Os testes que precisam do
    cenario oposto sobrescrevem settings.DEFAULT_STUDENT_PASSWORD com string
    vazia; a fixture settings do pytest-django restaura o valor no fim de
    cada teste.
    """
    settings.DEFAULT_STUDENT_PASSWORD = SENHA_PADRAO_ALUNO
    return SENHA_PADRAO_ALUNO


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        email="coordenacao@exemplo.test",
        full_name="Carla Coordenadora",
        password=SENHA_VALIDA,
        role=UserRole.ADMIN,
    )


def _aluno_pronto_para_login(full_name, email):
    """
    Aluno com perfil e senha conhecida, pronto para autenticar.

    Passa pelo servico de criacao, para que o StudentProfile exista como na
    aplicacao real, e depois troca a senha e baixa a flag de troca
    obrigatoria. Sem isso os testes de autenticacao da Etapa 1, que usam
    SENHA_VALIDA e esperam entrar direto no painel, deixariam de valer.
    """
    from students.services import create_student

    user = create_student(full_name=full_name, email=email)
    user.set_password(SENHA_VALIDA)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])
    return user


@pytest.fixture
def student_user(db):
    return _aluno_pronto_para_login("Joao da Silva", "joao.aluno@exemplo.test")


@pytest.fixture
def outro_student(db):
    return _aluno_pronto_para_login("Maria Oliveira", "maria.aluna@exemplo.test")


@pytest.fixture
def admin_com_troca_pendente(db):
    """
    Administrador obrigado a trocar a senha no proximo acesso.

    A partir da Etapa 5 a flag must_change_password vale SOMENTE para ADMIN: o
    aluno nao troca a propria senha, entao para ele a flag deixou de comandar
    o fluxo. Esta fixture existe para que os testes do middleware continuem
    exercitando a regra no papel onde ela ainda se aplica.
    """
    return User.objects.create_user(
        email="admin.pendente@exemplo.test",
        full_name="Carlos Pendente",
        password=SENHA_VALIDA,
        role=UserRole.ADMIN,
        is_staff=True,
        must_change_password=True,
    )


@pytest.fixture
def student_com_troca_pendente(db):
    return User.objects.create_user(
        email="maria.pendente@exemplo.test",
        full_name="Maria Souza",
        password=SENHA_VALIDA,
        role=UserRole.STUDENT,
        must_change_password=True,
    )


# ---------------------------------------------------------------------------
# Clientes ja autenticados
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client_logado(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def student_client_logado(client, student_user):
    client.force_login(student_user)
    return client


# ---------------------------------------------------------------------------
# Modulos e matriculas
# ---------------------------------------------------------------------------


# Dados que a Etapa 8 passou a exigir para emitir certificado. Ficam nas
# fixtures base porque um modulo que aplica prova e emite documento tem essas
# informacoes na vida real; os testes da recusa por dado faltante montam o
# modulo incompleto por conta propria.
CERTIFICADO_DO_MODULO = {
    "certificate_display_name": "Modulo I - Cooperadores e Diaconos",
    "certificate_course_dates_text": "10 e 17 de outubro de 2026",
    "certificate_location": "Igreja Sede",
    "certificate_workload_hours": 8,
    "certificate_year": 2026,
}


@pytest.fixture
def modulo(db):
    from courses.models import Module

    return Module.objects.create(
        name="Modulo 1",
        code="MOD1",
        description="Primeiro modulo",
        order=1,
        **CERTIFICADO_DO_MODULO,
    )


@pytest.fixture
def outro_modulo(db):
    from courses.models import Module

    return Module.objects.create(
        name="Modulo 2",
        code="MOD2",
        order=2,
        **{**CERTIFICADO_DO_MODULO,
           "certificate_display_name": "Modulo II - Presbiteros"},
    )


@pytest.fixture
def modulo_sem_dados_de_certificado(db):
    """
    Modulo como os criados antes da Etapa 8: sem data, local, carga nem ano.

    E o estado real de qualquer modulo que ja existia quando os campos foram
    adicionados — a migration nao inventou valores para eles.
    """
    from courses.models import Module

    return Module.objects.create(name="Modulo Antigo", code="MOD8", order=8)


@pytest.fixture
def modulo_inativo(db):
    from courses.models import Module

    return Module.objects.create(
        name="Modulo Arquivado", code="MOD9", order=9, is_active=False
    )


@pytest.fixture
def matricula(db, student_user, modulo):
    from courses.services import create_enrollment

    return create_enrollment(student=student_user, module=modulo)


# ---------------------------------------------------------------------------
# Provas (Etapa 3)
# ---------------------------------------------------------------------------

# Pontuacoes escolhidas para somar exatamente 10.00 em Decimal. Servem para
# provar que a soma nao passa por float em nenhum ponto: 2.50 + 3.25 + 1.25 +
# 1.50 + 1.50 em binario daria 9.999999999999998.
PONTOS_DAS_QUESTOES = ("2.50", "3.25", "1.25", "1.50", "1.50")
TOTAL_ESPERADO = "10.00"


@pytest.fixture
def janela(db):
    """Abertura e encerramento validos, no futuro."""
    from datetime import timedelta

    from django.utils import timezone

    agora = timezone.now()
    return agora + timedelta(days=1), agora + timedelta(days=2)


@pytest.fixture
def prova(db, modulo, admin_user, janela):
    """Prova em rascunho, configurada e sem questoes."""
    from decimal import Decimal

    from exams.services import create_exam

    abertura, encerramento = janela
    return create_exam(
        module=modulo,
        title="Avaliacao Modulo 1",
        description="",
        instructions="Leia com atencao.",
        open_at=abertura,
        close_at=encerramento,
        duration_minutes=60,
        passing_score=Decimal("8.00"),
        max_attempts=1,
        failure_message="",
        randomize_questions=False,
        randomize_options=False,
        show_score_after_submission=True,
        actor=admin_user,
    )


@pytest.fixture
def prova_pronta(db, prova, admin_user):
    """
    Rascunho com uma questao de cada tipo, valida para publicacao.

    E a prova usada na maioria dos testes de publicacao, duplicacao e
    vazamento, porque exercita os cinco tipos de uma vez.
    """
    from exams.models import QuestionType
    from exams.services import create_question

    create_question(
        prova,
        type=QuestionType.SINGLE_CHOICE,
        text="Qual e a capital do Brasil?",
        points=PONTOS_DAS_QUESTOES[0],
        order=1,
        opcoes=[
            {"text": "Brasilia", "is_correct": True},
            {"text": "Rio de Janeiro", "is_correct": False},
            {"text": "Salvador", "is_correct": False},
        ],
        internal_explanation="Referencia: apostila 1, pagina 12.",
        actor=admin_user,
    )
    create_question(
        prova,
        type=QuestionType.MULTIPLE_CHOICE,
        text="Quais destes sao numeros primos?",
        points=PONTOS_DAS_QUESTOES[1],
        order=2,
        opcoes=[
            {"text": "2", "is_correct": True},
            {"text": "3", "is_correct": True},
            {"text": "4", "is_correct": False},
        ],
        actor=admin_user,
    )
    create_question(
        prova,
        type=QuestionType.TRUE_FALSE,
        text="A Terra e plana.",
        points=PONTOS_DAS_QUESTOES[2],
        order=3,
        resposta_verdadeira=False,
        actor=admin_user,
    )
    create_question(
        prova,
        type=QuestionType.SHORT_TEXT,
        text="Cite um bioma brasileiro.",
        points=PONTOS_DAS_QUESTOES[3],
        order=4,
        actor=admin_user,
    )
    create_question(
        prova,
        type=QuestionType.ESSAY,
        text="Disserte sobre a importancia do tema.",
        points=PONTOS_DAS_QUESTOES[4],
        order=5,
        internal_explanation="Avaliar coesao e uso dos conceitos.",
        actor=admin_user,
    )
    return prova


@pytest.fixture
def prova_publicada(db, prova_pronta, admin_user):
    from exams.services import publish_exam

    return publish_exam(prova_pronta, actor=admin_user)


# ---------------------------------------------------------------------------
# Tentativas (Etapa 4)
# ---------------------------------------------------------------------------

# A fixture `janela` coloca a prova no futuro, o que serve para publicacao mas
# nao para realizacao: uma prova que ainda nao abriu nao pode ser iniciada. As
# fixtures abaixo montam a mesma prova com a janela aberta agora.


@pytest.fixture
def janela_aberta(db):
    """Janela que engloba o instante atual, com folga dos dois lados."""
    from datetime import timedelta

    from django.utils import timezone

    agora = timezone.now()
    return agora - timedelta(hours=1), agora + timedelta(hours=3)


@pytest.fixture
def prova_aberta(db, prova_pronta, admin_user, janela_aberta):
    """
    Prova publicada, com janela aberta e um aluno matriculado.

    E a fixture base de quase todo teste de tentativa. A janela e movida antes
    da publicacao porque a prova publicada e imutavel — mexer depois exigiria
    escrever direto na tabela e contornar exatamente a regra que a Etapa 3
    existe para garantir.
    """
    from exams.services import publish_exam, update_exam

    abertura, encerramento = janela_aberta
    update_exam(
        prova_pronta,
        module=prova_pronta.module,
        title=prova_pronta.title,
        description=prova_pronta.description,
        instructions=prova_pronta.instructions,
        open_at=abertura,
        close_at=encerramento,
        duration_minutes=60,
        passing_score=prova_pronta.passing_score,
        max_attempts=1,
        failure_message=prova_pronta.failure_message,
        randomize_questions=False,
        randomize_options=False,
        show_score_after_submission=True,
        actor=admin_user,
    )
    prova_pronta.refresh_from_db()
    return publish_exam(prova_pronta, actor=admin_user)


@pytest.fixture
def aluno_matriculado(db, student_user, matricula):
    """Aluno com matricula liberada no modulo da prova."""
    return student_user


@pytest.fixture
def tentativa(db, prova_aberta, aluno_matriculado):
    """Tentativa em andamento, com questoes e alternativas ja montadas."""
    from exams.services import start_attempt

    return start_attempt(aluno_matriculado, prova_aberta)


@pytest.fixture
def tokens(db, tentativa):
    """
    Mapa {tipo de questao: (token da questao, [tokens das alternativas])}.

    Evita que cada teste precise redescobrir os tokens pelo tipo. Os tokens
    sao o unico jeito de escrever numa tentativa, entao praticamente todo
    teste de autosave comeca aqui.
    """
    from exams.models import AttemptOption, AttemptQuestion

    mapa = {}
    for linha in (
        AttemptQuestion.objects.filter(attempt=tentativa)
        .select_related("question")
        .order_by("display_order")
    ):
        alternativas = list(
            AttemptOption.objects.filter(attempt_question=linha).order_by(
                "display_order"
            )
        )
        mapa[linha.question.type] = (
            str(linha.public_token),
            [str(alternativa.public_token) for alternativa in alternativas],
        )
    return mapa


# ---------------------------------------------------------------------------
# Modelos de certificado (Etapa 10)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def media_temporaria(tmp_path, settings):
    """
    MEDIA_ROOT proprio por teste.

    Autouse porque nenhum teste deve escrever no diretorio de media do
    repositorio. Sem isto, o primeiro teste de upload deixaria arquivos em
    media/ — versionados por engano ou acumulando em silencio.

    Nao depende de `db`: e barato e nao forca a criacao do banco em testes
    que nao usam banco.
    """
    settings.MEDIA_ROOT = str(tmp_path / "media")


def png_de_teste(largura=1200, altura=850, cor=(250, 248, 240)):
    """
    Bytes de um PNG valido, na proporcao aproximada de uma A4 paisagem.

    1200x850 nao e capricho: a validacao de upload recusa imagem pequena
    demais para impressao, e um PNG de 1x1 nao passaria. O teste precisa
    exercitar o caminho que o administrador percorre, e nao um atalho.
    """
    import io as _io

    from PIL import Image

    imagem = Image.new("RGB", (largura, altura), cor)
    buffer = _io.BytesIO()
    imagem.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def arte_de_fundo():
    """Upload de arte pronto para o formulario."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(
        "arte-oficial.png", png_de_teste(), content_type="image/png"
    )


@pytest.fixture
def modelo_de_certificado(db, admin_user, arte_de_fundo):
    """
    Modelo padrao ativo, com arte e campos, pronto para emitir.

    A partir da Etapa 10 a emissao exige um modelo: sem ele o sistema recusa,
    de proposito. Este fixture e o equivalente a "a instituicao ja configurou
    o certificado" — a precondicao de qualquer teste que emita documento.
    """
    from certificates.models import FieldType
    from certificates.services_templates import (
        activate_template,
        create_template,
        save_fields,
        set_background,
    )

    template = create_template(
        name="Modelo de teste", is_global=True, actor=admin_user
    )
    set_background(template, arte_de_fundo, actor=admin_user)

    def caixa(y, **extras):
        base = {
            "x": 10,
            "y": y,
            "width": 80,
            "height": 8,
            "font_family": "Helvetica",
            "font_size": 14,
            "min_font_size": 8,
            "auto_fit": True,
            "line_height": 1.2,
            "text_align": "CENTER",
            "text_color": "#000000",
            "rotation": 0,
            "is_visible": True,
            "z_index": 10,
        }
        base.update(extras)
        return base

    save_fields(
        template,
        {
            FieldType.STUDENT_NAME: caixa(30, font_size=24),
            FieldType.COURSE_NAME: caixa(45),
            FieldType.MODULE_NAME: caixa(52),
            FieldType.COURSE_DATES: caixa(60),
            FieldType.COURSE_LOCATION: caixa(66),
            FieldType.WORKLOAD: caixa(72),
            FieldType.YEAR: caixa(20, x=88, width=8, height=25, rotation=90),
            FieldType.ISSUED_AT: caixa(78),
            FieldType.INSTITUTION: caixa(12),
            FieldType.SIGNATORY_NAME: caixa(84),
            FieldType.SIGNATORY_TITLE: caixa(88, font_size=9),
            FieldType.VERIFICATION_CODE: caixa(94, font_size=7, min_font_size=6),
            FieldType.QR_CODE: caixa(78, x=78, width=14, height=20),
        },
        actor=admin_user,
    )

    template, _ = activate_template(template, actor=admin_user)
    return template
