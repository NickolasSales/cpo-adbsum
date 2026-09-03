"""
Texto personalizado e suas variaveis.

O bloco CUSTOM_TEXT e o unico lugar do sistema em que o administrador escreve
uma frase que vai para um documento oficial. Isso o torna, ao mesmo tempo, o
recurso mais util do editor e a superficie mais delicada dele.

Os testes se dividem em tres perguntas:

    a frase certa sai no documento, com os dados do aluno no lugar?
    uma variavel fora da lista consegue ser gravada?
    mudar o texto depois reescreve um certificado ja emitido?
"""

import pytest

from certificates import services_templates as servicos
from certificates.models import CertificateTemplateField, FieldType
from certificates.pdf import render_certificate_pdf
from certificates.placeholders import (
    LINHAS_MAXIMAS,
    PLACEHOLDERS,
    TAMANHO_MAXIMO_DO_TEXTO,
    PlaceholderInvalido,
    aplicar,
    encontrar_invalidos,
    validar_texto,
)
from certificates.render import ajustar, render_from_snapshot
from certificates.snapshot import (
    montar_snapshot,
    valores_de_preview,
    valores_do_certificado,
)
from common.exceptions import DomainError

pytestmark = pytest.mark.django_db

FRASE = (
    "Concluiu com exito o Curso de Preparacao de Obreiros em\n"
    "{{data_conclusao}}, na {{local_curso}},\n"
    "com carga horaria de {{carga_horaria}} horas."
)


def elemento(**extras):
    base = {
        "type": FieldType.CUSTOM_TEXT,
        "x": 10,
        "y": 55,
        "width": 76,
        "height": 12,
        "font_family": "Helvetica",
        "font_weight": 400,
        "italic": False,
        "font_size": 13,
        "min_font_size": 8,
        "auto_fit": True,
        "line_height": 1.3,
        "text_align": "CENTER",
        "text_color": "#0F172A",
        "rotation": 0,
        "wrap": True,
        "is_visible": True,
        "z_index": 5,
        "content": FRASE,
    }
    base.update(extras)
    return base


@pytest.fixture
def rascunho(admin_user, arte_de_fundo):
    modelo = servicos.create_template(name="Modelo com texto", actor=admin_user)
    servicos.set_background(modelo, arte_de_fundo, actor=admin_user)
    return modelo


# ---------------------------------------------------------------------------
# O parser
# ---------------------------------------------------------------------------


def test_a_lista_de_variaveis_cobre_os_campos_de_texto():
    """
    Uma variavel para cada dado que o certificado imprime como texto.

    QR fica de fora de proposito: e imagem, e imagem nao cabe no meio de uma
    frase.
    """
    sem_variavel = {FieldType.QR_CODE, FieldType.STATIC_IMAGE, FieldType.CUSTOM_TEXT}

    assert set(PLACEHOLDERS.values()) == set(FieldType.values) - sem_variavel


@pytest.mark.parametrize(
    "texto,invalidos",
    [
        ("{{senha}}", ["{{senha}}"]),
        ("{{user.password}}", ["{{user.password}}"]),
        ("{{request}}", ["{{request}}"]),
        ("{{ settings.SECRET_KEY }}", ["{{ settings.SECRET_KEY }}"]),
        ("{{__class__}}", ["{{__class__}}"]),
        ("{{ 1 + 1 }}", ["{{ 1 + 1 }}"]),
        ("{{nome_aluno}} e {{senha}}", ["{{senha}}"]),
        ("{{nome_aluno}}", []),
        ("{{ nome_aluno }}", []),
        ("sem variavel nenhuma", []),
    ],
)
def test_encontra_o_que_nao_esta_na_lista(texto, invalidos):
    assert encontrar_invalidos(texto) == invalidos


def test_recusa_variavel_fora_da_lista_com_o_nome_dela():
    """
    A mensagem precisa dizer QUAL variavel esta errada. "Texto invalido"
    manda o administrador procurar num paragrafo inteiro.
    """
    with pytest.raises(PlaceholderInvalido) as erro:
        validar_texto("Ola {{senha}} e {{nome_aluno}}")

    assert erro.value.invalidos == ["{{senha}}"]
    assert "{{senha}}" in str(erro.value)


def test_normaliza_a_quebra_de_linha_do_windows():
    """
    Um textarea no Windows manda \\r\\n. O \\r nao desenha nada e seria
    contado como caractere pelo ajuste de fonte.
    """
    assert validar_texto("uma\r\noutra") == "uma\noutra"


def test_recusa_texto_gigante():
    with pytest.raises(ValueError):
        validar_texto("a" * (TAMANHO_MAXIMO_DO_TEXTO + 1))


def test_recusa_linhas_demais():
    with pytest.raises(ValueError):
        validar_texto("linha\n" * (LINHAS_MAXIMAS + 1))


