"""
Editor de modelos de certificado (Etapa 10).

Cinco perguntas atravessam o arquivo:

    1  o upload recusa o que nao e imagem, mesmo com extensao mentindo?
    2  o navegador consegue escolher fonte, cor ou caminho fora da lista?
    3  um modelo que ja emitiu certificado continua imutavel?
    4  criar a v2 deixa o certificado da v1 exatamente como estava?
    5  sem modelo configurado, a emissao recusa em vez de inventar layout?
"""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from audit.models import AuditEvent, AuditLog
from certificates import services_templates as servicos
from certificates.models import (
    Certificate,
    CertificateTemplate,
    CertificateTemplateField,
    FieldType,
    TemplateStatus,
)
from certificates.services import issue_certificate
from certificates.snapshot import (
    RESOLVEDORES,
    montar_snapshot,
    resolve_certificate_field,
)
from common.exceptions import DomainError
from conftest import png_de_teste

pytestmark = pytest.mark.django_db


def upload(nome="arte.png", dados=None, tipo="image/png"):
    return SimpleUploadedFile(
        nome, dados if dados is not None else png_de_teste(), content_type=tipo
    )


def campo_valido(**extras):
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
    return servicos.create_template(name="Modelo novo", actor=admin_user)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def test_aceita_png(rascunho, admin_user):
    template, avisos = servicos.set_background(
        rascunho, upload(), actor=admin_user
    )

    assert template.background
    assert template.background_width == 1200
    assert template.background_height == 850
    assert len(template.background_checksum) == 64
    assert avisos == []


def test_aceita_jpg(rascunho, admin_user):
    buffer = io.BytesIO()
    Image.new("RGB", (1200, 850), (255, 255, 255)).save(buffer, format="JPEG")
    template, _ = servicos.set_background(
        rascunho,
        SimpleUploadedFile("arte.jpg", buffer.getvalue(), content_type="image/jpeg"),
        actor=admin_user,
    )

    assert template.background


def test_o_nome_do_arquivo_nao_vira_caminho(rascunho, admin_user):
    """
    Path traversal: o nome enviado nao controla onde o arquivo cai.

    O nome interno e um uuid4. Nada do que veio do navegador sobrevive nele
    — nem os `../`, nem o nome original.
    """
    template, _ = servicos.set_background(
        rascunho,
        upload(nome="../../../../etc/passwd.png"),
        actor=admin_user,
    )

    caminho = template.background.name
    assert ".." not in caminho
    assert "passwd" not in caminho
    assert caminho.startswith("certificate_templates/fundos/")
    assert caminho.endswith(".png")


def test_recusa_html_com_extensao_de_imagem(rascunho, admin_user):
    """
    A extensao e texto escolhido por quem envia.

    Este arquivo se chama .png e comeca com <script>. Quem decide o formato e
    o Pillow, abrindo o conteudo.
    """
    veneno = b"<html><script>alert(1)</script></html>"

    with pytest.raises(DomainError) as erro:
        servicos.set_background(
            rascunho, upload(nome="arte.png", dados=veneno), actor=admin_user
        )

    assert "imagem" in str(erro.value).lower()
    rascunho.refresh_from_db()
    assert not rascunho.background


def test_recusa_svg(rascunho, admin_user):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>'

    with pytest.raises(DomainError):
        servicos.set_background(
            rascunho, upload(nome="arte.svg", dados=svg), actor=admin_user
        )


def test_recusa_extensao_fora_da_lista(rascunho, admin_user):
    with pytest.raises(DomainError) as erro:
        servicos.set_background(
            rascunho,
            upload(nome="arte.pdf", dados=b"%PDF-1.4 nada"),
            actor=admin_user,
        )

    assert "PNG ou JPG" in str(erro.value)


def test_recusa_arquivo_grande(rascunho, admin_user):
    from certificates.uploads import TAMANHO_MAXIMO

    grande = upload(dados=b"x" * (TAMANHO_MAXIMO + 1))

    with pytest.raises(DomainError) as erro:
        servicos.set_background(rascunho, grande, actor=admin_user)

    assert "limite" in str(erro.value).lower()


def test_recusa_arquivo_vazio(rascunho, admin_user):
    with pytest.raises(DomainError):
        servicos.set_background(rascunho, upload(dados=b""), actor=admin_user)


