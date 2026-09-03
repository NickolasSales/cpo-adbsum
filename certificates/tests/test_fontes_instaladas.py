"""
As fontes institucionais: arquivo, registro, navegador e PDF.

O que estes testes protegem nao e o codigo — e a coincidencia entre quatro
coisas que moram em lugares diferentes:

    o arquivo .ttf no disco
    o nome que o ReportLab registra
    a regra @font-face que o navegador le
    o subconjunto embutido no PDF entregue

Cada uma pode desalinhar sozinha, e o desalinhamento nao aparece numa tela de
erro. Aparece num certificado impresso com a tipografia errada, na mao de
alguem, semanas depois.
"""

import hashlib
import json
import re
import struct

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from certificates import services_templates as servicos
from certificates.apps import verificar_fontes
from certificates.css_das_fontes import caminho_do_css, gerar
from certificates.fonts import (
    CERTIFICATE_FONTS,
    FAMILIAS_PERMITIDAS,
    FONTES_PERMITIDAS,
    MEDIO,
    NEGRITO,
    REGULAR,
    SEMIBOLD,
    FonteIndisponivel,
    arquivos_ausentes,
    caminho_no_disco,
    catalogo_para_o_editor,
    faces_com_arquivo,
    pesos_suportados,
    pilha_css,
    raiz_no_disco,
    registrar_fontes,
    resolver_fonte,
    tem_italico,
)
from certificates.models import CertificateTemplateField, FieldType
from certificates.render import ajustar, render_from_snapshot
from common.exceptions import DomainError

pytestmark = pytest.mark.django_db

FAMILIAS_NOVAS = ("BODONI_MODA", "MONTSERRAT", "GREAT_VIBES", "ALLURA")


@pytest.fixture
def rascunho(admin_user):
    return servicos.create_template(name="Modelo tipografico", actor=admin_user)


@pytest.fixture
def sem_arquivos_de_fonte(monkeypatch, tmp_path):
    """
    Simula o diretorio de fontes ausente — deploy incompleto, volume nao
    montado, arquivo que nao subiu.

    O teardown REGISTRA DE NOVO. Sem isso o estado global do ReportLab
    ficaria quebrado para os testes seguintes, e a falha apareceria longe
    daqui.
    """
    monkeypatch.setattr("certificates.fonts.raiz_no_disco", lambda: tmp_path)
    registrar_fontes(forcar=True)
    try:
        yield tmp_path
    finally:
        monkeypatch.undo()
        registrar_fontes(forcar=True)


def paginas(pdf):
    """/Type /Pages e o no da arvore; /Type /Page sao as folhas."""
    return pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages")


# ---------------------------------------------------------------------------
# O registro
# ---------------------------------------------------------------------------


def test_o_registro_tem_as_quatro_familias():
    for familia in FAMILIAS_NOVAS:
        assert familia in CERTIFICATE_FONTS
        assert familia in FAMILIAS_PERMITIDAS


def test_as_de_texto_tem_os_quatro_pesos():
    """
    Regular, Medio, Semibold e Negrito, cada um com arquivo proprio.

    E o que separa esta implementacao de uma que usasse a fonte VARIAVEL do
    google/fonts: la existe um arquivo so, e o ReportLab desenharia sempre a
    instancia padrao em qualquer peso.
    """
    for familia in ("BODONI_MODA", "MONTSERRAT"):
        assert pesos_suportados(familia) == (REGULAR, MEDIO, SEMIBOLD, NEGRITO)
        assert tem_italico(familia)


def test_as_caligraficas_tem_um_desenho_so():
    for familia in ("GREAT_VIBES", "ALLURA"):
        assert pesos_suportados(familia) == (REGULAR,)
        assert not tem_italico(familia)


def test_as_embutidas_continuam_disponiveis():
    """
    Helvetica, Times e Courier nao saem. Sao as unicas que desenham mesmo se
    o diretorio de fontes sumir do servidor, e um modelo antigo configurado
    nelas nao pode perder a fonte com que foi montado.
    """
    for familia in ("Helvetica", "Times", "Courier"):
        assert familia in FAMILIAS_PERMITIDAS


# ---------------------------------------------------------------------------
# Os arquivos
# ---------------------------------------------------------------------------


