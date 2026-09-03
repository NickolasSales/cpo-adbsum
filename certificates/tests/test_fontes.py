"""
Familia, negrito e italico.

O pedido do ajuste era simples: negrito e italico como opcoes proprias no
editor. O que ele nao pode virar e um segundo lugar onde a fonte esta
escrita. Com Type 1 nao existe "Times-Bold mais italico" — existe
"Times-BoldItalic" —, entao guardar o nome composto E os marcadores criaria
duas versoes do mesmo fato, e um dia elas discordariam.

A saida: o campo guarda a FAMILIA e dois booleanos, e o nome PostScript e
calculado. Estes testes cercam as tres consequencias disso:

    a combinacao produz o nome certo, para as tres familias
    a configuracao antiga, com nome composto, continua sendo entendida
    o navegador continua sem conseguir escolher fonte fora da lista
"""

import pytest
from django.db import IntegrityError, transaction

from certificates import services_templates as servicos
from certificates.fonts import NEGRITO, REGULAR
from certificates.models import (
    FAMILIAS_PERMITIDAS,
    FONTES_PERMITIDAS,
    CertificateTemplateField,
    FieldType,
    decompor_fonte,
    resolver_fonte,
)
from certificates.render import render_from_snapshot
from certificates.snapshot import montar_snapshot, valores_de_preview
from common.exceptions import DomainError

pytestmark = pytest.mark.django_db


def campo(**extras):
    base = {
        "x": 10,
        "y": 20,
        "width": 50,
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
        "z_index": 5,
    }
    base.update(extras)
    return base


@pytest.fixture
def rascunho(admin_user):
    return servicos.create_template(name="Modelo de fonte", actor=admin_user)


# ---------------------------------------------------------------------------
# A tabela
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "familia,negrito,italico,esperado",
    [
        ("Helvetica", False, False, "Helvetica"),
        ("Helvetica", True, False, "Helvetica-Bold"),
        ("Helvetica", False, True, "Helvetica-Oblique"),
        ("Helvetica", True, True, "Helvetica-BoldOblique"),
        ("Times", False, False, "Times-Roman"),
        ("Times", True, False, "Times-Bold"),
        ("Times", False, True, "Times-Italic"),
        ("Times", True, True, "Times-BoldItalic"),
        ("Courier", False, False, "Courier"),
        ("Courier", True, True, "Courier-BoldOblique"),
    ],
)
def test_a_combinacao_produz_o_nome_postscript(familia, negrito, italico, esperado):
    """
    Repare em "Times-Roman" e "Helvetica-Oblique".

    Nao ha regra entre as familias: a regular do Times tem sufixo e a da
    Helvetica nao, e uma delas chama a inclinada de Italic e a outra de
    Oblique. Concatenar strings produziria nomes que o ReportLab nao conhece.
    """
    assert resolver_fonte(familia, negrito, italico) == esperado


def test_toda_combinacao_cai_na_lista_do_renderizador():
    for familia in FAMILIAS_PERMITIDAS:
        for negrito in (False, True):
            for italico in (False, True):
                assert resolver_fonte(familia, negrito, italico) in FONTES_PERMITIDAS


def test_resolver_e_idempotente():
    """
    Resolver duas vezes nao acumula estilo.

    E o que permite aplicar a funcao sobre um snapshot antigo, que ja guarda
    o nome composto, sem alterar o documento.
    """
    for nome in FONTES_PERMITIDAS:
        assert resolver_fonte(resolver_fonte(nome)) == nome


def test_decompor_entende_o_nome_composto():
    """Devolve (familia, PESO, italico) — o negrito e o peso 700."""
    assert decompor_fonte("Times-BoldItalic") == ("Times", NEGRITO, True)
    assert decompor_fonte("Helvetica-Oblique") == ("Helvetica", REGULAR, True)
    assert decompor_fonte("Courier") == ("Courier", REGULAR, False)
    assert decompor_fonte("Montserrat-SemiBold") == ("MONTSERRAT", 600, False)