def test_recusa_resolucao_baixa(rascunho, admin_user):
    """Uma arte de 200x140 nao imprime; o documento sairia borrado."""
    with pytest.raises(DomainError) as erro:
        servicos.set_background(
            rascunho, upload(dados=png_de_teste(200, 140)), actor=admin_user
        )

    assert "resolucao" in str(erro.value).lower()


def test_recusa_imagem_com_pixels_demais(rascunho, admin_user):
    """
    Bomba de descompressao.

    O arquivo e pequeno; o cabecalho e que declara uma imagem enorme. A
    checagem acontece antes de decodificar, lendo so o cabecalho — decodificar
    para depois medir seria justamente o que derruba o processo.
    """
    from certificates import uploads

    with pytest.raises(DomainError) as erro:
        servicos.set_background(
            rascunho,
            upload(dados=png_de_teste(uploads.LADO_MAXIMO + 1, 100)),
            actor=admin_user,
        )

    assert "lado" in str(erro.value).lower() or "pixels" in str(erro.value).lower()


def test_avisa_quando_a_proporcao_nao_bate(rascunho, admin_user):
    """Aviso, e nao recusa: o documento sai utilizavel, so esticado."""
    _, avisos = servicos.set_background(
        rascunho, upload(dados=png_de_teste(1200, 1200)), actor=admin_user
    )

    assert avisos
    assert "esticada" in avisos[0]


def test_upload_e_auditado_sem_o_nome_enviado(rascunho, admin_user):
    servicos.set_background(
        rascunho, upload(nome="segredo-do-usuario.png"), actor=admin_user
    )

    evento = AuditLog.objects.filter(
        event=AuditEvent.CERTIFICATE_TEMPLATE_BACKGROUND_SET
    ).first()
    assert evento is not None
    assert evento.metadata["format"] == "PNG"
    assert "segredo-do-usuario" not in str(evento.metadata)


# ---------------------------------------------------------------------------
# Campos: listas brancas e faixas
# ---------------------------------------------------------------------------


def test_grava_campo_valido(rascunho, admin_user):
    servicos.save_fields(
        rascunho,
        {FieldType.STUDENT_NAME: campo_valido(x=25, y=40)},
        actor=admin_user,
    )

    campo = CertificateTemplateField.objects.get(template=rascunho)
    assert campo.field_type == FieldType.STUDENT_NAME
    assert float(campo.x) == 25
    assert float(campo.y) == 40


def test_recusa_fonte_fora_da_lista(rascunho, admin_user):
    """
    A fonte nao pode virar caminho de arquivo.

    "../../../etc/passwd" como font_family faria o ReportLab procurar um
    arquivo. A lista branca impede a pergunta.
    """
    for fonte in ("Comic Sans", "../../../etc/passwd", "", "Helvetica ; rm -rf"):
        with pytest.raises(DomainError) as erro:
            servicos.save_fields(
                rascunho,
                {FieldType.STUDENT_NAME: campo_valido(font_family=fonte)},
                actor=admin_user,
            )
        assert "Fonte nao permitida" in str(erro.value)

    assert not CertificateTemplateField.objects.filter(template=rascunho).exists()


def test_recusa_cor_que_nao_e_hexadecimal(rascunho, admin_user):
    """Cor e o unico campo livre que chega perto de virar CSS."""
    for cor in ("red", "#00", "#0000000", "red; background:url(x)", "rgb(0,0,0)"):
        with pytest.raises(DomainError) as erro:
            servicos.save_fields(
                rascunho,
                {FieldType.STUDENT_NAME: campo_valido(text_color=cor)},
                actor=admin_user,
            )
        assert "#RRGGBB" in str(erro.value)


def test_recusa_alinhamento_invalido(rascunho, admin_user):
    with pytest.raises(DomainError):
        servicos.save_fields(
            rascunho,
            {FieldType.STUDENT_NAME: campo_valido(text_align="JUSTIFY")},
            actor=admin_user,
        )


@pytest.mark.parametrize(
    "extras",
    [
        {"x": -1},
        {"x": 101},
        {"y": -0.5},
        {"y": 100.5},
        {"width": 0},
        {"height": 0},
        {"width": 101},
    ],
)
def test_recusa_coordenada_fora_da_pagina(rascunho, admin_user, extras):
    with pytest.raises(DomainError):
        servicos.save_fields(
            rascunho,
            {FieldType.STUDENT_NAME: campo_valido(**extras)},
            actor=admin_user,
        )