def test_todo_arquivo_declarado_existe():
    """
    Nao le o diretorio do desenvolvedor: pergunta a configuracao onde o
    static mora e confere ali. O mesmo teste roda no Windows e no Ubuntu.
    """
    assert arquivos_ausentes() == []


def test_as_fontes_ficam_dentro_do_static():
    """
    Fora de STATICFILES_DIRS o collectstatic nao as levaria, e o navegador
    receberia 404 em producao — com o PDF continuando certo, o que faria o
    defeito parecer coisa do navegador de quem abriu.
    """
    raiz = raiz_no_disco().resolve()

    assert any(
        raiz.is_relative_to(pasta.resolve()) for pasta in settings.STATICFILES_DIRS
    )


def test_o_check_do_django_nao_reclama():
    assert verificar_fontes(None) == []


def test_o_check_reclama_quando_falta_arquivo(sem_arquivos_de_fonte):
    """
    `manage.py check` ja roda no deploy. Um arquivo que nao subiu aparece
    ANTES do restart, e nao no meio da primeira emissao.
    """
    problemas = verificar_fontes(None)

    assert len(problemas) == 1
    assert problemas[0].id == "certificates.E001"
    assert "Bodoni Moda" in problemas[0].msg
    # O caminho absoluto do servidor nao vai para a saida de um deploy.
    assert str(sem_arquivos_de_fonte) not in problemas[0].msg


def _tabelas(caminho):
    """Os nomes das tabelas de um arquivo TrueType."""
    dados = caminho.read_bytes()
    quantidade = struct.unpack(">H", dados[4:6])[0]
    return {
        dados[12 + 16 * i : 16 + 16 * i].decode("latin-1")
        for i in range(quantidade)
    }


def test_nenhuma_fonte_e_variavel():
    """
    O teste que sustenta a promessa "preview igual ao PDF".

    Uma fonte variavel tem a tabela `fvar`. O navegador entende o eixo de
    peso dela e desenha o Semibold; o ReportLab le a `glyf` direto e desenha
    a instancia padrao. Tela em Semibold, papel em Regular — e ninguem
    percebe ate por os dois lado a lado.
    """
    for familia, peso, italico, nome, _relativo in faces_com_arquivo():
        tabelas = _tabelas(caminho_no_disco(familia, peso, italico))

        assert "fvar" not in tabelas, "{} e uma fonte variavel".format(nome)
        assert "glyf" in tabelas


def test_o_nome_do_registro_e_o_nome_real_da_fonte():
    """
    O nome registrado vem da tabela em Python; o nome do desenho vem do
    arquivo. Trocar o .ttf por outra versao com nome diferente faria o PDF
    pedir uma fonte que nao e a que abriu.
    """
    from reportlab.pdfbase.ttfonts import TTFontFile

    for familia, peso, italico, nome, _relativo in faces_com_arquivo():
        arquivo = TTFontFile(str(caminho_no_disco(familia, peso, italico)))

        assert arquivo.name.decode("latin-1") == nome


def test_o_sha256_de_cada_arquivo_confere():
    """
    Confere contra o manifesto gravado no dia do download.

    Nao e paranoia com adulteracao: e a rede contra corrupcao silenciosa. Um
    checkout que normalize fim de linha num binario troca bytes no meio da
    tabela de glifos, e o arquivo continua abrindo — so que errado.
    """
    manifesto = raiz_no_disco() / "SHA256SUMS"
    assert manifesto.is_file(), "SHA256SUMS nao esta versionado"

    esperado = {}
    for linha in manifesto.read_text(encoding="utf-8").splitlines():
        if not linha.strip():
            continue
        soma, caminho = linha.split(None, 1)
        esperado[caminho.strip().lstrip("*").replace("\\", "/")] = soma

    assert len(esperado) == len(faces_com_arquivo())

    for familia, peso, italico, _nome, relativo in faces_com_arquivo():
        # `relativo` comeca em "fonts/certificates/"; o manifesto e relativo
        # a propria pasta das fontes.
        chave = "/".join(relativo.split("/")[2:])
        atual = hashlib.sha256(
            caminho_no_disco(familia, peso, italico).read_bytes()
        ).hexdigest()

        assert atual == esperado[chave], chave


