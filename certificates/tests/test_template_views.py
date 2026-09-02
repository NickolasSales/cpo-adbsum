"""
Telas do editor de modelos (Etapa 10).

Concentrado no que a interface promete e no que ela recusa:

    ADMIN ve, aluno recebe 403, anonimo vai para o login
    escrita e sempre POST com CSRF
    a arte nao tem URL publica
    o browser nao escolhe status, versao nem autor
"""

import pytest
from django.test import Client
from django.urls import reverse

from certificates import services_templates as servicos
from certificates.models import (
    CertificateTemplate,
    CertificateTemplateField,
    FieldType,
    TemplateStatus,
)
from conftest import png_de_teste

pytestmark = pytest.mark.django_db


def url(nome, *args):
    return reverse("admin_panel:{}".format(nome), args=args)


def upload(nome="arte.png", dados=None):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(
        nome, dados if dados is not None else png_de_teste(), content_type="image/png"
    )


def campos_do_post(tipo, **extras):
    """Payload de um campo, no formato prefixado que o formulario envia."""
    base = {
        "ativo": "1",
        "x": "10",
        "y": "20",
        "width": "50",
        "height": "8",
        "font_family": "Helvetica",
        "font_size": "14",
        "min_font_size": "8",
        "auto_fit": "1",
        "line_height": "1.2",
        "text_align": "CENTER",
        "text_color": "#000000",
        "rotation": "0",
        "is_visible": "1",
        "z_index": "5",
    }
    base.update(extras)
    return {"{}-{}".format(tipo, chave): valor for chave, valor in base.items()}


@pytest.fixture
def rascunho(admin_user):
    return servicos.create_template(name="Modelo novo", actor=admin_user)


# ---------------------------------------------------------------------------
# Acesso
# ---------------------------------------------------------------------------


TELAS = [
    ("certificate_template_list", False),
    ("certificate_template_create", False),
]

TELAS_COM_ID = [
    "certificate_template_edit",
    "certificate_template_preview",
    "certificate_template_art",
]

ESCRITAS = [
    "certificate_template_update",
    "certificate_template_save_fields",
    "certificate_template_background",
    "certificate_template_activate",
    "certificate_template_archive",
    "certificate_template_duplicate",
]


@pytest.mark.parametrize("nome,_", TELAS)
def test_admin_abre_as_telas_sem_id(admin_client_logado, nome, _):
    assert admin_client_logado.get(url(nome)).status_code == 200


@pytest.mark.parametrize("nome", TELAS_COM_ID)
def test_admin_abre_as_telas_com_id(
    admin_client_logado, modelo_de_certificado, nome
):
    assert (
        admin_client_logado.get(url(nome, modelo_de_certificado.pk)).status_code
        == 200
    )


@pytest.mark.parametrize("nome,_", TELAS)
def test_aluno_nao_abre_as_telas(student_client_logado, nome, _):
    assert student_client_logado.get(url(nome)).status_code == 403


@pytest.mark.parametrize("nome", TELAS_COM_ID)
def test_aluno_nao_abre_a_arte_nem_o_preview(
    student_client_logado, modelo_de_certificado, nome
):
    """
    A arte e material administrativo.

    Ela nao e servida pelo Nginx nem mora em STATIC_ROOT justamente para que
    esta verificacao exista: a entrega passa por uma view com controle de
    acesso.
    """
    resposta = student_client_logado.get(url(nome, modelo_de_certificado.pk))
    assert resposta.status_code == 403


@pytest.mark.parametrize("nome", TELAS_COM_ID)
def test_anonimo_vai_para_o_login(client, modelo_de_certificado, nome):
    resposta = client.get(url(nome, modelo_de_certificado.pk))

    assert resposta.status_code == 302
    assert "/login/" in resposta["Location"]


@pytest.mark.parametrize("nome", ESCRITAS)
def test_get_nas_rotas_de_escrita_devolve_405(
    admin_client_logado, modelo_de_certificado, nome
):
    resposta = admin_client_logado.get(url(nome, modelo_de_certificado.pk))
    assert resposta.status_code == 405