def test_o_marcador_booleano_no_lugar_do_peso_continua_valendo():
    """
    `True` E um int de valor 1 em Python. Sem a guarda de normalizar_peso,
    resolver_fonte("Times", True) pediria peso 1, cairia no mais proximo —
    400 — e devolveria a Regular. O negrito sumiria em silencio em todo
    chamador que ainda passe um marcador: snapshot antigo, script, aba
    aberta antes do deploy.
    """
    assert resolver_fonte("Times", True, True) == "Times-BoldItalic"
    assert resolver_fonte("Times", False, False) == "Times-Roman"
    assert resolver_fonte("MONTSERRAT", True) == "Montserrat-Bold"


def test_decompor_cai_no_padrao_para_o_desconhecido():
    """
    Aqui o silencio e o comportamento certo: esta funcao serve para desenhar
    um snapshot antigo, e um documento nao deve falhar por causa de um nome
    estranho no JSON. Quem recusa e a validacao do formulario — ver abaixo.
    """
    assert decompor_fonte("Arial")[0] == "Helvetica"
    assert decompor_fonte(None)[0] == "Helvetica"
    assert decompor_fonte("")[0] == "Helvetica"


# ---------------------------------------------------------------------------
# O que o formulario aceita
# ---------------------------------------------------------------------------


def test_grava_familia_e_marcadores(rascunho, admin_user):
    servicos.save_fields(
        rascunho,
        {
            FieldType.STUDENT_NAME: campo(
                font_family="Times", bold=True, italic=False
            )
        },
        actor=admin_user,
    )

    gravado = CertificateTemplateField.objects.get(template=rascunho)

    assert gravado.font_family == "Times"
    assert gravado.font_weight == NEGRITO
    assert gravado.italic is False
    assert gravado.fonte_resolvida == "Times-Bold"


def test_o_nome_composto_antigo_continua_sendo_aceito(rascunho, admin_user):
    """
    Um POST com "Times-BoldItalic" vem de tela antiga ou de script.

    Recusa-lo transformaria uma melhoria de interface em perda de
    configuracao. Ele e decomposto, e o resultado e o mesmo.
    """
    servicos.save_fields(
        rascunho,
        {FieldType.STUDENT_NAME: campo(font_family="Times-BoldItalic")},
        actor=admin_user,
    )

    gravado = CertificateTemplateField.objects.get(template=rascunho)

    assert gravado.font_family == "Times"
    assert gravado.font_weight == NEGRITO
    assert gravado.italic is True
    assert gravado.fonte_resolvida == "Times-BoldItalic"


def test_os_marcadores_somam_com_o_nome(rascunho, admin_user):
    servicos.save_fields(
        rascunho,
        {FieldType.STUDENT_NAME: campo(font_family="Times-Bold", italic=True)},
        actor=admin_user,
    )

    assert (
        CertificateTemplateField.objects.get(template=rascunho).fonte_resolvida
        == "Times-BoldItalic"
    )


@pytest.mark.parametrize(
    "fonte",
    ["Arial", "Comic Sans", "../../../etc/passwd", "", "Times ; rm -rf", "times"],
)
def test_recusa_familia_fora_da_lista(rascunho, admin_user, fonte):
    """
    A familia nao pode virar caminho de arquivo nem cair em Helvetica em
    silencio: quem digitou "Arial" precisa saber que ela nao existe aqui.
    """
    with pytest.raises(DomainError) as erro:
        servicos.save_fields(
            rascunho,
            {FieldType.STUDENT_NAME: campo(font_family=fonte)},
            actor=admin_user,
        )

    assert "Fonte nao permitida" in str(erro.value)
    assert not CertificateTemplateField.objects.filter(template=rascunho).exists()