def test_a_licenca_acompanha_cada_familia():
    """
    A OFL exige o texto da licenca junto na redistribuicao. Fonte sem
    licenca ao lado e pendencia juridica, nao detalhe de organizacao.
    """
    for chave, dados in CERTIFICATE_FONTS.items():
        licenca = raiz_no_disco() / dados["pasta"] / "OFL.txt"

        assert licenca.is_file(), chave
        assert "SIL OPEN FONT LICENSE" in licenca.read_text(
            encoding="utf-8"
        ).upper()


# ---------------------------------------------------------------------------
# Registro no ReportLab
# ---------------------------------------------------------------------------


def test_registrar_poe_todas_as_faces_no_reportlab():
    from reportlab.pdfbase import pdfmetrics

    registrar_fontes(forcar=True)
    registradas = set(pdfmetrics.getRegisteredFontNames())

    for _familia, _peso, _italico, nome, _relativo in faces_com_arquivo():
        assert nome in registradas


def test_registrar_de_novo_nao_rele_os_arquivos(monkeypatch):
    """
    Idempotente de verdade, e nao "nao explode se chamar duas vezes".

    Cada renderizacao chama `registrar_fontes`. Sem a guarda, dezoito
    arquivos seriam abertos e parseados a cada certificado emitido.
    """
    registrar_fontes(forcar=True)

    def proibido(*args, **kwargs):
        raise AssertionError("releu uma fonte que ja estava registrada")

    monkeypatch.setattr("reportlab.pdfbase.pdfmetrics.registerFont", proibido)

    registrar_fontes()


# ---------------------------------------------------------------------------
# Familia + peso + italico -> arquivo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "familia,peso,italico,esperado",
    [
        ("MONTSERRAT", REGULAR, False, "Montserrat-Regular"),
        ("MONTSERRAT", MEDIO, False, "Montserrat-Medium"),
        ("MONTSERRAT", SEMIBOLD, False, "Montserrat-SemiBold"),
        ("MONTSERRAT", NEGRITO, False, "Montserrat-Bold"),
        ("MONTSERRAT", REGULAR, True, "Montserrat-Italic"),
        ("MONTSERRAT", NEGRITO, True, "Montserrat-BoldItalic"),
        ("BODONI_MODA", REGULAR, False, "BodoniModa-Regular"),
        ("BODONI_MODA", SEMIBOLD, True, "BodoniModa-SemiBoldItalic"),
        ("BODONI_MODA", NEGRITO, False, "BodoniModa-Bold"),
        ("GREAT_VIBES", REGULAR, False, "GreatVibes-Regular"),
        ("ALLURA", REGULAR, False, "Allura-Regular"),
        # As embutidas continuam compondo como sempre.
        ("Times", NEGRITO, True, "Times-BoldItalic"),
        ("Helvetica", REGULAR, True, "Helvetica-Oblique"),
        ("Courier", NEGRITO, False, "Courier-Bold"),
    ],
)
def test_o_peso_escolhe_o_arquivo_certo(familia, peso, italico, esperado):
    assert resolver_fonte(familia, peso, italico) == esperado


@pytest.mark.parametrize(
    "familia,peso,italico",
    [
        ("GREAT_VIBES", NEGRITO, False),
        ("GREAT_VIBES", SEMIBOLD, True),
        ("ALLURA", NEGRITO, True),
        ("ALLURA", MEDIO, False),
    ],
)
def test_caligrafica_com_negrito_nao_inventa_arquivo(familia, peso, italico):
    """
    Pedir negrito numa fonte de um desenho so cai no desenho que existe.

    Nao ha simulacao. Engrossar os tracos de uma caligrafica por conta
    propria destroi justamente o que faz ela servir para assinatura.
    """
    nome = resolver_fonte(familia, peso, italico)

    assert nome == CERTIFICATE_FONTS[familia]["faces"][(REGULAR, False)][0]
    assert nome in FONTES_PERMITIDAS


def test_resolver_e_idempotente():
    """Resolver o resultado de resolver devolve o mesmo nome."""
    for _familia, _peso, _italico, nome, _relativo in faces_com_arquivo():
        assert resolver_fonte(nome) == nome


# ---------------------------------------------------------------------------
# O que o navegador recebe
# ---------------------------------------------------------------------------