@pytest.mark.parametrize("tamanho", [5, 121, 0, -3])
def test_recusa_tamanho_de_fonte_fora_da_faixa(rascunho, admin_user, tamanho):
    with pytest.raises(DomainError):
        servicos.save_fields(
            rascunho,
            {FieldType.STUDENT_NAME: campo_valido(font_size=tamanho)},
            actor=admin_user,
        )


def test_recusa_minimo_maior_que_o_tamanho(rascunho, admin_user):
    """
    A faixa invertida faria o laco de auto-ajuste nao ter para onde ir.

    O renderizador comeca no maior e desce ate o minimo; com o minimo acima
    do tamanho, nao existe passo valido.
    """
    with pytest.raises(DomainError) as erro:
        servicos.save_fields(
            rascunho,
            {FieldType.STUDENT_NAME: campo_valido(font_size=10, min_font_size=20)},
            actor=admin_user,
        )

    assert "minimo" in str(erro.value).lower()


@pytest.mark.parametrize("rotacao", [361, -361, 1000])
def test_recusa_rotacao_fora_da_faixa(rascunho, admin_user, rotacao):
    with pytest.raises(DomainError):
        servicos.save_fields(
            rascunho,
            {FieldType.STUDENT_NAME: campo_valido(rotation=rotacao)},
            actor=admin_user,
        )


def test_aceita_rotacao_do_ano_vertical(rascunho, admin_user):
    """O modelo oficial imprime o ano girado na lateral."""
    for rotacao in (90, -90):
        servicos.save_fields(
            rascunho, {FieldType.YEAR: campo_valido(rotation=rotacao)},
            actor=admin_user,
        )
        campo = CertificateTemplateField.objects.get(
            template=rascunho, field_type=FieldType.YEAR
        )
        assert campo.rotation == rotacao


def test_recusa_tipo_de_campo_desconhecido(rascunho, admin_user):
    """
    Nao existe placeholder livre.

    Este e o teste que garante que o administrador nao consegue pedir
    `{{qualquer_coisa}}` e o sistema resolver por introspeccao.
    """
    with pytest.raises(DomainError) as erro:
        servicos.save_fields(
            rascunho, {"ATTEMPT": campo_valido()}, actor=admin_user
        )

    assert "desconhecido" in str(erro.value).lower()


def test_um_valor_invalido_nao_grava_os_anteriores(rascunho, admin_user):
    """Transacao unica: tudo ou nada."""
    with pytest.raises(DomainError):
        servicos.save_fields(
            rascunho,
            {
                FieldType.STUDENT_NAME: campo_valido(),
                FieldType.YEAR: campo_valido(font_family="Inexistente"),
            },
            actor=admin_user,
        )

    assert not CertificateTemplateField.objects.filter(template=rascunho).exists()


def test_tipo_ausente_e_removido(rascunho, admin_user):
    servicos.save_fields(
        rascunho,
        {
            FieldType.STUDENT_NAME: campo_valido(),
            FieldType.YEAR: campo_valido(),
        },
        actor=admin_user,
    )
    assert CertificateTemplateField.objects.filter(template=rascunho).count() == 2

    servicos.save_fields(
        rascunho, {FieldType.STUDENT_NAME: campo_valido()}, actor=admin_user
    )

    tipos = set(
        CertificateTemplateField.objects.filter(template=rascunho).values_list(
            "field_type", flat=True
        )
    )
    assert tipos == {FieldType.STUDENT_NAME}


# ---------------------------------------------------------------------------
# Constraints do banco
# ---------------------------------------------------------------------------