def test_o_banco_tambem_recusa(rascunho, admin_user):
    """A camada que sobrevive a um UPDATE direto."""
    servicos.save_fields(
        rascunho, {FieldType.STUDENT_NAME: campo()}, actor=admin_user
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CertificateTemplateField.objects.filter(template=rascunho).update(
                font_family="Arial"
            )


# ---------------------------------------------------------------------------
# O que chega ao PDF
# ---------------------------------------------------------------------------


def fontes_usadas(snapshot, valores, monkeypatch):
    from reportlab.pdfgen import canvas as modulo_canvas

    vistas = []
    original = modulo_canvas.Canvas.setFont

    def espiao(self, nome, tamanho, *args, **kwargs):
        vistas.append(nome)
        return original(self, nome, tamanho, *args, **kwargs)

    monkeypatch.setattr(modulo_canvas.Canvas, "setFont", espiao)
    render_from_snapshot(snapshot, valores)
    return vistas


def test_o_renderizador_recebe_a_fonte_resolvida(
    rascunho, admin_user, arte_de_fundo, monkeypatch
):
    servicos.set_background(rascunho, arte_de_fundo, actor=admin_user)
    servicos.save_fields(
        rascunho,
        {FieldType.STUDENT_NAME: campo(font_family="Times", bold=True, italic=True)},
        actor=admin_user,
    )

    vistas = fontes_usadas(
        montar_snapshot(rascunho), valores_de_preview(), monkeypatch
    )

    assert "Times-BoldItalic" in vistas


def test_snapshot_antigo_mantem_a_fonte_com_que_foi_assinado(monkeypatch):
    """
    Um documento emitido antes desta mudanca guarda "Times-Bold" em
    font_family e nao tem bold/italic. Ele precisa continuar saindo em
    Times-Bold — nao em Times-Roman, e nao em Helvetica.
    """
    antigo = {
        "page_width_mm": 297,
        "page_height_mm": 210,
        "background_path": "",
        "fields": [
            {
                "field_type": FieldType.STUDENT_NAME,
                "x": 10,
                "y": 30,
                "width": 80,
                "height": 10,
                "font_family": "Times-Bold",
                "font_size": 20,
                "min_font_size": 10,
                "auto_fit": True,
                "line_height": 1.2,
                "text_align": "CENTER",
                "text_color": "#000000",
                "rotation": 0,
                "is_visible": True,
                "z_index": 1,
            }
        ],
    }

    vistas = fontes_usadas(antigo, {FieldType.STUDENT_NAME: "Ana"}, monkeypatch)

    assert "Times-Bold" in vistas


def test_fonte_impossivel_no_snapshot_nao_derruba_a_emissao(monkeypatch):
    """
    O snapshot e JSON no banco. Um UPDATE manual pode ter posto qualquer
    coisa ali, e o meio de uma emissao nao e lugar de excecao.
    """
    quebrado = {
        "page_width_mm": 297,
        "page_height_mm": 210,
        "background_path": "",
        "fields": [
            {
                "field_type": FieldType.STUDENT_NAME,
                "x": 10,
                "y": 30,
                "width": 80,
                "height": 10,
                "font_family": "../../../etc/passwd",
                "font_size": 20,
                "min_font_size": 10,
                "auto_fit": True,
                "line_height": 1.2,
                "text_align": "CENTER",
                "text_color": "#000000",
                "rotation": 0,
                "is_visible": True,
                "z_index": 1,
            }
        ],
    }

    vistas = fontes_usadas(quebrado, {FieldType.STUDENT_NAME: "Ana"}, monkeypatch)

    assert vistas == ["Helvetica"]


# ---------------------------------------------------------------------------
# Duplicacao
# ---------------------------------------------------------------------------


def test_duplicar_preserva_negrito_e_italico(rascunho, admin_user, arte_de_fundo):
    servicos.set_background(rascunho, arte_de_fundo, actor=admin_user)
    servicos.save_fields(
        rascunho,
        {FieldType.STUDENT_NAME: campo(font_family="Times", bold=True, italic=True)},
        actor=admin_user,
    )

    copia = servicos.duplicate_template(rascunho, actor=admin_user)
    copiado = CertificateTemplateField.objects.get(template=copia)

    assert copia.version == rascunho.version + 1
    assert copiado.font_family == "Times"
    assert copiado.font_weight == NEGRITO
    assert copiado.italic is True