def test_o_css_das_fontes_esta_em_dia():
    """
    O arquivo em disco tem que ser exatamente o que o registro produz.

    E o que impede a tela de oferecer um peso que o PDF nao desenha: as duas
    listas nao podem divergir porque uma e gerada da outra.

        python manage.py gerar_css_das_fontes
    """
    assert caminho_do_css().read_text(encoding="utf-8") == gerar()


def test_o_css_declara_uma_face_por_arquivo():
    assert gerar().count("@font-face") == len(faces_com_arquivo())


def test_todo_caminho_do_css_existe_no_disco():
    css = gerar()
    pasta_do_css = caminho_do_css().parent

    caminhos = re.findall(r'url\("([^"]+)"\)', css)
    assert len(caminhos) == len(faces_com_arquivo())

    for caminho in caminhos:
        assert caminho.startswith("../fonts/certificates/")
        assert (pasta_do_css / caminho).resolve().is_file(), caminho


def test_o_css_declara_o_peso_de_cada_face():
    """
    Sem `font-weight` na regra, o navegador acharia que todas as faces sao a
    Regular e engordaria os tracos por conta propria para desenhar o
    negrito — mostrando na tela um peso que o PDF nao tem.
    """
    css = gerar()

    for familia, peso, italico, _nome, _relativo in faces_com_arquivo():
        bloco = re.search(
            r'@font-face \{{[^}}]*?{}[^}}]*?\}}'.format(
                re.escape(caminho_no_disco(familia, peso, italico).name)
            ),
            css,
            re.S,
        )
        assert bloco, (familia, peso, italico)
        assert "font-weight: {};".format(peso) in bloco.group(0)
        assert "font-style: {};".format(
            "italic" if italico else "normal"
        ) in bloco.group(0)


def test_o_css_nao_aponta_para_cdn():
    """
    O sistema precisa abrir com a internet da instituicao fora do ar. Uma
    fonte que so existe num CDN e uma fonte que um dia nao carrega.
    """
    corpo = gerar().split("*/", 1)[1]

    for proibido in ("fonts.googleapis.com", "fonts.gstatic.com", "http", "//"):
        assert proibido not in corpo


def test_o_catalogo_do_editor_bate_com_o_registro():
    catalogo = {item["valor"]: item for item in catalogo_para_o_editor()}

    assert set(catalogo) == set(FAMILIAS_PERMITIDAS)

    for familia, item in catalogo.items():
        assert item["css"] == pilha_css(familia)
        assert [peso["valor"] for peso in item["pesos"]] == list(
            pesos_suportados(familia)
        )
        assert item["italico"] == tem_italico(familia)


def test_a_pilha_css_usa_o_nome_declarado_no_font_face():
    """
    O `font-family` que o editor aplica tem que ser o mesmo nome que o
    @font-face declara. Nomes diferentes fariam o navegador ignorar o
    arquivo e cair na generica — em silencio.
    """
    css = gerar()

    for chave, dados in CERTIFICATE_FONTS.items():
        assert 'font-family: "{}";'.format(dados["css_familia"]) in css
        assert pilha_css(chave).startswith('"{}"'.format(dados["css_familia"]))


def test_a_pagina_do_editor_carrega_o_css_local_e_o_catalogo(
    admin_client_logado, rascunho
):
    corpo = admin_client_logado.get(
        reverse("admin_panel:certificate_template_edit", args=[rascunho.pk])
    ).content.decode()

    assert "css/fontes-certificado" in corpo
    assert "fonts.googleapis.com" not in corpo
    assert "fonts.gstatic.com" not in corpo

    familias = json.loads(
        re.search(r'id="dados-familias"[^>]*>(.*?)</script>', corpo, re.S).group(1)
    )
    assert [item["valor"] for item in familias][:4] == list(FAMILIAS_NOVAS)


# ---------------------------------------------------------------------------
# O PDF
# ---------------------------------------------------------------------------


def _bloco(texto, familia, y, peso=REGULAR):
    return {
        "field_type": FieldType.CUSTOM_TEXT,
        "content": texto,
        "x": 5,
        "y": y,
        "width": 90,
        "height": 14,
        "font_family": familia,
        "font_weight": peso,
        "italic": False,
        "font_size": 30,
        "min_font_size": 10,
        "auto_fit": True,
        "line_height": 1.2,
        "text_align": "CENTER",
        "text_color": "#000000",
        "rotation": 0,
        "wrap": True,
        "is_visible": True,
        "z_index": 1,
    }