def test_nao_executa_o_texto_como_template():
    """
    A razao de existir o parser proprio.

    Nenhum dos tres alcanca coisa alguma: nao ha motor de template por
    tras, so uma expressao regular e um dicionario fechado.
    """
    for tentativa in (
        "{{ settings.SECRET_KEY }}",
        "{% load static %}",
        "{{ nome_aluno.__class__ }}",
    ):
        # `{%...%}` nao e reconhecido como variavel e passa como texto puro;
        # os dois com chaves duplas sao recusados na validacao.
        if "{{" in tentativa:
            with pytest.raises(PlaceholderInvalido):
                validar_texto(tentativa)
        else:
            assert validar_texto(tentativa) == tentativa
            assert aplicar(tentativa, {}) == tentativa


def test_o_resultado_da_substituicao_nao_e_reprocessado():
    """
    Se o nome do aluno fosse literalmente "{{ano}}", ele sairia com as
    chaves visiveis — e nao viraria 2026. Dado do aluno e dado, nao
    marcacao.
    """
    valores = {FieldType.STUDENT_NAME: "{{ano}}", FieldType.YEAR: "2026"}

    assert aplicar("{{nome_aluno}}", valores) == "{{ano}}"


def test_variavel_sem_valor_nao_imprime_none():
    assert aplicar("carga: {{carga_horaria}}.", {}) == "carga: ."


def test_a_carga_horaria_entra_sem_a_palavra_horas():
    """
    O elemento solto imprime "08 horas"; dentro de uma frase que ja escreve
    "horas", a variavel entrega so o numero. Sem isso a frase do modelo
    oficial sairia com "horas" duas vezes.
    """
    valores = {FieldType.WORKLOAD: "08 horas"}

    assert aplicar("carga horaria de {{carga_horaria}} horas", valores) == (
        "carga horaria de 08 horas"
    )


# ---------------------------------------------------------------------------
# Gravar
# ---------------------------------------------------------------------------


def test_grava_o_bloco_com_o_texto(rascunho, admin_user):
    servicos.save_elements(rascunho, [elemento()], actor=admin_user)

    campo = CertificateTemplateField.objects.get(template=rascunho)

    assert campo.field_type == FieldType.CUSTOM_TEXT
    assert campo.content == FRASE


def test_o_servico_recusa_variavel_invalida(rascunho, admin_user):
    with pytest.raises(DomainError) as erro:
        servicos.save_elements(
            rascunho, [elemento(content="Ola {{senha}}")], actor=admin_user
        )

    assert "{{senha}}" in str(erro.value)
    assert not CertificateTemplateField.objects.filter(template=rascunho).exists()


def test_o_servico_recusa_bloco_sem_texto(rascunho, admin_user):
    with pytest.raises(DomainError):
        servicos.save_elements(rascunho, [elemento(content="   ")], actor=admin_user)


def test_varios_blocos_convivem(rascunho, admin_user):
    """
    O uso normal: um paragrafo no corpo e uma observacao no rodape.
    """
    servicos.save_elements(
        rascunho,
        [
            elemento(content="Primeiro bloco.", y=50),
            elemento(content="Segundo bloco.", y=70),
        ],
        actor=admin_user,
    )

    assert CertificateTemplateField.objects.filter(template=rascunho).count() == 2


def test_texto_proprio_nao_gruda_em_outro_tipo(rascunho, admin_user):
    """
    Um "Nome do aluno" com content preenchido seria uma instrucao que nada
    le — e um dia alguem faria o renderizador ler.
    """
    servicos.save_elements(
        rascunho,
        [elemento(type=FieldType.STUDENT_NAME, content="texto teimoso")],
        actor=admin_user,
    )

    campo = CertificateTemplateField.objects.get(template=rascunho)

    assert campo.field_type == FieldType.STUDENT_NAME
    assert campo.content == ""


# ---------------------------------------------------------------------------
# Sair no documento
# ---------------------------------------------------------------------------


def desenhadas(pdf_pronto, monkeypatch):
    from reportlab.pdfgen import canvas as modulo_canvas

    escritas = []
    for nome in ("drawString", "drawCentredString", "drawRightString"):
        original = getattr(modulo_canvas.Canvas, nome)

        def espiao(self, x, y, texto, *args, _original=original, **kwargs):
            escritas.append(texto)
            return _original(self, x, y, texto, *args, **kwargs)

        monkeypatch.setattr(modulo_canvas.Canvas, nome, espiao)

    pdf_pronto()
    return escritas


def test_o_preview_substitui_as_variaveis(rascunho, admin_user, monkeypatch):
    servicos.save_elements(rascunho, [elemento()], actor=admin_user)
    snapshot = montar_snapshot(rascunho)

    escritas = desenhadas(
        lambda: render_from_snapshot(snapshot, valores_de_preview()), monkeypatch
    )
    tudo = " ".join(escritas)

    assert "{{" not in tudo
    assert "Concluiu com exito" in tudo
    assert "02 de setembro de 2026" in tudo
    assert "Igreja Sede" in tudo


