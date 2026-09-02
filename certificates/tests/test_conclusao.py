"""
A data de conclusao, e o que ela nao pode ser.

Um certificado e gerado sob demanda: o PDF nao fica guardado, e o aluno pode
baixar o mesmo documento hoje, semana que vem e daqui a dois anos. Se
qualquer valor impresso for calculado na hora do download, o documento muda
sozinho depois de assinado.

Estes testes cercam esse risco por tres lados:

    a data impressa e a da CORRECAO, nao a de hoje nem a da emissao
    ela e COPIA, e nao leitura da tentativa na hora de desenhar
    corrigir a tentativa ou o nome do aluno depois nao reescreve o documento
"""

import re
from datetime import timedelta

import pytest
from django.utils import timezone

from certificates.models import Certificate, FieldType
from certificates.pdf import render_certificate_pdf
from certificates.render import render_from_snapshot
from certificates.services import issue_certificate
from certificates.snapshot import (
    montar_snapshot,
    resolve_certificate_field,
    valores_de_preview,
    valores_do_certificado,
)
from common.datas import data_por_extenso
from exams.models import ExamAttempt

pytestmark = pytest.mark.django_db

POR_EXTENSO = r"^\d{2} de [a-zç]+ de \d{4}$"


def conclusao(certificado):
    return resolve_certificate_field(FieldType.COMPLETION_DATE, certificado)


# ---------------------------------------------------------------------------
# Origem da data
# ---------------------------------------------------------------------------


def test_a_data_de_conclusao_e_a_da_correcao(certificado, tentativa_aprovada):
    assert certificado.completed_at_snapshot == tentativa_aprovada.graded_at


def test_a_conclusao_nao_e_a_emissao(tentativa_aprovada, admin_user):
    """
    O caso real: a prova foi corrigida em agosto e alguem clicou em emitir
    tres semanas depois.

    Sem o snapshot, o certificado diria que o aluno concluiu no dia do
    clique — e o clique e um ato administrativo, nao academico.
    """
    correcao = timezone.now() - timedelta(days=21)
    ExamAttempt.objects.filter(pk=tentativa_aprovada.pk).update(graded_at=correcao)
    tentativa_aprovada.refresh_from_db()

    documento, criado = issue_certificate(tentativa_aprovada, actor=admin_user)

    assert criado
    assert documento.completed_at_snapshot == correcao
    assert documento.issued_at - correcao > timedelta(days=20)
    assert conclusao(documento) == data_por_extenso(correcao)


def test_a_data_sai_por_extenso(certificado):
    assert re.match(POR_EXTENSO, conclusao(certificado)), conclusao(certificado)


def test_o_certificado_sem_snapshot_cai_na_emissao(certificado):
    """
    Certificados emitidos antes desta etapa nao tem o campo.

    A alternativa seria imprimir vazio no lugar da data — ou pior, a data de
    hoje. issued_at e a data real mais proxima que esses documentos tem.
    """
    Certificate.objects.filter(pk=certificado.pk).update(completed_at_snapshot=None)
    certificado.refresh_from_db()

    assert certificado.data_de_conclusao == certificado.issued_at
    assert conclusao(certificado) == data_por_extenso(certificado.issued_at)


# ---------------------------------------------------------------------------
# O documento nao muda
# ---------------------------------------------------------------------------


def test_baixar_dias_depois_nao_muda_a_data(certificado):
    antes = conclusao(certificado)

    certificado.refresh_from_db()
    depois = conclusao(certificado)

    assert antes == depois


def test_nenhum_valor_impresso_depende_de_agora(certificado, monkeypatch):
    """
    A prova de que o documento e deterministico.

    `timezone.now` passa a explodir. Se qualquer resolvedor consultar o
    relogio para decidir o que imprimir, este teste cai — e e exatamente
    esse o defeito que faria um certificado mudar de data entre dois
    downloads.
    """

    def proibido():
        raise AssertionError(
            "O documento nao pode consultar o relogio para decidir o que imprimir."
        )

    monkeypatch.setattr(timezone, "now", proibido)

    valores = valores_do_certificado(certificado)

    assert valores[FieldType.COMPLETION_DATE]
    assert valores[FieldType.STUDENT_NAME] == certificado.student_name_snapshot


def test_corrigir_a_tentativa_depois_nao_reescreve_o_certificado(
    certificado, tentativa_aprovada
):
    original = certificado.completed_at_snapshot
    impresso = conclusao(certificado)

    ExamAttempt.objects.filter(pk=tentativa_aprovada.pk).update(
        graded_at=timezone.now() + timedelta(days=400)
    )
    certificado.refresh_from_db()

    assert certificado.completed_at_snapshot == original
    assert conclusao(certificado) == impresso


def test_renomear_o_aluno_depois_nao_reescreve_o_certificado(
    certificado, tentativa_aprovada
):
    nome_no_documento = certificado.student_name_snapshot

    aluno = tentativa_aprovada.student
    aluno.full_name = "Outro Nome Totalmente Diferente"
    aluno.save(update_fields=["full_name"])
    certificado.refresh_from_db()

    assert certificado.student_name_snapshot == nome_no_documento
    assert (
        resolve_certificate_field(FieldType.STUDENT_NAME, certificado)
        == nome_no_documento
    )
    assert nome_no_documento != aluno.full_name