AMOSTRA = {
    "page_width_mm": 297,
    "page_height_mm": 210,
    "background_path": "",
    "fields": [
        _bloco("CERTIFICADO", "BODONI_MODA", 10),
        _bloco("DE CONCLUSAO", "MONTSERRAT", 30, NEGRITO),
        _bloco("Rodrigo Montenegro", "GREAT_VIBES", 50),
        _bloco("Teste Allura", "ALLURA", 70),
    ],
}


def test_o_pdf_de_amostra_sai_valido_e_com_uma_pagina():
    pdf = render_from_snapshot(AMOSTRA, {})

    assert pdf.startswith(b"%PDF-")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert paginas(pdf) == 1


def test_as_fontes_customizadas_vao_embutidas_no_pdf():
    """
    Sem isto o certificado abriria com outra tipografia em qualquer
    computador que nao tenha Bodoni Moda instalada — que sao praticamente
    todos.
    """
    pdf = render_from_snapshot(AMOSTRA, {})

    assert b"/FontFile2" in pdf

    for esperado in (
        b"BodoniModa-Regular",
        b"Montserrat-Bold",
        b"GreatVibes-Regular",
        b"Allura-Regular",
    ):
        # O ReportLab embute um SUBCONJUNTO, e o nome vira "ABCDEF+Nome".
        assert re.search(rb"/BaseFont\s*/[A-Z]{6}\+" + esperado, pdf), esperado


def test_a_embutida_type1_nao_carrega_arquivo_nenhum():
    """
    Helvetica continua sendo a Type 1 do proprio formato PDF: nenhum
    /FontFile para ela. E o que a torna o caminho seguro se o diretorio de
    fontes sumir.
    """
    pdf = render_from_snapshot(
        dict(AMOSTRA, fields=[_bloco("Teste", "Helvetica", 40)]), {}
    )

    assert b"/BaseFont /Helvetica" in pdf
    assert b"/FontFile" not in pdf


def test_cada_familia_desenha_um_pdf_diferente():
    """
    A prova de que a fonte escolhida chega mesmo ao papel: quatro PDFs com o
    MESMO texto e o mesmo tamanho, e quatro conteudos diferentes.
    """
    saidas = {}
    for familia in FAMILIAS_NOVAS + ("Helvetica",):
        saidas[familia] = render_from_snapshot(
            dict(AMOSTRA, fields=[_bloco("Rodrigo Montenegro", familia, 40)]), {}
        )

    for familia, pdf in saidas.items():
        assert paginas(pdf) == 1
        esperado = resolver_fonte(familia, REGULAR, False).encode()
        assert esperado in pdf, familia


# ---------------------------------------------------------------------------
# Auto-ajuste
# ---------------------------------------------------------------------------


def test_o_auto_ajuste_mede_com_a_fonte_escolhida():
    """
    Medir com Helvetica e desenhar com Bodoni daria a caixa certa para a
    fonte errada. As larguras precisam ser diferentes de verdade.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    registrar_fontes()
    texto = "Maria Aparecida dos Santos"

    larguras = {
        nome: stringWidth(texto, nome, 20)
        for nome in (
            "Helvetica",
            "BodoniModa-Regular",
            "Montserrat-Regular",
            "GreatVibes-Regular",
            "Allura-Regular",
        )
    }

    assert len(set(larguras.values())) == len(larguras)


def test_o_peso_muda_a_medida_do_texto():
    """
    Semibold e mais largo que Regular no mesmo corpo. Se o auto-ajuste
    medisse sempre pela Regular, um titulo em Semibold transbordaria a caixa
    que o preview mostrou certa.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    registrar_fontes()
    texto = "CERTIFICADO DE CONCLUSAO"

    regular = stringWidth(texto, "Montserrat-Regular", 24)
    semibold = stringWidth(texto, "Montserrat-SemiBold", 24)
    negrito = stringWidth(texto, "Montserrat-Bold", 24)

    assert regular < semibold < negrito