@pytest.mark.parametrize("nome", ESCRITAS)
def test_aluno_nao_escreve(student_client_logado, modelo_de_certificado, nome):
    resposta = student_client_logado.post(url(nome, modelo_de_certificado.pk))
    assert resposta.status_code == 403


@pytest.mark.parametrize("nome", ESCRITAS)
def test_post_sem_csrf_e_recusado(admin_user, modelo_de_certificado, nome):
    cliente = Client(enforce_csrf_checks=True)
    cliente.force_login(admin_user)

    resposta = cliente.post(url(nome, modelo_de_certificado.pk))
    assert resposta.status_code == 403


@pytest.mark.parametrize("nome", TELAS_COM_ID + ESCRITAS)
def test_id_inexistente_devolve_404(admin_client_logado, nome):
    metodo = (
        admin_client_logado.get
        if nome in TELAS_COM_ID
        else admin_client_logado.post
    )
    assert metodo(url(nome, 999999)).status_code == 404


# ---------------------------------------------------------------------------
# Criacao e mass assignment
# ---------------------------------------------------------------------------


def test_cria_modelo_em_rascunho(admin_client_logado, admin_user):
    resposta = admin_client_logado.post(
        url("certificate_template_create"),
        {
            "name": "Certificado CPO",
            "description": "Arte oficial",
            "page_orientation": "LANDSCAPE",
            "page_width_mm": "297",
            "page_height_mm": "210",
        },
    )

    assert resposta.status_code == 302
    modelo = CertificateTemplate.objects.get(name="Certificado CPO")
    assert modelo.status == TemplateStatus.DRAFT
    assert modelo.version == 1
    assert modelo.created_by == admin_user


def test_o_browser_nao_escolhe_status_versao_nem_autor(
    admin_client_logado, admin_user, outro_student
):
    """
    Mass assignment.

    O POST manda status, version e created_by. Nenhum dos tres chega ao banco
    pelo valor enviado: quem decide e o servico.
    """
    admin_client_logado.post(
        url("certificate_template_create"),
        {
            "name": "Forjado",
            "page_orientation": "LANDSCAPE",
            "page_width_mm": "297",
            "page_height_mm": "210",
            "status": "ACTIVE",
            "version": "99",
            "created_by": outro_student.pk,
            "background_checksum": "falso",
        },
    )

    modelo = CertificateTemplate.objects.get(name="Forjado")
    assert modelo.status == TemplateStatus.DRAFT
    assert modelo.version == 1
    assert modelo.created_by == admin_user
    assert modelo.background_checksum == ""


def test_nome_vazio_devolve_400(admin_client_logado):
    resposta = admin_client_logado.post(
        url("certificate_template_create"),
        {
            "name": "   ",
            "page_orientation": "LANDSCAPE",
            "page_width_mm": "297",
            "page_height_mm": "210",
        },
    )

    assert resposta.status_code == 400
    assert not CertificateTemplate.objects.filter(name="").exists()


# ---------------------------------------------------------------------------
# Upload pela tela
# ---------------------------------------------------------------------------


def test_upload_pela_tela(admin_client_logado, rascunho):
    resposta = admin_client_logado.post(
        url("certificate_template_background", rascunho.pk),
        {"background": upload()},
    )

    assert resposta.status_code == 302
    rascunho.refresh_from_db()
    assert rascunho.background


def test_upload_invalido_nao_grava(admin_client_logado, rascunho):
    resposta = admin_client_logado.post(
        url("certificate_template_background", rascunho.pk),
        {"background": upload(dados=b"<html>nao sou imagem</html>")},
    )

    assert resposta.status_code == 302
    rascunho.refresh_from_db()
    assert not rascunho.background


def test_upload_em_modelo_usado_devolve_409(admin_client_logado, certificado):
    template = certificado.certificate_template

    resposta = admin_client_logado.post(
        url("certificate_template_background", template.pk),
        {"background": upload()},
    )

    assert resposta.status_code == 409


# ---------------------------------------------------------------------------
# Campos pela tela
# ---------------------------------------------------------------------------


