"""
Telas administrativas de certificados.

    /admin-panel/certificados/               lista com busca e filtros
    /admin-panel/certificados/<id>/          detalhe
    /admin-panel/certificados/<id>/baixar/   PDF
    /admin-panel/certificados/<id>/revogar/  POST

Aqui a URL usa a PK. E area administrativa: nao existe aluno curioso trocando
o numero, e o codigo de verificacao continua sendo o identificador publico do
documento. Expor a PK numa tela que ja exige ADMIN nao acrescenta risco.

Revogar e POST com CSRF e exige motivo. Um GET que revogasse transformaria
qualquer link colado num chat em uma revogacao acidental.
"""

import logging

from django.contrib import messages
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from django.views.generic import ListView, TemplateView

from certificates import services
from certificates.models import Certificate, CertificateStatus
from certificates.fonts import FonteIndisponivel
from certificates.pdf import render_certificate_pdf, url_de_validacao
from common.mixins import admin_required
from common.views import PainelAdminMixin
from courses.models import Module

logger = logging.getLogger(__name__)

POR_PAGINA = 25


def _certificado_ou_404(pk):
    certificado = (
        Certificate.objects.select_related(
            "attempt",
            "attempt__student",
            "attempt__exam",
            "attempt__exam__module",
            "revoked_by",
        )
        .filter(pk=pk)
        .first()
    )
    if certificado is None:
        raise Http404("Certificado nao encontrado.")
    return certificado


class CertificateListView(PainelAdminMixin, ListView):
    """Lista paginada, com busca por aluno ou codigo."""

    template_name = "admin_panel/certificates/list.html"
    context_object_name = "pagina"
    paginate_by = POR_PAGINA
    secao = "certificados"

    def get_queryset(self):
        consulta = Certificate.objects.select_related(
            "attempt", "attempt__student", "attempt__exam", "attempt__exam__module"
        ).order_by("-issued_at")

        busca = (self.request.GET.get("q") or "").strip()
        modulo = (self.request.GET.get("modulo") or "").strip()
        situacao = (self.request.GET.get("situacao") or "").strip()

        if busca:
            # O codigo entra na busca porque e como um certificado chega ate o
            # administrador: alguem liga com o papel na mao e le o codigo.
            filtro = Q(student_name_snapshot__icontains=busca) | Q(
                attempt__student__email__icontains=busca
            )
            if _parece_uuid(busca):
                filtro = filtro | Q(verification_code=busca)
            consulta = consulta.filter(filtro)

        if modulo.isdigit():
            consulta = consulta.filter(attempt__exam__module_id=int(modulo))
        if situacao in CertificateStatus.values:
            consulta = consulta.filter(status=situacao)

        return consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["modulos"] = Module.objects.order_by("code")
        contexto["situacoes"] = CertificateStatus.choices
        contexto["busca"] = (self.request.GET.get("q") or "").strip()
        contexto["filtro_modulo"] = (self.request.GET.get("modulo") or "").strip()
        contexto["filtro_situacao"] = (self.request.GET.get("situacao") or "").strip()
        return contexto


def _parece_uuid(texto):
    import uuid

    try:
        uuid.UUID(str(texto))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


class CertificateDetailView(PainelAdminMixin, TemplateView):
    """Detalhe administrativo, com as acoes disponiveis."""

    template_name = "admin_panel/certificates/detail.html"
    secao = "certificados"

    def get(self, request, *args, **kwargs):
        self.certificado = _certificado_ou_404(kwargs["certificate_id"])
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        certificado = self.certificado
        contexto["certificado"] = certificado
        contexto["tentativa"] = certificado.attempt
        contexto["url_publica"] = url_de_validacao(certificado)
        contexto["revogado"] = certificado.status == CertificateStatus.REVOKED
        return contexto


@admin_required
def certificate_download_admin(request, certificate_id):
    """
    PDF pela rota administrativa.

    Mesma regra do aluno para documento revogado: o arquivo nao e gerado. Os
    dados historicos continuam na tela de detalhe, que e onde o administrador
    precisa deles.
    """
    certificado = _certificado_ou_404(certificate_id)

    if certificado.status == CertificateStatus.REVOKED:
        messages.error(
            request,
            "Certificado revogado nao gera PDF. Os dados historicos estao "
            "nesta tela.",
        )
        return redirect("admin_panel:certificate_detail", certificate_id=certificado.pk)

    try:
        pdf = render_certificate_pdf(certificado)
    except FonteIndisponivel as erro:
        # Mesmo raciocinio do lado do aluno: nao entregar o documento com
        # outra tipografia. Aqui quem le e o administrador, entao a mensagem
        # nomeia a familia — mas o caminho do disco continua so no log.
        logger.error(
            "Certificado %s sem fonte: %s", certificado.verification_code, erro
        )
        messages.error(
            request,
            "{} O arquivo da fonte nao esta no servidor; avise o "
            "responsavel tecnico.".format(erro),
        )
        return redirect(
            "admin_panel:certificate_detail", certificate_id=certificado.pk
        )

    resposta = HttpResponse(pdf, content_type="application/pdf")
    resposta["Content-Disposition"] = 'attachment; filename="{}"'.format(
        certificado.nome_do_arquivo
    )
    resposta["Content-Length"] = str(len(pdf))
    resposta["Cache-Control"] = "private, no-store"
    return resposta


@require_POST
@admin_required
def certificate_revoke(request, certificate_id):
    """Revoga o certificado. Motivo obrigatorio."""
    certificado = _certificado_ou_404(certificate_id)
    motivo = (request.POST.get("motivo") or "").strip()

    if not motivo:
        messages.error(request, "Informe o motivo da revogacao.")
        return redirect("admin_panel:certificate_detail", certificate_id=certificado.pk)

    _, revogado = services.revoke_certificate(
        certificado, actor=request.user, request=request, motivo=motivo
    )
    if revogado:
        messages.success(
            request,
            "Certificado revogado. O codigo continua consultavel e passa a "
            "informar a revogacao.",
        )
    else:
        messages.info(request, "Este certificado ja estava revogado.")
    return redirect("admin_panel:certificate_detail", certificate_id=certificado.pk)
