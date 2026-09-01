"""
O modelo oficial: dados dinamicos, snapshots e desenho.

O limite deste arquivo, dito com todas as letras
------------------------------------------------
Nao existe leitor de QR nem extrator de texto de PDF nesta suite, e nao vou
adicionar um so para testar. O que da para verificar sem eles:

    o arquivo e um PDF valido, de uma pagina, nao vazio
    o desenho escolhido corresponde a versao gravada no certificado
    os textos vem dos snapshots, e nao dos dados vivos
    a URL que ALIMENTA o QR aponta para o endereco publico correto
    nome, modulo, datas e local longos nao quebram a geracao

Se a moldura ficou bonita, se o ano esta na altura certa, se o dourado combina
com o modelo institucional — isso e inspecao visual, e esta fora do alcance de
teste automatizado. A conferencia final continua sendo humana.
"""

import uuid

import pytest
from django.utils import timezone

from certificates.models import VERSAO_ATUAL_DO_MODELO, Certificate
from certificates.pdf import _texto_de_conclusao, render_certificate_pdf
from certificates.services import issue_certificate

pytestmark = pytest.mark.django_db


def paginas(dados):
    """/Type /Pages e o no da arvore; /Type /Page sao as folhas."""
    return dados.count(b"/Type /Page") - dados.count(b"/Type /Pages")


# ---------------------------------------------------------------------------
# Os dados sao do sistema, nunca do exemplo
# ---------------------------------------------------------------------------


def test_a_emissao_copia_os_dados_do_modulo(certificado, modulo):
    assert certificado.module_display_name_snapshot == modulo.certificate_display_name
    assert certificado.course_dates_snapshot == modulo.certificate_course_dates_text
    assert certificado.course_location_snapshot == modulo.certificate_location
    assert certificado.workload_hours_snapshot == modulo.certificate_workload_hours
    assert certificado.certificate_year_snapshot == modulo.certificate_year


def test_a_emissao_copia_curso_e_signatario_da_configuracao(certificado, settings):
    assert certificado.course_name_snapshot == settings.CERTIFICATE_COURSE_NAME
    assert certificado.signatory_name_snapshot == settings.CERTIFICATE_SIGNATORY_NAME
    assert certificado.signatory_title_snapshot == settings.CERTIFICATE_SIGNATORY_TITLE


def test_certificado_novo_nasce_na_versao_atual(certificado):
    assert certificado.template_version == VERSAO_ATUAL_DO_MODELO
    assert VERSAO_ATUAL_DO_MODELO == 2


def test_mudar_o_modulo_depois_nao_altera_o_certificado(certificado, modulo):
    """
    A razao de existirem snapshots.

    A secretaria corrige a data do modulo no ano seguinte. O documento ja
    assinado nao pode mudar junto: ele afirma o que aconteceu, e nao o que a
    tabela diz hoje.
    """
    antes = {
        "datas": certificado.course_dates_snapshot,
        "local": certificado.course_location_snapshot,
        "horas": certificado.workload_hours_snapshot,
        "ano": certificado.certificate_year_snapshot,
        "modulo": certificado.module_display_name_snapshot,
    }

    modulo.certificate_course_dates_text = "outra data completamente diferente"
    modulo.certificate_location = "Outro Templo"
    modulo.certificate_workload_hours = 99
    modulo.certificate_year = 2099
    modulo.certificate_display_name = "Modulo Renomeado"
    modulo.save()

    certificado.refresh_from_db()
    assert certificado.course_dates_snapshot == antes["datas"]
    assert certificado.course_location_snapshot == antes["local"]
    assert certificado.workload_hours_snapshot == antes["horas"]
    assert certificado.certificate_year_snapshot == antes["ano"]
    assert certificado.module_display_name_snapshot == antes["modulo"]


def test_o_texto_impresso_usa_os_snapshots(certificado):
    texto = _texto_de_conclusao(certificado)

    assert certificado.course_name_snapshot in texto
    assert certificado.course_dates_snapshot in texto
    assert certificado.course_location_snapshot in texto
    assert "08 horas" in texto


