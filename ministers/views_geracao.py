"""
Geracao de mensalidades em massa: /admin-panel/ministros/mensalidades/gerar/*.

    gerar/                   parametros (GET mostra, POST valida e guarda)
    gerar/preview/           o que aconteceria. Somente leitura
    gerar/confirmar/         POST: grava
    gerar/cancelar/          POST: descarta os parametros
    gerar/resultado/<id>/    o recibo do lote, lido do banco

Somente ADMIN. GET nunca grava. Do navegador, a confirmacao recebe so o
token do lote e a confirmacao explicita: competencia, vencimento e valor vem
da sessao do servidor e sao validados de novo; quem recebe mensalidade e
recalculado no banco, dentro da transacao.

O resultado e uma pagina propria, lida de MinisterFeeBatch: recarregar a
pagina nao gera nada, e um reenvio do POST cai no mesmo lote.
"""

from datetime import timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST
from django.views.generic import FormView, TemplateView

from common.exceptions import DomainError
from common.mixins import admin_required
from common.views import PainelAdminMixin
from ministers import geracao
from ministers.forms_mensalidades import GeracaoForm
from ministers.models import MinisterFeeBatch

SECAO = "mensalidades"
CHAVE_SESSAO = "geracao_mensalidades"
VALIDADE = timedelta(minutes=30)


def _lote_da_sessao(request):
    lote = request.session.get(CHAVE_SESSAO)
    if not lote:
        return None
    try:
        criado_em = parse_datetime(lote.get("criado_em") or "")
    except (TypeError, ValueError):
        criado_em = None
    if criado_em is None or timezone.now() - criado_em > VALIDADE:
        request.session.pop(CHAVE_SESSAO, None)
        return None
    return lote


class GeracaoView(PainelAdminMixin, FormView):
    template_name = "admin_panel/ministers/fees/gerar.html"
    form_class = GeracaoForm
    secao = SECAO

    def get_initial(self):
        inicial = {"reference_month": timezone.localdate().strftime("%Y-%m")}
        sugerido = geracao.valor_sugerido()
        if sugerido is not None:
            inicial["amount"] = sugerido
        return inicial

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["valor_sugerido"] = geracao.valor_sugerido()
        return contexto

    def form_valid(self, form):
        dados = form.cleaned_data
        try:
            parametros = geracao.validar_parametros(
                dados["reference_month"], dados["due_date"], dados["amount"]
            )
        except DomainError as erro:
            for mensagem in erro.mensagens:
                form.add_error(None, mensagem)
            return self.form_invalid(form)

        # Um token novo a cada preview. Gerar de novo a mesma competencia e
        # outra operacao, com outro token — e so cria para quem ainda nao tem.
        self.request.session[CHAVE_SESSAO] = {
            "token": geracao.novo_token(),
            "criado_em": timezone.now().isoformat(),
            **parametros.para_sessao(),
        }
        return redirect("admin_panel:minister_fee_generate_preview")


class GeracaoPreviewView(PainelAdminMixin, TemplateView):
    template_name = "admin_panel/ministers/fees/gerar_preview.html"
    secao = SECAO

    def get(self, request, *args, **kwargs):
        lote = _lote_da_sessao(request)
        if lote is None:
            messages.info(request, "Informe os dados da geração novamente.")
            return redirect("admin_panel:minister_fee_generate")
        try:
            self.parametros = geracao.parametros_da_sessao(lote)
        except DomainError as erro:
            request.session.pop(CHAVE_SESSAO, None)
            messages.error(request, str(erro))
            return redirect("admin_panel:minister_fee_generate")
        self.lote = lote
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        analise = geracao.analisar(self.parametros)
        contexto["analise"] = analise
        contexto["parametros"] = self.parametros
        contexto["token"] = self.lote["token"]
        return contexto


@require_POST
@admin_required
def geracao_confirmar(request):
    token = request.POST.get("token") or ""

    # Reenvio de uma confirmacao ja processada: mostra o mesmo recibo.
    ja_gravado = MinisterFeeBatch.objects.filter(token=token).first() if token else None
    if ja_gravado is not None:
        return redirect("admin_panel:minister_fee_generate_result", pk=ja_gravado.pk)

    lote = _lote_da_sessao(request)
    if lote is None:
        messages.info(request, "O preview expirou. Informe os dados da geração novamente.")
        return redirect("admin_panel:minister_fee_generate")
    if not constant_time_compare(token, lote["token"]):
        messages.error(
            request,
            "Esta confirmação não corresponde ao preview atual. Revise e confirme novamente.",
        )
        return redirect("admin_panel:minister_fee_generate_preview")
    if request.POST.get("confirmar") != "1":
        messages.error(request, "Confirme a geração para continuar.")
        return redirect("admin_panel:minister_fee_generate_preview")

    try:
        parametros = geracao.parametros_da_sessao(lote)
        resultado = geracao.gerar(
            parametros, token=lote["token"], actor=request.user, request=request
        )
    except DomainError as erro:
        messages.error(request, str(erro))
        return redirect("admin_panel:minister_fee_generate_preview")

    request.session.pop(CHAVE_SESSAO, None)
    return redirect("admin_panel:minister_fee_generate_result", pk=resultado.pk)


@require_POST
@admin_required
def geracao_cancelar(request):
    request.session.pop(CHAVE_SESSAO, None)
    messages.info(request, "Geração cancelada. Nenhuma mensalidade foi criada.")
    return redirect("admin_panel:minister_fee_dashboard")


class GeracaoResultadoView(PainelAdminMixin, TemplateView):
    template_name = "admin_panel/ministers/fees/gerar_resultado.html"
    secao = SECAO

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["lote"] = get_object_or_404(
            MinisterFeeBatch.objects.select_related("created_by"), pk=kwargs["pk"]
        )
        return contexto
