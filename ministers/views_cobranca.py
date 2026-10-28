"""
Cobranca Pix no painel administrativo (Fase 2D).

    configuracoes/pagamento/                 dados de recebimento (GET/POST)
    mensalidades/cobrancas/                  resumo antes de gerar (GET)
    mensalidades/cobrancas/gerar/            gera as pendentes sem cobranca (POST)
    mensalidades/<id>/cobranca/gerar/        gera a de UMA mensalidade (POST)
    mensalidades/<id>/guia.pdf               a guia, para a secretaria enviar

Somente ADMIN. Toda escrita e POST com CSRF e passa por ministers.cobranca;
GET nunca gera cobranca.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import FormView, TemplateView

from common.exceptions import DomainError
from common.mixins import admin_required
from common.views import PainelAdminMixin
from ministers import cobranca as servico
from ministers import guia
from ministers.forms_cobranca import ConfiguracaoPagamentoForm
from ministers.models import FeeSituation, MinisterFeeCharge, MinisterMonthlyFee

SITUACOES_COBRAVEIS = (FeeSituation.PENDING, FeeSituation.OVERDUE)


@method_decorator(never_cache, name="dispatch")
class ConfiguracaoPagamentoView(PainelAdminMixin, FormView):
    template_name = "admin_panel/ministers/billing/settings.html"
    form_class = ConfiguracaoPagamentoForm
    secao = "ministros_configuracoes"

    def get_initial(self):
        atual = servico.configuracao()
        if atual is None:
            return {"is_active": True}
        return {campo: getattr(atual, campo) for campo in servico.CAMPOS_DA_CONFIGURACAO}

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        atual = servico.configuracao()
        contexto["configuracao"] = atual
        contexto["pronta"] = servico.configuracao_pronta() is not None
        if atual is not None and atual.updated_by_id:
            contexto["atualizado_por"] = atual.updated_by.full_name
        contexto["cobrancas_vigentes"] = servico.vigentes().count()
        return contexto

    def form_valid(self, form):
        try:
            _, alterou = servico.salvar_configuracao(
                form.cleaned_data, actor=self.request.user, request=self.request
            )
        except DomainError as erro:
            for mensagem in erro.mensagens:
                form.add_error(None, mensagem)
            return self.form_invalid(form)
        if alterou:
            messages.success(
                self.request,
                "Configuração de pagamento salva. Ela vale para as cobranças geradas "
                "a partir de agora; as já geradas não mudam.",
            )
        else:
            messages.info(self.request, "Nada foi alterado.")
        return redirect("admin_panel:minister_billing_settings")


class CobrancasView(PainelAdminMixin, TemplateView):
    """O retrato antes de gerar em lote. Somente leitura."""

    template_name = "admin_panel/ministers/billing/charges.html"
    secao = "mensalidades"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["resumo"] = servico.resumo()
        contexto["pronta"] = servico.configuracao_pronta() is not None
        contexto["motivo"] = servico.motivo_sem_configuracao()
        return contexto


@require_POST
@admin_required
def cobrancas_gerar(request):
    if request.POST.get("confirmar") != "1":
        messages.error(request, "Confirme a geração para continuar.")
        return redirect("admin_panel:minister_charges")
    try:
        criadas = servico.gerar_cobrancas_pendentes(actor=request.user, request=request)
    except DomainError as erro:
        messages.error(request, str(erro))
        return redirect("admin_panel:minister_charges")
    if criadas:
        messages.success(
            request,
            "{} cobrança{} gerada{}.".format(
                criadas, "s" if criadas > 1 else "", "s" if criadas > 1 else ""
            ),
        )
    else:
        messages.info(request, "Nenhuma mensalidade pendente estava sem cobrança.")
    return redirect("admin_panel:minister_charges")


@require_POST
@admin_required
def cobranca_gerar(request, pk):
    mensalidade = get_object_or_404(MinisterMonthlyFee, pk=pk)
    try:
        cobranca, criada = servico.gerar_cobranca(mensalidade, actor=request.user, request=request)
    except DomainError as erro:
        messages.error(request, str(erro))
    else:
        if criada:
            messages.success(request, "Cobrança gerada. TXID {}.".format(cobranca.txid))
        else:
            messages.info(request, "Esta mensalidade já tinha cobrança. Nada foi alterado.")
    return redirect("admin_panel:minister_fee_detail", pk=pk)


@require_GET
@never_cache
@admin_required
def guia_admin(request, pk):
    """
    A mesma guia que o ministro baixa, para a secretaria enviar por fora.
    So existe enquanto ha o que pagar: pendente ou vencida, com cobranca.
    """
    mensalidade = get_object_or_404(MinisterMonthlyFee, pk=pk)
    if mensalidade.situacao not in SITUACOES_COBRAVEIS:
        raise Http404
    cobranca = servico.cobranca_vigente(mensalidade)
    if cobranca is None:
        raise Http404
    return guia.resposta_da_guia(cobranca)


def cobranca_da_ficha(mensalidade):
    """O bloco "Cobranca" da ficha administrativa da mensalidade."""
    vigente = (
        MinisterFeeCharge.objects.select_related("generated_by")
        .filter(fee=mensalidade, superseded_at__isnull=True)
        .first()
    )
    substituidas = list(
        MinisterFeeCharge.objects.filter(fee=mensalidade, superseded_at__isnull=False)
        .only("txid", "generated_at", "superseded_at", "amount_snapshot")
        .order_by("-superseded_at")
    )
    return {
        "cobranca": vigente,
        "cobrancas_substituidas": substituidas,
        "pode_gerar_cobranca": vigente is None and servico.pode_ter_cobranca(mensalidade),
        "cobranca_pronta": servico.configuracao_pronta() is not None,
        "cobranca_motivo": servico.motivo_sem_configuracao(),
        "guia_disponivel": vigente is not None and mensalidade.situacao in SITUACOES_COBRAVEIS,
    }