def test_banco_recusa_cor_invalida(rascunho, admin_user):
    """A camada que sobrevive a um UPDATE direto."""
    from django.db import IntegrityError, transaction

    servicos.save_fields(
        rascunho, {FieldType.STUDENT_NAME: campo_valido()}, actor=admin_user
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            # Sete caracteres: cabe na coluna, entao quem recusa e a
            # CheckConstraint, e nao o limite de tamanho. Um valor longo
            # falharia por outro motivo e nao testaria a regra.
            CertificateTemplateField.objects.filter(template=rascunho).update(
                text_color="#GGGGGG"
            )


def test_banco_recusa_ativo_sem_arte(rascunho):
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CertificateTemplate.objects.filter(pk=rascunho.pk).update(
                status=TemplateStatus.ACTIVE
            )


def test_banco_recusa_dois_padroes_ativos(modelo_de_certificado, admin_user):
    """
    O fallback global nao pode ter dois candidatos: a emissao passaria a
    depender da ordenacao da consulta.
    """
    from django.db import IntegrityError, transaction

    outro = servicos.create_template(name="Segundo", actor=admin_user)
    servicos.set_background(outro, upload(), actor=admin_user)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CertificateTemplate.objects.filter(pk=outro.pk).update(
                status=TemplateStatus.ACTIVE, is_global=True
            )


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------


def test_nasce_em_rascunho(rascunho):
    assert rascunho.status == TemplateStatus.DRAFT
    assert rascunho.version == 1


def test_nao_ativa_sem_arte(rascunho, admin_user):
    with pytest.raises(DomainError) as erro:
        servicos.activate_template(rascunho, actor=admin_user)

    assert "arte" in str(erro.value).lower()


def test_nao_ativa_sem_campo_visivel(rascunho, admin_user):
    servicos.set_background(rascunho, upload(), actor=admin_user)

    with pytest.raises(DomainError) as erro:
        servicos.activate_template(rascunho, actor=admin_user)

    assert "campo" in str(erro.value).lower()


def test_ativar_padrao_arquiva_o_anterior(modelo_de_certificado, admin_user):
    novo = servicos.duplicate_template(modelo_de_certificado, actor=admin_user)
    novo.is_global = True
    novo.save(update_fields=["is_global"])

    ativo, substituido = servicos.activate_template(novo, actor=admin_user)

    assert ativo.status == TemplateStatus.ACTIVE
    assert substituido.pk == modelo_de_certificado.pk

    modelo_de_certificado.refresh_from_db()
    assert modelo_de_certificado.status == TemplateStatus.ARCHIVED


def test_arquivar_solta_os_modulos(modelo_de_certificado, modulo, admin_user):
    modulo.certificate_template = modelo_de_certificado
    modulo.save(update_fields=["certificate_template"])

    servicos.archive_template(modelo_de_certificado, actor=admin_user)

    modulo.refresh_from_db()
    assert modulo.certificate_template is None


def test_arquivar_nao_apaga_nada(modelo_de_certificado, admin_user):
    servicos.archive_template(modelo_de_certificado, actor=admin_user)

    modelo_de_certificado.refresh_from_db()
    assert modelo_de_certificado.status == TemplateStatus.ARCHIVED
    assert modelo_de_certificado.background
    assert modelo_de_certificado.fields.exists()


def test_arquivar_duas_vezes_levanta_conflito(modelo_de_certificado, admin_user):
    servicos.archive_template(modelo_de_certificado, actor=admin_user)

    with pytest.raises(servicos.ModeloJaArquivado):
        servicos.archive_template(modelo_de_certificado, actor=admin_user)


def test_duplicar_sobe_a_versao_e_copia_os_campos(modelo_de_certificado, admin_user):
    copia = servicos.duplicate_template(modelo_de_certificado, actor=admin_user)

    assert copia.version == modelo_de_certificado.version + 1
    assert copia.status == TemplateStatus.DRAFT
    assert copia.parent_template_id == modelo_de_certificado.pk
    assert copia.fields.count() == modelo_de_certificado.fields.count()


def test_a_copia_nunca_nasce_padrao(modelo_de_certificado, admin_user):
    """
    Duplicar nao pode trocar o certificado de todo mundo.

    Se a copia herdasse is_global e fosse ativada, o padrao institucional
    mudaria por um clique em "Duplicar".
    """
    assert modelo_de_certificado.is_global is True
    copia = servicos.duplicate_template(modelo_de_certificado, actor=admin_user)

    assert copia.is_global is False


def test_a_copia_aponta_para_a_mesma_arte(modelo_de_certificado, admin_user):
    copia = servicos.duplicate_template(modelo_de_certificado, actor=admin_user)

    assert copia.background.name == modelo_de_certificado.background.name
    assert copia.background_checksum == modelo_de_certificado.background_checksum


def test_trocar_a_arte_da_copia_nao_toca_na_original(
    modelo_de_certificado, admin_user
):
    """
    A garantia central do versionamento de arquivo.

    A v2 grava um arquivo NOVO. O da v1 continua onde estava, com o conteudo
    que sempre teve — senao um certificado antigo perderia o proprio fundo.
    """
    original = modelo_de_certificado.background.name
    copia = servicos.duplicate_template(modelo_de_certificado, actor=admin_user)

    servicos.set_background(
        copia, upload(dados=png_de_teste(cor=(10, 20, 30))), actor=admin_user
    )

    modelo_de_certificado.refresh_from_db()
    copia.refresh_from_db()

    assert modelo_de_certificado.background.name == original
    assert copia.background.name != original
    assert modelo_de_certificado.background.storage.exists(original)


# ---------------------------------------------------------------------------
# Imutabilidade
# ---------------------------------------------------------------------------


def test_modelo_em_uso_nao_aceita_alteracao(certificado, admin_user):
    template = certificado.certificate_template
    assert template is not None
    assert template.esta_em_uso()
    assert template.editavel is False

    with pytest.raises(servicos.ModeloNaoEditavel):
        servicos.update_template(
            template, name="Outro nome", actor=admin_user
        )
    with pytest.raises(servicos.ModeloNaoEditavel):
        servicos.save_fields(
            template, {FieldType.STUDENT_NAME: campo_valido()}, actor=admin_user
        )
    with pytest.raises(servicos.ModeloNaoEditavel):
        servicos.set_background(template, upload(), actor=admin_user)


def test_modelo_arquivado_nao_aceita_alteracao(modelo_de_certificado, admin_user):
    servicos.archive_template(modelo_de_certificado, actor=admin_user)

    with pytest.raises(servicos.ModeloNaoEditavel):
        servicos.update_template(
            modelo_de_certificado, name="Outro", actor=admin_user
        )


def test_modelo_em_uso_pode_ser_duplicado(certificado, admin_user):
    """O caminho de saida da imutabilidade."""
    template = certificado.certificate_template
    copia = servicos.duplicate_template(template, actor=admin_user)

    assert copia.editavel is True
    servicos.save_fields(
        copia, {FieldType.STUDENT_NAME: campo_valido(y=70)}, actor=admin_user
    )


# ---------------------------------------------------------------------------
# Snapshot e historico
# ---------------------------------------------------------------------------


def test_a_emissao_congela_a_configuracao(certificado):
    snapshot = certificado.template_snapshot

    assert snapshot
    assert snapshot["template_id"] == certificado.certificate_template_id
    assert snapshot["page_width_mm"] == 297
    assert snapshot["fields"]
    assert snapshot["background_checksum"]


def test_o_certificado_antigo_nao_muda_quando_nasce_a_v2(
    certificado, admin_user
):
    """
    A regra que o pedido chamou de "certificado imutavel".

    Emite com a v1, cria a v2 com outro layout, e o documento antigo continua
    sendo desenhado exatamente como foi.
    """
    antes = dict(certificado.template_snapshot)
    pdf_antes = certificado.attempt and None  # marcador de leitura
    del pdf_antes

    v2 = servicos.duplicate_template(
        certificado.certificate_template, actor=admin_user
    )
    servicos.save_fields(
        v2,
        {FieldType.STUDENT_NAME: campo_valido(x=1, y=1, font_size=60)},
        actor=admin_user,
    )
    v2.is_global = True
    v2.save(update_fields=["is_global"])
    servicos.activate_template(v2, actor=admin_user)

    certificado.refresh_from_db()
    assert certificado.template_snapshot == antes
    assert certificado.certificate_template_id != v2.pk


def test_o_pdf_do_certificado_antigo_continua_saindo(certificado, admin_user):
    from certificates.pdf import render_certificate_pdf

    antes = render_certificate_pdf(certificado)

    v2 = servicos.duplicate_template(
        certificado.certificate_template, actor=admin_user
    )
    servicos.save_fields(
        v2, {FieldType.STUDENT_NAME: campo_valido(font_size=60)}, actor=admin_user
    )
    v2.is_global = True
    v2.save(update_fields=["is_global"])
    servicos.activate_template(v2, actor=admin_user)

    certificado.refresh_from_db()
    depois = render_certificate_pdf(certificado)

    # Bytes nao sao comparaveis: o PDF carrega data de criacao. O que precisa
    # coincidir e o desenho, e o tamanho e um indicador direto disso.
    assert abs(len(antes) - len(depois)) < 200


def test_modelo_usado_nao_pode_ser_apagado(certificado):
    """PROTECT: o modelo faz parte do historico do documento."""
    from django.db.models import ProtectedError

    with pytest.raises(ProtectedError):
        certificado.certificate_template.delete()


# ---------------------------------------------------------------------------
# Resolucao dos valores
# ---------------------------------------------------------------------------


def test_o_resolvedor_usa_mapa_fixo_e_nao_getattr(certificado):
    """
    Um tipo desconhecido devolve vazio, e nao um atributo do objeto.

    Se o resolvedor usasse getattr, "attempt" leria a tentativa inteira e
    "_state" leria o estado interno do Django.
    """
    assert resolve_certificate_field("attempt", certificado) == ""
    assert resolve_certificate_field("_state", certificado) == ""
    assert resolve_certificate_field("student_name_snapshot", certificado) == ""
    assert resolve_certificate_field("__class__", certificado) == ""


def test_o_resolvedor_le_os_snapshots(certificado):
    assert (
        resolve_certificate_field(FieldType.STUDENT_NAME, certificado)
        == certificado.student_name_snapshot
    )
    assert (
        resolve_certificate_field(FieldType.VERIFICATION_CODE, certificado)
        == str(certificado.verification_code)
    )


def test_carga_horaria_sai_por_extenso(certificado):
    certificado.workload_hours_snapshot = 8
    assert resolve_certificate_field(FieldType.WORKLOAD, certificado) == "08 horas"

    certificado.workload_hours_snapshot = 1
    assert resolve_certificate_field(FieldType.WORKLOAD, certificado) == "1 hora"

    certificado.workload_hours_snapshot = None
    assert resolve_certificate_field(FieldType.WORKLOAD, certificado) == ""


def test_todos_os_tipos_de_texto_tem_resolvedor():
    """
    Um tipo novo no enum sem resolvedor sairia em branco no documento, em
    silencio. Este teste faz a ausencia aparecer aqui.
    """
    sem_resolvedor = {FieldType.QR_CODE, FieldType.STATIC_IMAGE}
    esperados = set(FieldType.values) - sem_resolvedor

    assert set(RESOLVEDORES.keys()) == esperados


# ---------------------------------------------------------------------------
# Emissao sem modelo
# ---------------------------------------------------------------------------


def test_sem_modelo_a_emissao_e_bloqueada(tentativa_aprovada, admin_user):
    """
    Nao volta para layout embutido.

    Arquivar o unico modelo e tentar emitir precisa recusar com mensagem, e
    nao produzir um documento com estetica que ninguem aprovou.
    """
    CertificateTemplate.objects.update(status=TemplateStatus.ARCHIVED)

    with pytest.raises(DomainError) as erro:
        issue_certificate(tentativa_aprovada, actor=admin_user)

    assert "Nenhum modelo de certificado" in str(erro.value)
    assert not Certificate.objects.exists()


def test_o_modulo_escolhe_o_proprio_modelo(
    tentativa_aprovada, modelo_de_certificado, admin_user
):
    proprio = servicos.duplicate_template(modelo_de_certificado, actor=admin_user)
    servicos.activate_template(proprio, actor=admin_user)

    modulo = tentativa_aprovada.exam.module
    modulo.certificate_template = proprio
    modulo.save(update_fields=["certificate_template"])

    certificado, _ = issue_certificate(tentativa_aprovada, actor=admin_user)

    assert certificado.certificate_template_id == proprio.pk


def test_modelo_do_modulo_arquivado_cai_no_padrao(
    tentativa_aprovada, modelo_de_certificado, admin_user
):
    """
    Emitir com modelo aposentado seria pior do que cair no padrao.

    O modulo aponta para um modelo que foi arquivado depois; a resolucao
    confere o status em vez de confiar na chave estrangeira.
    """
    outro = servicos.duplicate_template(modelo_de_certificado, actor=admin_user)
    servicos.activate_template(outro, actor=admin_user)

    modulo = tentativa_aprovada.exam.module
    modulo.certificate_template = outro
    modulo.save(update_fields=["certificate_template"])

    # Arquiva sem passar pelo servico, para nao soltar a FK: e o cenario em
    # que a chave sobreviveu ao arquivamento.
    CertificateTemplate.objects.filter(pk=outro.pk).update(
        status=TemplateStatus.ARCHIVED
    )
    modulo.refresh_from_db()

    certificado, _ = issue_certificate(tentativa_aprovada, actor=admin_user)

    assert certificado.certificate_template_id == modelo_de_certificado.pk


def test_a_emissao_audita_o_modelo(certificado):
    evento = AuditLog.objects.filter(event=AuditEvent.CERTIFICATE_ISSUED).first()

    assert evento is not None
    assert evento.metadata["template_id"] == certificado.certificate_template_id
    assert evento.metadata["template_version"] == 1