def test_renomear_o_modulo_depois_nao_reescreve_o_certificado(
    certificado, tentativa_aprovada
):
    impresso = resolve_certificate_field(FieldType.MODULE_NAME, certificado)

    modulo = tentativa_aprovada.exam.module
    modulo.certificate_display_name = "Modulo Renomeado Depois"
    modulo.save(update_fields=["certificate_display_name"])
    certificado.refresh_from_db()

    assert resolve_certificate_field(FieldType.MODULE_NAME, certificado) == impresso


# ---------------------------------------------------------------------------
# O valor chega ao papel
# ---------------------------------------------------------------------------


def test_a_data_de_conclusao_e_desenhada_no_pdf(certificado, monkeypatch):
    """
    Conferir o dicionario de valores nao basta: um campo pode estar resolvido
    e nunca ser desenhado. Aqui a canvas e espionada.
    """
    from reportlab.pdfgen import canvas as modulo_canvas

    escrito = []
    original = modulo_canvas.Canvas.drawCentredString

    def espiao(self, x, y, texto, *args, **kwargs):
        escrito.append(texto)
        return original(self, x, y, texto, *args, **kwargs)

    monkeypatch.setattr(modulo_canvas.Canvas, "drawCentredString", espiao)

    render_certificate_pdf(certificado)

    assert conclusao(certificado) in escrito


def test_o_preview_mostra_a_data_no_formato_do_documento():
    """
    O preview precisa mostrar o formato real. Um preview com `17/10/2026`
    faria o administrador dimensionar a caixa para um texto curto e receber
    `02 de setembro de 2026` no documento — que nao caberia.
    """
    valor = valores_de_preview()[FieldType.COMPLETION_DATE]

    assert re.match(POR_EXTENSO, valor), valor


# ---------------------------------------------------------------------------
# A arte e a mesma nos dois caminhos
# ---------------------------------------------------------------------------


def imagens(pdf):
    """
    Bytes de cada XObject de imagem.

    Sem quebra de linha antes de `endstream`: e assim que o ReportLab
    escreve. O padrao que a exigia nao casava nada, e o teste comparava duas
    listas vazias — passando sem afirmar coisa alguma.
    """
    return re.findall(rb"/Subtype /Image.*?\nstream\n(.*?)endstream", pdf, re.S)


def so_o_fundo(snapshot):
    """
    Os bytes da arte, como o ReportLab a embute.

    Renderizar sem campo nenhum deixa uma imagem so no PDF, e ela e o fundo.
    Identificar o fundo pelo tamanho nao funcionaria: a arte de teste e uma
    cor chapada e comprime para menos que o QR, que e ruido puro.
    """
    encontradas = imagens(render_from_snapshot(dict(snapshot, fields=[]), {}))
    assert len(encontradas) == 1
    return encontradas[0]


def test_preview_e_documento_usam_a_mesma_arte(certificado, modelo_de_certificado):
    """
    O preview nao pode desenhar sobre outra coisa.

    Se a mesma sequencia de bytes aparece nos dois PDFs, o administrador
    esta posicionando sobre exatamente a arte que o aluno vai receber — e
    nao sobre uma reamostragem, uma versao anterior ou um placeholder.
    """
    arte = so_o_fundo(certificado.template_snapshot)

    documento = render_certificate_pdf(certificado)
    previa = render_from_snapshot(
        montar_snapshot(modelo_de_certificado), valores_de_preview()
    )

    assert len(arte) > 0
    assert arte in imagens(documento)
    assert arte in imagens(previa)
    # E o QR e diferente nos dois: o do preview nao aponta para certificado
    # nenhum. Sem esta linha, uma arte ausente nos dois lados passaria.
    assert set(imagens(documento)) != set(imagens(previa))


def test_o_documento_usa_a_arte_do_snapshot_e_nao_a_do_modelo_atual(
    certificado, modelo_de_certificado
):
    """
    Trocar o arquivo depois nao pode mudar um documento ja emitido.

    O snapshot guarda o caminho e o checksum da arte usada na emissao — e e
    dali que o renderizador le, e nao do modelo vivo.
    """
    assert certificado.template_snapshot["background_path"] == (
        modelo_de_certificado.background.path
    )
    assert (
        certificado.template_snapshot["background_checksum"]
        == modelo_de_certificado.background_checksum
    )
    assert certificado.template_snapshot["template_version"] == (
        modelo_de_certificado.version
    )


# ---------------------------------------------------------------------------
# A pagina publica
# ---------------------------------------------------------------------------


def test_a_validacao_publica_mostra_a_data_de_conclusao(client, certificado):
    """
    Quem confere esta com o papel na mao. Se o documento traz a data de
    conclusao e a pagina nao, nao ha o que comparar.
    """
    from django.urls import reverse

    resposta = client.get(
        reverse(
            "certificates:validate",
            kwargs={"verification_code": certificado.verification_code},
        )
    )
    corpo = resposta.content.decode()

    assert resposta.status_code == 200
    assert "Concluido em" in corpo
    assert data_por_extenso(certificado.completed_at_snapshot) in corpo


def test_a_validacao_publica_omite_a_conclusao_quando_nao_ha(client, certificado):
    """
    Certificados anteriores a este ajuste nao registraram a conclusao.
    Mostrar issued_at sob o rotulo "Concluido em" seria afirmar uma data que
    aquele documento nunca teve.
    """
    from django.urls import reverse

    Certificate.objects.filter(pk=certificado.pk).update(completed_at_snapshot=None)

    corpo = client.get(
        reverse(
            "certificates:validate",
            kwargs={"verification_code": certificado.verification_code},
        )
    ).content.decode()

    assert "Concluido em" not in corpo
    assert "Emitido em" in corpo