def test_salva_campos_pela_tela(admin_client_logado, rascunho):
    dados = {}
    dados.update(campos_do_post(FieldType.STUDENT_NAME, x="25", y="40"))
    dados.update(campos_do_post(FieldType.QR_CODE, x="80", y="70"))

    resposta = admin_client_logado.post(
        url("certificate_template_save_fields", rascunho.pk), dados
    )

    assert resposta.status_code == 302
    tipos = set(
        CertificateTemplateField.objects.filter(template=rascunho).values_list(
            "field_type", flat=True
        )
    )
    assert tipos == {FieldType.STUDENT_NAME, FieldType.QR_CODE}


def test_campo_sem_marcador_nao_e_criado(admin_client_logado, rascunho):
    dados = campos_do_post(FieldType.STUDENT_NAME)
    del dados["{}-ativo".format(FieldType.STUDENT_NAME)]

    admin_client_logado.post(
        url("certificate_template_save_fields", rascunho.pk), dados
    )

    assert not CertificateTemplateField.objects.filter(template=rascunho).exists()


def test_fonte_forjada_pela_tela_e_recusada(admin_client_logado, rascunho):
    """
    O select do formulario so tem a lista branca, mas o POST e montado a mao.
    Quem recusa e o servico.
    """
    dados = campos_do_post(
        FieldType.STUDENT_NAME, font_family="../../../etc/passwd"
    )

    resposta = admin_client_logado.post(
        url("certificate_template_save_fields", rascunho.pk), dados
    )

    assert resposta.status_code == 302
    assert not CertificateTemplateField.objects.filter(template=rascunho).exists()


def test_cor_forjada_pela_tela_e_recusada(admin_client_logado, rascunho):
    dados = campos_do_post(
        FieldType.STUDENT_NAME, text_color="red;background:url(x)"
    )

    admin_client_logado.post(
        url("certificate_template_save_fields", rascunho.pk), dados
    )

    assert not CertificateTemplateField.objects.filter(template=rascunho).exists()


def test_tipo_inventado_pela_tela_e_ignorado(admin_client_logado, rascunho):
    """
    Um prefixo que nao esta na lista de tipos nem chega ao servico: a view le
    por nome, a partir da lista fechada.
    """
    dados = campos_do_post("ATTEMPT_STUDENT_PASSWORD")
    dados.update(campos_do_post(FieldType.STUDENT_NAME))

    admin_client_logado.post(
        url("certificate_template_save_fields", rascunho.pk), dados
    )

    tipos = set(
        CertificateTemplateField.objects.filter(template=rascunho).values_list(
            "field_type", flat=True
        )
    )
    assert tipos == {FieldType.STUDENT_NAME}


def test_editar_modelo_usado_devolve_409(admin_client_logado, certificado):
    template = certificado.certificate_template

    resposta = admin_client_logado.post(
        url("certificate_template_save_fields", template.pk),
        campos_do_post(FieldType.STUDENT_NAME),
    )

    assert resposta.status_code == 409


# ---------------------------------------------------------------------------
# Ciclo de vida pela tela
# ---------------------------------------------------------------------------


def test_ativar_sem_arte_devolve_409(admin_client_logado, rascunho):
    resposta = admin_client_logado.post(
        url("certificate_template_activate", rascunho.pk)
    )

    assert resposta.status_code == 409
    rascunho.refresh_from_db()
    assert rascunho.status == TemplateStatus.DRAFT


def test_duplicar_leva_para_a_copia(admin_client_logado, modelo_de_certificado):
    resposta = admin_client_logado.post(
        url("certificate_template_duplicate", modelo_de_certificado.pk)
    )

    copia = CertificateTemplate.objects.exclude(
        pk=modelo_de_certificado.pk
    ).get()
    assert resposta.status_code == 302
    assert resposta["Location"].endswith(
        url("certificate_template_edit", copia.pk)
    )


def test_arquivar_pela_tela(admin_client_logado, modelo_de_certificado):
    resposta = admin_client_logado.post(
        url("certificate_template_archive", modelo_de_certificado.pk)
    )

    assert resposta.status_code == 302
    modelo_de_certificado.refresh_from_db()
    assert modelo_de_certificado.status == TemplateStatus.ARCHIVED