def test_o_ano_impresso_nao_vem_do_relogio(certificado):
    """
    O §27 em forma de teste.

    Um certificado reimpresso em 2030 continua sendo de 2026. Usar
    timezone.now().year na renderizacao produziria um documento que muda de
    ano sozinho a cada virada.
    """
    import io as _io
    import pathlib as _pathlib

    import certificates.pdf as modulo_pdf

    certificado.certificate_year_snapshot = 2019
    dados = render_certificate_pdf(certificado)
    assert dados[:5] == b"%PDF-"
    assert certificado.certificate_year_snapshot != timezone.now().year

    # A guarda que realmente vale: o renderizador nao tem como consultar o
    # relogio, porque nao importa nenhuma forma de le-lo. Sem ela, o teste
    # acima passaria mesmo com o ano corrente desenhado no papel — a assercao
    # de bytes nao veria a diferenca.
    #
    # A varredura olha os IMPORTS, e nao a mencao ao nome: a docstring de
    # _ano_vertical cita timezone.now() justamente para explicar por que ele
    # nao e usado, e uma busca ingenua acusaria a explicacao como o problema.
    fonte = _io.open(
        _pathlib.Path(modulo_pdf.__file__), encoding="utf-8"
    ).read()
    for relogio in (
        "from django.utils import timezone",
        "import datetime",
        "from datetime import",
    ):
        assert relogio not in fonte, relogio

    import inspect

    assert "certificate_year_snapshot" in inspect.getsource(
        modulo_pdf._ano_vertical
    )


def test_uma_hora_sai_no_singular(certificado):
    certificado.workload_hours_snapshot = 1
    assert "carga horária de 1 hora." in _texto_de_conclusao(certificado)


# ---------------------------------------------------------------------------
# O arquivo
# ---------------------------------------------------------------------------


def test_o_modelo_oficial_gera_um_pdf_de_uma_pagina(certificado):
    dados = render_certificate_pdf(certificado)

    assert dados[:5] == b"%PDF-"
    assert dados.rstrip().endswith(b"%%EOF")
    assert paginas(dados) == 1
    assert len(dados) > 5000


def test_continua_sem_arquivo_de_fonte_no_servidor(certificado):
    """
    Somente as Type 1 padrao do formato.

    Uma fonte embutida apareceria como FontFile no PDF. Sem isso, o mesmo
    arquivo e gerado igual no Windows do desenvolvimento e na EC2, sem
    instalar nada no servidor.
    """
    dados = render_certificate_pdf(certificado)

    assert b"/FontFile" not in dados
    assert b"/Helvetica" in dados
    assert b"/Times" in dados


def test_o_qr_entra_no_arquivo(certificado):
    assert b"/Subtype /Image" in render_certificate_pdf(certificado)


@pytest.mark.parametrize(
    "nome",
    [
        "Ana Silva",
        "João Pedro de Souza",
        "Maria Aparecida dos Santos de Oliveira Montenegro",
        "Maria " + "da Conceicao " * 10,
    ],
)
def test_nomes_de_todos_os_tamanhos_cabem_em_uma_pagina(certificado, nome):
    """
    O caso que quebra na vida real.

    Nome proprio de quatro sobrenomes e comum. O nome desce em duas linhas e
    empurra a regua e o texto de conclusao junto; se as posicoes fossem fixas,
    a segunda linha escreveria por cima do divisor.
    """
    certificado.student_name_snapshot = nome

    dados = render_certificate_pdf(certificado)

    assert dados[:5] == b"%PDF-"
    assert paginas(dados) == 1


def test_modulo_datas_e_local_longos_cabem_em_uma_pagina(certificado):
    certificado.module_display_name_snapshot = (
        "Modulo III - Presbiteros, Evangelistas e Auxiliares da Obra Missionaria"
    )
    certificado.course_dates_snapshot = (
        "3, 10, 17 e 24 de outubro e 7, 14 e 21 de novembro de 2026"
    )
    certificado.course_location_snapshot = (
        "Congregacao Central do Jardim Bela Vista, Sumare"
    )
    certificado.workload_hours_snapshot = 40

    dados = render_certificate_pdf(certificado)

    assert dados[:5] == b"%PDF-"
    assert paginas(dados) == 1


