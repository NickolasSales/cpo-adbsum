"""
O editor visual: o que ele grava, e o que ele recusa.

O editor manda JSON. JSON e texto que o navegador escreveu, e o servidor o
trata assim: cada elemento e conferido de novo, um a um, contra as mesmas
listas fechadas do formulario antigo. Os testes daqui existem para que essa
frase continue verdadeira depois da proxima alteracao.

A pergunta que atravessa o arquivo: **o que o navegador consegue fazer o
servidor gravar?**
"""

import json
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from audit.models import AuditEvent, AuditLog
from certificates import services_templates as servicos
from certificates.models import CertificateTemplateField, FieldType
from conftest import png_de_teste

pytestmark = pytest.mark.django_db


def url(nome, pk):
    return reverse("admin_panel:{}".format(nome), kwargs={"pk": pk})


def elemento(tipo=FieldType.STUDENT_NAME, **extras):
    base = {
        "type": tipo,
        "x": 20,
        "y": 40,
        "width": 60,
        "height": 8,
        "font_family": "Times",
        "bold": True,
        "italic": False,
        "font_size": 30,
        "min_font_size": 14,
        "auto_fit": True,
        "line_height": 1.2,
        "text_align": "CENTER",
        "text_color": "#1A1A1A",
        "rotation": 0,
        "wrap": True,
        "is_visible": True,
        "z_index": 10,
        "content": "",
    }
    base.update(extras)
    return base


def salvar(cliente, modelo, elementos):
    return cliente.post(
        url("certificate_template_save_elements", modelo.pk),
        data=json.dumps({"elements": elementos}),
        content_type="application/json",
    )


def elementos_da_tela(cliente, modelo):
    """O que o editor recebe ao abrir a pagina — a fonte da verdade do reload."""
    corpo = cliente.get(url("certificate_template_edit", modelo.pk)).content.decode()
    bruto = re.search(
        r'id="dados-elementos"[^>]*>(.*?)</script>', corpo, re.S
    ).group(1)
    return json.loads(bruto)


@pytest.fixture
def rascunho(admin_user, arte_de_fundo):
    modelo = servicos.create_template(name="Modelo do editor", actor=admin_user)
    servicos.set_background(modelo, arte_de_fundo, actor=admin_user)
    return modelo


# ---------------------------------------------------------------------------
# Serializacao: arrastar, salvar, recarregar
# ---------------------------------------------------------------------------


def test_a_posicao_sobrevive_ao_reload(admin_client_logado, rascunho):
    """
    O ciclo inteiro do editor, do jeito que o administrador o vive:
    arrastar, salvar, fechar a pagina, abrir de novo.
    """
    resposta = salvar(
        admin_client_logado,
        rascunho,
        [elemento(x=31.5, y=51.25, width=40, height=7)],
    )
    assert resposta.status_code == 200
    assert resposta.json()["ok"] is True

    lidos = elementos_da_tela(admin_client_logado, rascunho)

    assert len(lidos) == 1
    assert lidos[0]["x"] == 31.5
    assert lidos[0]["y"] == 51.25
    assert lidos[0]["width"] == 40.0
    assert lidos[0]["height"] == 7.0


def test_o_redimensionamento_sobrevive_ao_reload(admin_client_logado, rascunho):
    salvar(admin_client_logado, rascunho, [elemento(width=40, height=7)])
    salvar(admin_client_logado, rascunho, [elemento(width=62.5, height=13.75)])

    lidos = elementos_da_tela(admin_client_logado, rascunho)

    assert lidos[0]["width"] == 62.5
    assert lidos[0]["height"] == 13.75


def test_o_estilo_sobrevive_ao_reload(admin_client_logado, rascunho):
    salvar(
        admin_client_logado,
        rascunho,
        [
            elemento(
                font_family="Courier",
                bold=False,
                italic=True,
                rotation=-90,
                wrap=False,
                is_visible=False,
                text_color="#B08D3D",
                z_index=42,
            )
        ],
    )

    lido = elementos_da_tela(admin_client_logado, rascunho)[0]

    assert lido["font_family"] == "Courier"
    assert lido["italic"] is True
    assert lido["bold"] is False
    assert lido["rotation"] == -90
    assert lido["wrap"] is False
    assert lido["is_visible"] is False
    assert lido["text_color"] == "#B08D3D"
    assert lido["z_index"] == 42


def test_salvar_substitui_o_conjunto_inteiro(admin_client_logado, rascunho):
    salvar(
        admin_client_logado,
        rascunho,
        [elemento(), elemento(FieldType.YEAR), elemento(FieldType.QR_CODE)],
    )
    salvar(admin_client_logado, rascunho, [elemento(FieldType.YEAR)])

    lidos = elementos_da_tela(admin_client_logado, rascunho)

    assert [item["type"] for item in lidos] == [FieldType.YEAR]