# ---------------------------------------------------------------------------
# Conteudo das telas
# ---------------------------------------------------------------------------


def test_a_lista_avisa_quando_nao_ha_padrao(admin_client_logado, rascunho):
    conteudo = admin_client_logado.get(
        url("certificate_template_list")
    ).content.decode()

    assert "Nenhum modelo padrao ativo" in conteudo


def test_o_editor_avisa_sobre_arte_com_campos_impressos(
    admin_client_logado, rascunho
):
    """
    O aviso do §28: se a arte ja trouxer os textos variaveis, os dados serao
    desenhados por cima e o documento sai duplicado.
    """
    conteudo = admin_client_logado.get(
        url("certificate_template_edit", rascunho.pk)
    ).content.decode()

    assert "sem os campos variaveis" in conteudo
    assert "duplicada" in conteudo


def test_o_editor_de_modelo_usado_explica_o_caminho(
    admin_client_logado, certificado
):
    conteudo = admin_client_logado.get(
        url("certificate_template_edit", certificado.certificate_template_id)
    ).content.decode()

    assert "nao aceita mais alteracoes" in conteudo
    assert "duplique" in conteudo.lower()


def test_o_editor_lista_todos_os_tipos_de_campo(admin_client_logado, rascunho):
    conteudo = admin_client_logado.get(
        url("certificate_template_edit", rascunho.pk)
    ).content.decode()

    for tipo in FieldType.values:
        if tipo == FieldType.STATIC_IMAGE:
            continue
        assert '{}-x'.format(tipo) in conteudo


def test_a_arte_nao_e_cacheada_por_intermediario(
    admin_client_logado, modelo_de_certificado
):
    resposta = admin_client_logado.get(
        url("certificate_template_art", modelo_de_certificado.pk)
    )

    assert "no-store" in resposta["Cache-Control"]
    assert resposta["X-Content-Type-Options"] == "nosniff"


def test_o_preview_pode_ser_baixado(admin_client_logado, modelo_de_certificado):
    resposta = admin_client_logado.get(
        url("certificate_template_preview", modelo_de_certificado.pk),
        {"baixar": "1"},
    )

    assert resposta["Content-Disposition"].startswith("attachment")
    assert resposta.content.startswith(b"%PDF-")


def test_arte_de_modelo_sem_fundo_devolve_404(admin_client_logado, rascunho):
    resposta = admin_client_logado.get(
        url("certificate_template_art", rascunho.pk)
    )

    assert resposta.status_code == 404


# ---------------------------------------------------------------------------
# Vinculo com o modulo
# ---------------------------------------------------------------------------


def test_o_formulario_do_modulo_so_oferece_modelos_ativos(
    admin_client_logado, modelo_de_certificado, rascunho, modulo
):
    from courses.forms import ModuleForm

    formulario = ModuleForm()
    disponiveis = set(formulario.fields["certificate_template"].queryset)

    assert modelo_de_certificado in disponiveis
    assert rascunho not in disponiveis


def test_vincular_modelo_ao_modulo_pela_tela(
    admin_client_logado, modelo_de_certificado, modulo
):
    resposta = admin_client_logado.post(
        url("module_update", modulo.pk),
        {
            "name": modulo.name,
            "code": modulo.code,
            "description": "",
            "order": modulo.order,
            "is_active": "on",
            "certificate_display_name": modulo.certificate_display_name,
            "certificate_course_dates_text": modulo.certificate_course_dates_text,
            "certificate_location": modulo.certificate_location,
            "certificate_workload_hours": modulo.certificate_workload_hours,
            "certificate_year": modulo.certificate_year,
            "certificate_template": modelo_de_certificado.pk,
        },
    )

    assert resposta.status_code == 302
    modulo.refresh_from_db()
    assert modulo.certificate_template_id == modelo_de_certificado.pk