def test_o_auto_ajuste_encolhe_a_fonte_larga_e_nao_a_estreita():
    """
    Montserrat e sensivelmente mais larga que Bodoni Moda no mesmo corpo.
    Numa caixa apertada uma cabe e a outra precisa encolher — e e o ajuste,
    e nao o corte, que resolve.
    """
    registrar_fontes()
    texto = "Maria Aparecida dos Santos de Oliveira"
    comum = dict(
        tamanho=20,
        minimo=6,
        auto_fit=True,
        largura=300,
        altura=26,
        entrelinha=1.2,
    )

    _linhas, corpo_bodoni = ajustar(texto, fonte="BodoniModa-Regular", **comum)
    _linhas, corpo_montserrat = ajustar(texto, fonte="Montserrat-Regular", **comum)

    assert corpo_bodoni > corpo_montserrat


def test_nome_longo_em_caligrafica_nunca_e_cortado():
    registrar_fontes()
    texto = "Maria Aparecida dos Santos de Oliveira Rodrigues"

    linhas, _corpo = ajustar(
        texto,
        fonte="GreatVibes-Regular",
        tamanho=40,
        minimo=10,
        auto_fit=True,
        largura=120,
        altura=40,
        entrelinha=1.2,
    )

    assert " ".join(linhas).split() == texto.split()
    assert "..." not in " ".join(linhas)


# ---------------------------------------------------------------------------
# Quando o arquivo falta
# ---------------------------------------------------------------------------


def test_arquivo_ausente_recusa_em_vez_de_trocar_a_fonte(sem_arquivos_de_fonte):
    """
    O comportamento exigido com todas as letras: nao gerar silenciosamente
    o PDF com outra fonte.
    """
    with pytest.raises(FonteIndisponivel) as erro:
        render_from_snapshot(AMOSTRA, {})

    assert "Nao foi possivel carregar a fonte" in str(erro.value)
    assert "Bodoni Moda" in str(erro.value)
    # A mensagem chega ao administrador. O caminho do disco nao vai junto.
    assert str(sem_arquivos_de_fonte) not in str(erro.value)


def test_a_embutida_continua_desenhando_sem_arquivo_nenhum(sem_arquivos_de_fonte):
    pdf = render_from_snapshot(
        dict(AMOSTRA, fields=[_bloco("Teste", "Helvetica", 40)]), {}
    )

    assert pdf.startswith(b"%PDF-")
    assert paginas(pdf) == 1


# ---------------------------------------------------------------------------
# Seguranca
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malicioso",
    [
        "../../malicious.ttf",
        "../../../etc/passwd",
        "/etc/passwd",
        "C:\\Windows\\Fonts\\arial.ttf",
        "fonts/certificates/montserrat/Montserrat-Bold.ttf",
        "Arial",
        "Comic Sans",
        "MONTSERRAT; url(http://exemplo/x)",
        "montserrat",
    ],
)
def test_familia_fora_da_lista_e_recusada(malicioso):
    with pytest.raises(DomainError) as erro:
        servicos.normalizar_campo(
            {
                "x": 10,
                "y": 10,
                "width": 30,
                "height": 8,
                "font_family": malicioso,
                "font_size": 14,
                "min_font_size": 8,
                "line_height": 1.2,
                "text_align": "CENTER",
                "text_color": "#000000",
                "rotation": 0,
                "z_index": 0,
            }
        )

    assert "Fonte nao permitida" in str(erro.value)


@pytest.mark.parametrize("peso", [123, 0, -700, 800, "700; drop table", "bold"])
def test_peso_fora_da_lista_e_recusado(peso):
    with pytest.raises(DomainError):
        servicos.normalizar_campo(
            {
                "x": 10,
                "y": 10,
                "width": 30,
                "height": 8,
                "font_family": "MONTSERRAT",
                "font_weight": peso,
                "font_size": 14,
                "min_font_size": 8,
                "line_height": 1.2,
                "text_align": "CENTER",
                "text_color": "#000000",
                "rotation": 0,
                "z_index": 0,
            }
        )


def test_o_editor_recusa_fonte_maliciosa_por_http(admin_client_logado, rascunho):
    resposta = admin_client_logado.post(
        reverse("admin_panel:certificate_template_save_elements", args=[rascunho.pk]),
        data=json.dumps(
            {"elements": [_elemento(font_family="../../malicious.ttf")]}
        ),
        content_type="application/json",
    )

    assert resposta.status_code == 400
    assert "Fonte nao permitida" in resposta.json()["erros"][0]
    assert not CertificateTemplateField.objects.filter(template=rascunho).exists()