def test_a_imagem_fixa_sobrevive_ao_salvar(admin_client_logado, rascunho):
    """
    Ela tem arquivo em disco e nao vem no payload. Apaga-la aqui destruiria
    um upload que a tela nem chegou a oferecer.
    """
    CertificateTemplateField.objects.create(
        template=rascunho,
        field_type=FieldType.STATIC_IMAGE,
        image=SimpleUploadedFile("selo.png", png_de_teste(), content_type="image/png"),
    )

    salvar(admin_client_logado, rascunho, [elemento()])

    assert CertificateTemplateField.objects.filter(
        template=rascunho, field_type=FieldType.STATIC_IMAGE
    ).exists()


def test_um_evento_de_auditoria_por_salvamento(admin_client_logado, rascunho):
    """
    Auditar cada pixel arrastado encheria a trilha de ruido e esconderia os
    atos que importam. O que se registra e a publicacao.
    """
    antes = AuditLog.objects.filter(
        event=AuditEvent.CERTIFICATE_TEMPLATE_UPDATED
    ).count()

    salvar(admin_client_logado, rascunho, [elemento(), elemento(FieldType.YEAR)])

    depois = AuditLog.objects.filter(
        event=AuditEvent.CERTIFICATE_TEMPLATE_UPDATED
    ).count()
    registro = AuditLog.objects.filter(
        event=AuditEvent.CERTIFICATE_TEMPLATE_UPDATED
    ).latest("timestamp")

    assert depois == antes + 1
    assert registro.metadata["elements"] == 2


# ---------------------------------------------------------------------------
# Acesso
# ---------------------------------------------------------------------------


def test_o_editor_e_somente_de_admin(student_client_logado, rascunho):
    resposta = student_client_logado.get(url("certificate_template_edit", rascunho.pk))

    assert resposta.status_code == 403


def test_salvar_e_somente_de_admin(student_client_logado, rascunho):
    resposta = salvar(student_client_logado, rascunho, [elemento()])

    assert resposta.status_code == 403
    assert not CertificateTemplateField.objects.filter(template=rascunho).exists()


def test_anonimo_vai_para_o_login(client, rascunho):
    resposta = client.post(
        url("certificate_template_save_elements", rascunho.pk),
        data=json.dumps({"elements": []}),
        content_type="application/json",
    )

    assert resposta.status_code == 302
    assert "/login/" in resposta["Location"]


def test_salvar_nao_aceita_get(admin_client_logado, rascunho):
    resposta = admin_client_logado.get(
        url("certificate_template_save_elements", rascunho.pk)
    )

    assert resposta.status_code == 405


def test_salvar_exige_csrf(admin_user, rascunho):
    """
    O editor manda o token no cabecalho. Sem ele a requisicao nao passa —
    senao um site qualquer poderia reescrever o certificado da instituicao
    com o navegador do administrador logado.
    """
    cliente = Client(enforce_csrf_checks=True)
    cliente.force_login(admin_user)

    resposta = cliente.post(
        url("certificate_template_save_elements", rascunho.pk),
        data=json.dumps({"elements": [elemento()]}),
        content_type="application/json",
    )

    assert resposta.status_code == 403
    assert not CertificateTemplateField.objects.filter(template=rascunho).exists()


def test_modelo_inexistente_responde_404(admin_client_logado):
    resposta = admin_client_logado.post(
        url("certificate_template_save_elements", 99999999),
        data=json.dumps({"elements": []}),
        content_type="application/json",
    )

    assert resposta.status_code == 404


def test_modelo_ja_usado_responde_409(admin_client_logado, certificado):
    modelo = certificado.certificate_template

    resposta = salvar(admin_client_logado, modelo, [elemento()])

    assert resposta.status_code == 409
    assert resposta.json()["bloqueado"] is True
    assert "duplicar" in resposta.json()["duplicar"]


# ---------------------------------------------------------------------------
# O que o payload nao consegue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "corpo",
    [
        "nao e json",
        "[]",
        '{"elements": "nao e lista"}',
        '{"elements": [1, 2, 3]}',
        '{"outra_chave": []}',
        '{"elements": [null]}',
    ],
)
def test_payload_adulterado_e_recusado(admin_client_logado, rascunho, corpo):
    resposta = admin_client_logado.post(
        url("certificate_template_save_elements", rascunho.pk),
        data=corpo,
        content_type="application/json",
    )

    assert resposta.status_code == 400
    assert resposta.json()["ok"] is False
    assert not CertificateTemplateField.objects.filter(template=rascunho).exists()