def test_o_pdf_do_certificado_substitui_as_variaveis(
    tentativa_aprovada, admin_user, modelo_de_certificado, monkeypatch
):
    """
    O documento de verdade, com os dados congelados do aluno.
    """
    from certificates.services import issue_certificate

    servicos.save_elements(
        modelo_de_certificado,
        [
            elemento(content="Aluno: {{nome_aluno}} em {{data_conclusao}}."),
            {**elemento(type=FieldType.QR_CODE, content=""), "y": 80},
        ],
        actor=admin_user,
    )
    documento, _ = issue_certificate(tentativa_aprovada, actor=admin_user)

    escritas = desenhadas(lambda: render_certificate_pdf(documento), monkeypatch)
    tudo = " ".join(escritas)

    assert "{{" not in tudo
    assert documento.student_name_snapshot in tudo


def test_as_quebras_escritas_sao_respeitadas():
    """
    Tres linhas escritas saem em tres linhas. Tratar o texto como um
    paragrafo unico juntaria frases que o administrador separou.
    """
    linhas, _ = ajustar(
        "primeira\nsegunda\nterceira",
        fonte="Helvetica",
        tamanho=12,
        minimo=8,
        auto_fit=True,
        largura=400,
        altura=80,
        entrelinha=1.2,
    )

    assert linhas == ["primeira", "segunda", "terceira"]


def test_a_quebra_automatica_age_dentro_de_cada_linha():
    linhas, _ = ajustar(
        "uma linha bem comprida que nao cabe de jeito nenhum\nsegunda",
        fonte="Helvetica",
        tamanho=12,
        minimo=12,
        auto_fit=False,
        largura=90,
        altura=200,
        entrelinha=1.2,
    )

    assert len(linhas) > 2
    assert linhas[-1] == "segunda"


def test_sem_quebra_o_texto_so_encolhe():
    """
    `wrap` desligado: nao ha quebra que resolva, so encolhimento. Serve para
    uma linha que precisa caber inteira num espaco estreito da arte.
    """
    linhas, tamanho = ajustar(
        "uma linha comprida que precisa caber inteira",
        fonte="Helvetica",
        tamanho=30,
        minimo=6,
        auto_fit=True,
        largura=140,
        altura=60,
        entrelinha=1.2,
        quebrar=False,
    )

    assert linhas == ["uma linha comprida que precisa caber inteira"]
    assert tamanho < 30


def test_o_bloco_longo_encolhe_em_vez_de_cortar():
    texto = FRASE.replace("{{data_conclusao}}", "02 de setembro de 2026")
    linhas, tamanho = ajustar(
        texto,
        fonte="Helvetica",
        tamanho=20,
        minimo=7,
        auto_fit=True,
        largura=200,
        altura=50,
        entrelinha=1.3,
    )

    assert tamanho < 20
    assert "..." not in " ".join(linhas)
    assert "…" not in " ".join(linhas)


# ---------------------------------------------------------------------------
# O certificado antigo nao muda
# ---------------------------------------------------------------------------


def test_mudar_o_texto_depois_nao_reescreve_o_documento(
    tentativa_aprovada, admin_user, modelo_de_certificado
):
    """
    A garantia inteira do versionamento, no caso do texto livre.

    O snapshot congela a FRASE, e nao o resultado dela: reeditar o modelo
    nao alcanca o documento, e a resolucao das variaveis continua
    acontecendo contra os dados congelados do proprio certificado.
    """
    from certificates.services import issue_certificate

    servicos.save_elements(
        modelo_de_certificado,
        [elemento(content="Concluiu em {{data_conclusao}}")],
        actor=admin_user,
    )
    documento, _ = issue_certificate(tentativa_aprovada, actor=admin_user)

    congelado = [
        campo["content"]
        for campo in documento.template_snapshot["fields"]
        if campo["field_type"] == FieldType.CUSTOM_TEXT
    ]

    copia = servicos.duplicate_template(modelo_de_certificado, actor=admin_user)
    servicos.save_elements(
        copia,
        [elemento(content="Concluiu com exito em {{data_conclusao}}")],
        actor=admin_user,
    )
    documento.refresh_from_db()

    assert congelado == ["Concluiu em {{data_conclusao}}"]
    assert [
        campo["content"]
        for campo in documento.template_snapshot["fields"]
        if campo["field_type"] == FieldType.CUSTOM_TEXT
    ] == congelado


def test_o_modelo_usado_nao_aceita_texto_novo(certificado, admin_user):
    modelo = certificado.certificate_template

    with pytest.raises(servicos.ModeloNaoEditavel):
        servicos.save_elements(modelo, [elemento()], actor=admin_user)


def test_duplicar_leva_o_texto(rascunho, admin_user):
    servicos.save_elements(rascunho, [elemento()], actor=admin_user)

    copia = servicos.duplicate_template(rascunho, actor=admin_user)

    assert CertificateTemplateField.objects.get(template=copia).content == FRASE


def test_o_valor_congelado_e_o_do_certificado_e_nao_o_de_hoje(certificado):
    """
    A frase congelada, resolvida contra os valores do certificado, precisa
    dar o mesmo texto sempre.
    """
    valores = valores_do_certificado(certificado)
    frase = aplicar("Aluno {{nome_aluno}}, em {{data_conclusao}}.", valores)

    assert certificado.student_name_snapshot in frase
    assert "{{" not in frase