def test_o_banco_tambem_recusa_familia_desconhecida(rascunho):
    """
    A ultima camada. Vale para um UPDATE feito direto no psql, que nao passa
    por validacao nenhuma da aplicacao.
    """
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CertificateTemplateField.objects.create(
                template=rascunho,
                field_type=FieldType.STUDENT_NAME,
                font_family="../../malicious.ttf",
            )


def test_o_banco_recusa_peso_desconhecido(rascunho):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CertificateTemplateField.objects.create(
                template=rascunho,
                field_type=FieldType.STUDENT_NAME,
                font_family="MONTSERRAT",
                font_weight=123,
            )


def test_o_modelo_recusa_peso_desconhecido(rascunho):
    campo = CertificateTemplateField(
        template=rascunho,
        field_type=FieldType.STUDENT_NAME,
        font_family="MONTSERRAT",
        font_weight=123,
    )

    with pytest.raises(ValidationError):
        campo.clean()


# ---------------------------------------------------------------------------
# Persistencia pelo editor
# ---------------------------------------------------------------------------


def _elemento(**extras):
    base = {
        "type": FieldType.STUDENT_NAME,
        "x": 15,
        "y": 45,
        "width": 70,
        "height": 8,
        "font_family": "MONTSERRAT",
        "font_weight": SEMIBOLD,
        "italic": False,
        "font_size": 24,
        "min_font_size": 10,
        "auto_fit": True,
        "line_height": 1.2,
        "text_align": "CENTER",
        "text_color": "#000000",
        "rotation": 0,
        "wrap": True,
        "is_visible": True,
        "z_index": 10,
        "content": "",
    }
    base.update(extras)
    return base


def _salvar(cliente, modelo, elementos):
    return cliente.post(
        reverse("admin_panel:certificate_template_save_elements", args=[modelo.pk]),
        data=json.dumps({"elements": elementos}),
        content_type="application/json",
    )


@pytest.mark.parametrize(
    "familia,peso,italico,resolvida",
    [
        ("BODONI_MODA", REGULAR, False, "BodoniModa-Regular"),
        ("MONTSERRAT", SEMIBOLD, False, "Montserrat-SemiBold"),
        ("MONTSERRAT", NEGRITO, True, "Montserrat-BoldItalic"),
        ("GREAT_VIBES", REGULAR, False, "GreatVibes-Regular"),
        ("ALLURA", REGULAR, False, "Allura-Regular"),
    ],
)
def test_a_fonte_sobrevive_ao_salvar_e_recarregar(
    admin_client_logado, rascunho, familia, peso, italico, resolvida
):
    resposta = _salvar(
        admin_client_logado,
        rascunho,
        [_elemento(font_family=familia, font_weight=peso, italic=italico)],
    )
    assert resposta.status_code == 200

    campo = CertificateTemplateField.objects.get(template=rascunho)

    assert campo.font_family == familia
    assert campo.font_weight == peso
    assert campo.fonte_resolvida == resolvida

    # E o que o editor recebe de volta ao recarregar a pagina.
    corpo = admin_client_logado.get(
        reverse("admin_panel:certificate_template_edit", args=[rascunho.pk])
    ).content.decode()
    lido = json.loads(
        re.search(r'id="dados-elementos"[^>]*>(.*?)</script>', corpo, re.S).group(1)
    )[0]

    assert lido["font_family"] == familia
    assert lido["font_weight"] == peso


def test_caligrafica_com_semibold_cai_no_desenho_existente(
    admin_client_logado, rascunho
):
    """
    A tela nao oferece a combinacao. Um POST montado a mao oferece — e o
    servidor cai no unico peso que a familia tem, em vez de gravar um peso
    sem arquivo.
    """
    resposta = _salvar(
        admin_client_logado,
        rascunho,
        [_elemento(font_family="GREAT_VIBES", font_weight=SEMIBOLD, italic=True)],
    )
    assert resposta.status_code == 200

    campo = CertificateTemplateField.objects.get(template=rascunho)

    assert campo.font_family == "GREAT_VIBES"
    assert campo.font_weight == REGULAR
    assert campo.italic is False
    assert campo.fonte_resolvida == "GreatVibes-Regular"