@pytest.mark.parametrize(
    "mudanca",
    [
        {"type": "SENHA"},
        {"type": ""},
        {"type": "STATIC_IMAGE"},
        {"font_family": "Arial"},
        {"font_family": "../../../etc/passwd"},
        {"text_color": "red"},
        {"text_color": "red; background:url(x)"},
        {"text_color": "#GGGGGG"},
        {"x": -5},
        {"x": 250},
        {"y": "muito abaixo"},
        {"width": 0},
        {"height": 500},
        {"rotation": 5000},
        {"rotation": "gira"},
        {"font_size": 900},
        {"font_size": 2},
        {"min_font_size": 40},
        {"line_height": 12},
        {"z_index": -3},
        {"text_align": "JUSTIFY"},
    ],
)
def test_valor_forjado_e_recusado(admin_client_logado, rascunho, mudanca):
    resposta = salvar(admin_client_logado, rascunho, [elemento(**mudanca)])

    assert resposta.status_code == 400, mudanca
    assert not CertificateTemplateField.objects.filter(template=rascunho).exists()


@pytest.mark.parametrize(
    "mudanca",
    [
        {"type": 5},
        {"type": ["STUDENT_NAME"]},
        {"type": None},
        {"font_family": 5},
        {"text_align": 7},
        {"text_color": 16711680},
        {"content": 5, "type": FieldType.CUSTOM_TEXT},
        {"x": {"valor": 10}},
        {"rotation": [90]},
    ],
)
def test_tipo_errado_no_json_vira_400_e_nao_500(
    admin_client_logado, rascunho, mudanca
):
    """
    JSON tem numeros, listas e nulos, e o editor nao e a unica coisa que
    consegue falar com esta rota. Um corpo com o tipo errado precisa ser
    RECUSADO, e nao explodir: 500 vira alerta no log, esconde a causa e, num
    caso ruim, deixa a transacao pela metade.
    """
    resposta = salvar(admin_client_logado, rascunho, [elemento(**mudanca)])

    assert resposta.status_code == 400, mudanca
    assert resposta.json()["ok"] is False
    assert not CertificateTemplateField.objects.filter(template=rascunho).exists()


def test_um_elemento_invalido_nao_grava_os_anteriores(admin_client_logado, rascunho):
    """Transacao unica: tudo ou nada."""
    resposta = salvar(
        admin_client_logado,
        rascunho,
        [elemento(), elemento(FieldType.YEAR, font_family="Arial")],
    )

    assert resposta.status_code == 400
    assert not CertificateTemplateField.objects.filter(template=rascunho).exists()


def test_tipo_unico_nao_aceita_duplicata(admin_client_logado, rascunho):
    resposta = salvar(
        admin_client_logado, rascunho, [elemento(), elemento(x=50, y=60)]
    )

    assert resposta.status_code == 400
    assert "uma vez" in " ".join(resposta.json()["erros"])


def test_elementos_demais_sao_recusados(admin_client_logado, rascunho):
    demais = [
        {**elemento(FieldType.CUSTOM_TEXT), "content": "bloco {}".format(indice)}
        for indice in range(servicos.MAXIMO_DE_ELEMENTOS + 1)
    ]

    resposta = salvar(admin_client_logado, rascunho, demais)

    assert resposta.status_code == 400
    assert not CertificateTemplateField.objects.filter(template=rascunho).exists()


def test_chaves_estranhas_sao_ignoradas(admin_client_logado, rascunho, admin_user):
    """
    O payload pode trazer o que quiser: o servico le apenas as chaves que
    conhece. `id`, `template` e `pk` nao existem para ele.
    """
    outro = servicos.create_template(name="Outro modelo", actor=admin_user)

    salvar(
        admin_client_logado,
        rascunho,
        [
            {
                **elemento(),
                "id": 987654,
                "pk": 987654,
                "template": outro.pk,
                "template_id": outro.pk,
                "created_at": "1999-01-01",
            }
        ],
    )

    assert CertificateTemplateField.objects.filter(template=rascunho).count() == 1
    assert not CertificateTemplateField.objects.filter(template=outro).exists()