def test_uma_palavra_sem_espacos_nao_estoura_a_moldura(certificado):
    """
    Corte por caractere.

    Sem ele, uma sequencia colada sem espacos continuaria numa unica linha
    ate sair pela lateral do papel — a quebra por palavra nao teria onde
    quebrar.
    """
    certificado.student_name_snapshot = "A" * 120

    dados = render_certificate_pdf(certificado)

    assert dados[:5] == b"%PDF-"
    assert paginas(dados) == 1


def test_acentuacao_nao_quebra_a_geracao(certificado):
    certificado.student_name_snapshot = "João Conceição de Assunção"
    certificado.module_display_name_snapshot = "Introdução à Educação Cristã"
    certificado.course_location_snapshot = "Congregação São José"

    assert render_certificate_pdf(certificado)[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# Escolha do desenho pela versao
# ---------------------------------------------------------------------------


def test_certificado_da_versao_1_continua_com_o_desenho_da_versao_1(certificado):
    """
    Certificados emitidos antes da Etapa 8 nao tem data, local, carga nem ano.

    Redesenha-los com o modelo oficial produziria "realizado em , em , com
    carga horaria de horas" — um documento oficial com buracos. Por isso o
    desenho e escolhido pelo numero da versao gravada.
    """
    certificado.template_version = 1
    certificado.course_dates_snapshot = ""
    certificado.course_location_snapshot = ""
    certificado.workload_hours_snapshot = None
    certificado.certificate_year_snapshot = None

    dados = render_certificate_pdf(certificado)

    assert dados[:5] == b"%PDF-"
    assert paginas(dados) == 1


def test_versao_desconhecida_cai_no_modelo_atual(certificado):
    certificado.template_version = 99

    dados = render_certificate_pdf(certificado)

    assert dados[:5] == b"%PDF-"
    assert paginas(dados) == 1


# ---------------------------------------------------------------------------
# Emissao sem os dados do modulo
# ---------------------------------------------------------------------------


def tentativa_aprovada_em(modulo, student_user, admin_user):
    """Monta uma tentativa aprovada num modulo escolhido."""
    from datetime import timedelta
    from decimal import Decimal

    from courses.services import create_enrollment
    from exams.models import AttemptResult, QuestionType
    from exams.services import (
        autosave_answer,
        create_exam,
        create_question,
        publish_exam,
        start_attempt,
        submit_attempt,
    )

    create_enrollment(student=student_user, module=modulo)
    agora = timezone.now()
    prova = create_exam(
        module=modulo,
        title="Prova do {}".format(modulo.code),
        duration_minutes=60,
        open_at=agora - timedelta(hours=1),
        close_at=agora + timedelta(hours=3),
        max_attempts=1,
        passing_score=Decimal("6.00"),
        actor=admin_user,
    )
    create_question(
        prova,
        type=QuestionType.SINGLE_CHOICE,
        text="Qual e a capital do Brasil?",
        points=Decimal("10.00"),
        order=1,
        opcoes=[
            {"text": "Brasilia", "is_correct": True},
            {"text": "Recife", "is_correct": False},
        ],
        actor=admin_user,
    )
    prova = publish_exam(prova, actor=admin_user)

    tentativa = start_attempt(student_user, prova)
    for linha in tentativa.questions.select_related("question").all():
        certas = [
            o
            for o in linha.options.select_related("option").all()
            if o.option.is_correct
        ]
        autosave_answer(
            tentativa,
            question_token=str(linha.public_token),
            option_tokens=[str(o.public_token) for o in certas],
        )
    enviada = submit_attempt(tentativa)
    assert enviada.result == AttemptResult.APPROVED
    return enviada


def test_modulo_sem_dados_nao_emite(
    modulo_sem_dados_de_certificado, student_user, admin_user
):
    """
    Recusa em vez de documento quebrado.

    Um PDF com "realizado em , em , com carga horaria de horas" so seria
    notado depois de o aluno baixar e imprimir. A recusa acontece antes, e diz
    o que preencher.
    """
    from certificates.services import DadosDoCertificadoIncompletos

    aprovada = tentativa_aprovada_em(
        modulo_sem_dados_de_certificado, student_user, admin_user
    )

    with pytest.raises(DadosDoCertificadoIncompletos):
        issue_certificate(aprovada, actor=admin_user)

    assert Certificate.objects.count() == 0


def test_a_recusa_nomeia_apenas_o_que_falta(
    modulo_sem_dados_de_certificado, student_user, admin_user
):
    from certificates.services import DadosDoCertificadoIncompletos

    modulo_sem_dados_de_certificado.certificate_course_dates_text = "10 de outubro"
    modulo_sem_dados_de_certificado.certificate_location = "Igreja Sede"
    modulo_sem_dados_de_certificado.save()

    aprovada = tentativa_aprovada_em(
        modulo_sem_dados_de_certificado, student_user, admin_user
    )

    with pytest.raises(DadosDoCertificadoIncompletos) as erro:
        issue_certificate(aprovada, actor=admin_user)

    mensagem = str(erro.value)
    assert "carga horaria" in mensagem
    assert "ano" in mensagem
    # O que ja esta preenchido nao entra na lista de pendencias.
    assert "local" not in mensagem
    assert "data(s) do curso" not in mensagem


def test_a_recusa_nao_deixa_matricula_concluida(
    modulo_sem_dados_de_certificado, student_user, admin_user
):
    """
    A transacao inteira volta atras.

    Emitir conclui a matricula e encerra o acesso. Se a validacao falhasse
    depois disso, o aluno perderia o modulo sem receber o documento.
    """
    from certificates.services import DadosDoCertificadoIncompletos
    from courses.models import Enrollment, EnrollmentStatus

    aprovada = tentativa_aprovada_em(
        modulo_sem_dados_de_certificado, student_user, admin_user
    )

    with pytest.raises(DadosDoCertificadoIncompletos):
        issue_certificate(aprovada, actor=admin_user)

    matricula = Enrollment.objects.get(
        student=student_user, module=modulo_sem_dados_de_certificado
    )
    assert matricula.status == EnrollmentStatus.ACTIVE
    assert matricula.access_enabled is True


def test_o_nome_exibido_em_branco_cai_no_nome_do_modulo(
    modulo_sem_dados_de_certificado, student_user, admin_user
):
    """
    Dos cinco campos, so o nome exibido tem substituto natural.

    Certificado com nome curto e melhor do que nenhum certificado; certificado
    sem data nao e.
    """
    modulo = modulo_sem_dados_de_certificado
    modulo.certificate_course_dates_text = "10 de outubro de 2026"
    modulo.certificate_location = "Igreja Sede"
    modulo.certificate_workload_hours = 4
    modulo.certificate_year = 2026
    modulo.save()

    aprovada = tentativa_aprovada_em(modulo, student_user, admin_user)
    certificado, criado = issue_certificate(aprovada, actor=admin_user)

    assert criado
    assert certificado.module_display_name_snapshot == modulo.name
    assert certificado.modulo_impresso == modulo.name


# ---------------------------------------------------------------------------
# Injecao
# ---------------------------------------------------------------------------


def test_marcacao_no_nome_e_tratada_como_texto(certificado):
    """
    O que o PDF faz com <script> e com %PDF.

    Nada: os dois viram glifos. O renderizador desenha strings, nunca
    interpreta conteudo — nao ha analisador de marcacao no caminho.
    """
    certificado.student_name_snapshot = "<script>alert(1)</script>"
    certificado.module_display_name_snapshot = "%PDF-1.7 /Catalog"

    dados = render_certificate_pdf(certificado)

    assert dados[:5] == b"%PDF-"
    assert paginas(dados) == 1


def test_codigo_resumido_nao_revela_o_codigo_inteiro(certificado):
    resumido = certificado.codigo_resumido
    inteiro = str(certificado.verification_code)

    assert resumido != inteiro
    assert len(resumido) < len(inteiro)
    assert inteiro.startswith(resumido.split("…")[0])
    assert inteiro.endswith(resumido.split("…")[1])


def test_o_codigo_do_certificado_e_um_uuid4_imprevisivel(certificado):
    codigo = uuid.UUID(str(certificado.verification_code))

    assert codigo.version == 4
    assert codigo.variant == uuid.RFC_4122