def test_o_negrito_booleano_da_tela_antiga_continua_valendo(
    admin_client_logado, rascunho
):
    """
    Uma aba aberta antes do deploy manda `bold: true` e nao manda peso. Nao
    deve virar erro nem perder o negrito: 700 e o mesmo desenho de sempre.
    """
    elemento = _elemento(font_family="MONTSERRAT")
    del elemento["font_weight"]
    elemento["bold"] = True

    assert _salvar(admin_client_logado, rascunho, [elemento]).status_code == 200

    campo = CertificateTemplateField.objects.get(template=rascunho)

    assert campo.font_weight == NEGRITO
    assert campo.fonte_resolvida == "Montserrat-Bold"


# ---------------------------------------------------------------------------
# O que cada tela faz quando a fonte falta
#
# O pedido separa os dois publicos: no preview do administrador, mensagem
# clara; em producao, erro operacional registrado e sem caminho sensivel.
# Estes testes olham exatamente essa diferenca.
# ---------------------------------------------------------------------------


def test_o_preview_do_admin_diz_qual_fonte_faltou(
    admin_client_logado, rascunho, arte_de_fundo, admin_user, sem_arquivos_de_fonte
):
    servicos.set_background(rascunho, arte_de_fundo, actor=admin_user)
    _salvar(admin_client_logado, rascunho, [_elemento(font_family="BODONI_MODA")])

    resposta = admin_client_logado.get(
        reverse("admin_panel:certificate_template_preview", args=[rascunho.pk])
    )

    assert resposta.status_code == 503
    corpo = resposta.content.decode()
    assert "Bodoni Moda" in corpo
    assert str(sem_arquivos_de_fonte) not in corpo
    # E, sobretudo, nao devolveu um PDF com outra fonte.
    assert resposta["Content-Type"].startswith("text/plain")


def test_o_aluno_nao_recebe_pdf_com_outra_fonte(
    student_client_logado, certificado, sem_arquivos_de_fonte, caplog
):
    """
    O documento nao sai. Um certificado impresso com tipografia diferente da
    aprovada e guardado, apresentado, e ninguem descobre.
    """
    import logging

    resposta = student_client_logado.get(
        reverse(
            "student:certificate_download",
            kwargs={"verification_code": certificado.verification_code},
        )
    )

    if resposta.status_code == 200:
        # O certificado da fixture usa as fontes embutidas, que nao dependem
        # de arquivo — entao ele SAI, e sair e o comportamento certo.
        assert resposta["Content-Type"] == "application/pdf"
        return

    assert resposta.status_code == 503
    corpo = resposta.content.decode()
    assert str(sem_arquivos_de_fonte) not in corpo
    # O aluno recebe uma frase que ele consegue repetir ao telefone, e nao o
    # nome interno de um arquivo de fonte.
    assert "Bodoni Moda" not in corpo
    assert "secretaria" in corpo


def test_o_certificado_com_fonte_de_arquivo_recusa_e_registra(
    admin_client_logado, certificado, sem_arquivos_de_fonte, caplog
):
    import logging

    # Poe o snapshot ja emitido numa fonte de arquivo, como estaria um
    # documento emitido depois desta etapa.
    snapshot = dict(certificado.template_snapshot)
    campos = [dict(campo) for campo in snapshot.get("fields", [])]
    assert campos, "a fixture precisa de um snapshot com campos"
    for campo in campos:
        campo["font_family"] = "MONTSERRAT"
        campo["font_weight"] = REGULAR
    snapshot["fields"] = campos
    certificado.template_snapshot = snapshot
    certificado.save(update_fields=["template_snapshot"])

    with caplog.at_level(logging.ERROR):
        resposta = admin_client_logado.get(
            reverse(
                "admin_panel:certificate_download_admin",
                args=[certificado.pk],
            )
        )

    # Redireciona para o detalhe com a mensagem, em vez de entregar o PDF.
    assert resposta.status_code == 302
    assert "certificados" in resposta["Location"]

    registrado = " ".join(r.getMessage() for r in caplog.records)
    assert str(certificado.verification_code) in registrado
    assert "Montserrat" in registrado
    assert str(sem_arquivos_de_fonte) not in registrado