def test_modulo_nao_aceita_modelo_em_rascunho(
    admin_client_logado, rascunho, modulo
):
    """
    O select nao oferece rascunho, e o POST montado a mao tambem nao passa: o
    queryset do campo e a validacao.
    """
    resposta = admin_client_logado.post(
        url("module_update", modulo.pk),
        {
            "name": modulo.name,
            "code": modulo.code,
            "description": "",
            "order": modulo.order,
            "is_active": "on",
            "certificate_display_name": modulo.certificate_display_name,
            "certificate_course_dates_text": modulo.certificate_course_dates_text,
            "certificate_location": modulo.certificate_location,
            "certificate_workload_hours": modulo.certificate_workload_hours,
            "certificate_year": modulo.certificate_year,
            "certificate_template": rascunho.pk,
        },
    )

    assert resposta.status_code == 200
    modulo.refresh_from_db()
    assert modulo.certificate_template_id is None


# ---------------------------------------------------------------------------
# Ajuste do certificado oficial: data de conclusao, negrito e italico
# ---------------------------------------------------------------------------


def test_a_tela_oferece_a_data_de_conclusao(admin_client_logado, rascunho):
    """
    O campo precisa aparecer na lista, senao nao ha como configura-lo — e o
    certificado oficial imprime a data de conclusao.
    """
    resposta = admin_client_logado.get(url("certificate_template_edit", rascunho.pk))
    corpo = resposta.content.decode()

    assert resposta.status_code == 200
    assert "COMPLETION_DATE-x" in corpo
    assert "Data de conclusao" in corpo


def test_a_tela_oferece_negrito_e_italico(admin_client_logado, rascunho):
    resposta = admin_client_logado.get(url("certificate_template_edit", rascunho.pk))
    corpo = resposta.content.decode()

    assert 'name="STUDENT_NAME-bold"' in corpo
    assert 'name="STUDENT_NAME-italic"' in corpo


def test_a_tela_oferece_familias_e_nao_nomes_compostos(admin_client_logado, rascunho):
    """
    O select passou a listar familias. Oferecer "Times-BoldItalic" junto com
    as caixas de negrito e italico deixaria duas formas de dizer a mesma
    coisa na mesma tela.
    """
    corpo = admin_client_logado.get(
        url("certificate_template_edit", rascunho.pk)
    ).content.decode()

    assert 'value="Times"' in corpo
    assert 'value="Times-BoldItalic"' not in corpo


def test_a_tela_avisa_sobre_arte_com_texto_gravado(
    admin_client_logado, rascunho, admin_user, arte_de_fundo
):
    """
    O sistema nao apaga texto da imagem em runtime — isso produziria borrao
    num documento oficial. O que ele faz e dizer o que procurar no preview.
    """
    servicos.set_background(rascunho, arte_de_fundo, actor=admin_user)

    corpo = admin_client_logado.get(
        url("certificate_template_edit", rascunho.pk)
    ).content.decode()

    assert "duas vezes" in corpo
    assert "sem os campos variaveis" in corpo


def test_salvar_com_negrito_e_italico(admin_client_logado, rascunho):
    dados = campos_do_post(
        FieldType.STUDENT_NAME, font_family="Times", bold="1", italic="1"
    )

    resposta = admin_client_logado.post(
        url("certificate_template_save_fields", rascunho.pk), dados
    )
    campo = CertificateTemplateField.objects.get(template=rascunho)

    assert resposta.status_code == 302
    assert campo.font_family == "Times"
    assert campo.bold is True
    assert campo.italic is True
    assert campo.fonte_resolvida == "Times-BoldItalic"


def test_desmarcar_negrito_volta_para_a_regular(admin_client_logado, rascunho):
    """
    Marcador ausente no POST vale False. Sem isto, negrito seria um caminho
    so de ida: uma vez marcado, nunca mais sairia.
    """
    admin_client_logado.post(
        url("certificate_template_save_fields", rascunho.pk),
        campos_do_post(FieldType.STUDENT_NAME, font_family="Times", bold="1"),
    )
    admin_client_logado.post(
        url("certificate_template_save_fields", rascunho.pk),
        campos_do_post(FieldType.STUDENT_NAME, font_family="Times"),
    )

    campo = CertificateTemplateField.objects.get(template=rascunho)

    assert campo.bold is False
    assert campo.fonte_resolvida == "Times-Roman"