def test_salvar_nao_alcanca_outro_modelo(admin_client_logado, rascunho, admin_user):
    """
    Nao ha id de elemento atravessando a fronteira: o servico substitui o
    conjunto DESTE modelo, e so dele. Um payload nao consegue mover o campo
    de um modelo vizinho.
    """
    vizinho = servicos.create_template(name="Vizinho", actor=admin_user)
    servicos.save_elements(
        vizinho, [elemento(FieldType.YEAR, x=90, y=20)], actor=admin_user
    )

    salvar(admin_client_logado, rascunho, [elemento(x=10, y=10)])

    do_vizinho = CertificateTemplateField.objects.get(template=vizinho)

    assert float(do_vizinho.x) == 90
    assert do_vizinho.field_type == FieldType.YEAR


def test_html_no_texto_e_gravado_como_texto(admin_client_logado, rascunho):
    """
    O bloco personalizado guarda TEXTO PURO. Nada e interpretado: o que
    entrar sai igual no PDF, incluindo os sinais de menor e maior.
    """
    veneno = '<div style="x"><img src=x onerror=alert(1)></div>'

    salvar(
        admin_client_logado,
        rascunho,
        [elemento(FieldType.CUSTOM_TEXT, content=veneno)],
    )
    campo = CertificateTemplateField.objects.get(template=rascunho)

    assert campo.content == veneno


def test_o_texto_do_administrador_nao_vira_marcacao_na_tela(
    admin_client_logado, rascunho
):
    """
    O editor recebe os elementos por `json_script`, que escapa `<`, `>` e
    `&`. Um `</script>` dentro do texto entra como dado, e nao fecha a tag.
    """
    salvar(
        admin_client_logado,
        rascunho,
        [
            elemento(
                FieldType.CUSTOM_TEXT,
                content="</script><script>alert(1)</script>",
            )
        ],
    )
    corpo = admin_client_logado.get(
        url("certificate_template_edit", rascunho.pk)
    ).content.decode()

    assert "<script>alert(1)</script>" not in corpo
    assert "\\u003Cscript\\u003Ealert(1)" in corpo
    # E o dado continua intacto do outro lado do escape.
    assert elementos_da_tela(admin_client_logado, rascunho)[0]["content"] == (
        "</script><script>alert(1)</script>"
    )


# ---------------------------------------------------------------------------
# Preview com dados de teste
# ---------------------------------------------------------------------------


def test_o_preview_aceita_dados_de_teste(admin_client_logado, rascunho, admin_user):
    servicos.save_elements(rascunho, [elemento()], actor=admin_user)

    resposta = admin_client_logado.get(
        url("certificate_template_preview", rascunho.pk),
        {"nome": "Antonio Melo"},
    )

    assert resposta.status_code == 200
    assert resposta["Content-Type"] == "application/pdf"


def test_o_preview_com_dados_de_teste_nao_cria_certificado(
    admin_client_logado, rascunho, admin_user
):
    from certificates.models import Certificate
    from courses.models import Enrollment

    servicos.save_elements(rascunho, [elemento()], actor=admin_user)
    antes = (Certificate.objects.count(), Enrollment.objects.count())

    admin_client_logado.get(
        url("certificate_template_preview", rascunho.pk),
        {"nome": "Antonio Melo", "data": "03 de setembro de 2026"},
    )

    assert (Certificate.objects.count(), Enrollment.objects.count()) == antes


def test_o_preview_so_aceita_os_parametros_da_lista(
    admin_client_logado, rascunho, admin_user
):
    """
    Um parametro fora da lista nao alcanca campo nenhum. O nome do campo
    nunca vem do navegador.
    """
    servicos.save_elements(rascunho, [elemento()], actor=admin_user)

    resposta = admin_client_logado.get(
        url("certificate_template_preview", rascunho.pk),
        {"student_name_snapshot": "x", "__class__": "y", "senha": "z"},
    )

    assert resposta.status_code == 200


def test_o_preview_e_somente_de_admin(student_client_logado, modelo_de_certificado):
    resposta = student_client_logado.get(
        url("certificate_template_preview", modelo_de_certificado.pk)
    )

    assert resposta.status_code == 403


def test_a_tela_entrega_as_variaveis_para_o_seletor(admin_client_logado, rascunho):
    corpo = admin_client_logado.get(
        url("certificate_template_edit", rascunho.pk)
    ).content.decode()
    variaveis = json.loads(
        re.search(r'id="dados-variaveis"[^>]*>(.*?)</script>', corpo, re.S).group(1)
    )
    chaves = [item["chave"] for item in variaveis]

    assert "{{nome_aluno}}" in chaves
    assert "{{data_conclusao}}" in chaves
    assert "{{qrcode}}" not in chaves
    # O exemplo evita a duvida da carga horaria, que entra sem a unidade.
    exemplo = next(item for item in variaveis if item["chave"] == "{{carga_horaria}}")
    assert " horas" not in exemplo["exemplo"]
