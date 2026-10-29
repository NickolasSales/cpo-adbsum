"""
Comprovantes no painel administrativo (Fase 2E).

    ministros/comprovantes/                    fila de conferencia (GET)
    ministros/comprovantes/<id>/               o comprovante e o historico (GET)
    ministros/comprovantes/<id>/arquivo/       o arquivo, para ver na tela (GET)
    ministros/comprovantes/<id>/baixar/        o arquivo, como download (GET)
    ministros/comprovantes/<id>/aprovar/       aprova e baixa a mensalidade (POST)
    ministros/comprovantes/<id>/recusar/       recusa com motivo (POST)

Somente ADMIN (PainelAdminMixin): aluno recebe 403, ministro — que nao e
User — chega como anonimo e vai para o login do CPO. Aqui o id na URL e
aceitavel: quem chega e administrador e ve todos. Aprovar e recusar sao POST
com CSRF; GET neles da 405.
"""

import datetime

from django.contrib import messages
from django.db.models import Case, F, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.generic import ListView, TemplateView

from common.exceptions import DomainError
from common.views import PainelAdminMixin
from ministers import comprovantes as servico
from ministers import mensalidades as financeiro
from ministers.forms_comprovantes import AprovarComprovanteForm, RecusarComprovanteForm
from ministers.models import MinisterPaymentProof, MonthlyFeeStatus, ProofStatus

SECAO = "ministros_comprovantes"

# "todas" e explicito: sem filtro nenhum, a tela abre na fila operacional.
SITUACAO_PADRAO = ProofStatus.SUBMITTED
TODAS = "todas"


def _data_ou_none(texto):
    try:
        return datetime.date.fromisoformat((texto or "").strip())
    except ValueError:
        return None


def _comprovantes():
    return MinisterPaymentProof.objects.select_related("fee__minister", "reviewed_by")


# Sem cache: comprovante e dado pessoal e financeiro. Depois do logout, o
# voltar do navegador nao pode reexibir a fila nem o arquivo.
@method_decorator(never_cache, name="dispatch")
class ComprovantesView(PainelAdminMixin, ListView):
    """
    A fila. Por padrao, so os que aguardam analise, do mais antigo para o
    mais novo: o que chegou primeiro nao fica esquecido no fim da lista.
    Uma consulta para a pagina inteira (mensalidade, ministro e quem analisou
    no mesmo SELECT); o arquivo nao e lido.
    """

    template_name = "admin_panel/ministers/proofs/list.html"
    context_object_name = "comprovantes"
    paginate_by = 25
    secao = SECAO

    def _filtros(self):
        g = self.request.GET
        situacao = (g.get("situacao") or "").strip()
        if situacao not in ProofStatus.values and situacao != TODAS:
            situacao = SITUACAO_PADRAO
        return {
            "q": (g.get("q") or "").strip(),
            "competencia": (g.get("competencia") or "").strip(),
            "situacao": situacao,
            "enviado_de": (g.get("enviado_de") or "").strip(),
            "enviado_ate": (g.get("enviado_ate") or "").strip(),
        }

    def get_queryset(self):
        f = self._filtros()
        consulta = _comprovantes()
        if f["situacao"] != TODAS:
            consulta = consulta.filter(status=f["situacao"])
        if f["q"]:
            consulta = consulta.filter(
                Q(fee__minister__full_name__icontains=f["q"])
                | Q(fee__minister__conamad_registration__icontains=f["q"])
                | Q(fee__minister__field_registration__icontains=f["q"])
            )
        competencia = financeiro.competencia_ou_none(f["competencia"])
        if competencia is not None:
            consulta = consulta.filter(fee__reference_month=competencia)
        de, ate = _data_ou_none(f["enviado_de"]), _data_ou_none(f["enviado_ate"])
        if de is not None:
            consulta = consulta.filter(submitted_at__date__gte=de)
        if ate is not None:
            consulta = consulta.filter(submitted_at__date__lte=ate)

        # Aguardando primeiro, do mais antigo; depois os ja analisados, do
        # envio mais recente para o mais antigo.
        return consulta.annotate(
            prioridade=Case(
                When(status=ProofStatus.SUBMITTED, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
            fila=Case(When(status=ProofStatus.SUBMITTED, then=F("submitted_at"))),
        ).order_by("prioridade", F("fila").asc(nulls_last=True), "-submitted_at", "-id")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        f = self._filtros()
        contexto["filtros"] = f
        contexto["tem_filtro"] = (
            f["situacao"] != SITUACAO_PADRAO
            or any(f[chave] for chave in ("q", "competencia", "enviado_de", "enviado_ate"))
        )
        contexto["situacoes"] = ProofStatus.choices
        contexto["todas"] = TODAS
        contexto["competencias"] = [
            (data.strftime("%Y-%m"), data.strftime("%m/%Y"))
            for data in financeiro.competencias_existentes()
        ]
        contexto["aguardando"] = servico.quantos_em_analise()
        return contexto


@method_decorator(never_cache, name="dispatch")
class _ComprovanteBase(PainelAdminMixin, TemplateView):
    template_name = "admin_panel/ministers/proofs/detail.html"
    secao = SECAO

    def _comprovante(self):
        return get_object_or_404(_comprovantes(), pk=self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        comprovante = kwargs.get("comprovante") or self._comprovante()
        mensalidade = comprovante.fee
        historico = list(
            MinisterPaymentProof.objects.filter(fee=mensalidade)
            .select_related("reviewed_by")
            .order_by("submitted_at", "id")
        )
        decidir = (
            comprovante.status == ProofStatus.SUBMITTED
            and mensalidade.status == MonthlyFeeStatus.PENDING
        )
        enviado_em = timezone.localdate(comprovante.submitted_at)
        contexto.update(
            {
                "comprovante": comprovante,
                "mensalidade": mensalidade,
                "ministro": mensalidade.minister,
                "situacao": mensalidade.situacao,
                "historico": historico,
                "decidir": decidir,
                "form_aprovar": kwargs.get("form_aprovar")
                or AprovarComprovanteForm(initial={"paid_at": enviado_em}),
                "form_recusar": kwargs.get("form_recusar") or RecusarComprovanteForm(),
                "erro": kwargs.get("erro"),
            }
        )
        return contexto


class ComprovanteDetalheView(_ComprovanteBase):
    http_method_names = ["get", "head", "options"]


class _ArquivoBase(PainelAdminMixin, TemplateView):
    inline = True
    http_method_names = ["get", "head", "options"]

    def get(self, request, *args, **kwargs):
        comprovante = get_object_or_404(
            MinisterPaymentProof.objects.select_related("fee"), pk=kwargs["pk"]
        )
        nome = servico.nome_para_download(
            comprovante, comprovante.fee.reference_month, comprovante.pk
        )
        return servico.resposta_do_arquivo(comprovante, nome=nome, inline=self.inline)


class ComprovanteArquivoView(_ArquivoBase):
    inline = True


class ComprovanteDownloadView(_ArquivoBase):
    inline = False


class AprovarComprovanteView(_ComprovanteBase):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        comprovante = self._comprovante()
        form = AprovarComprovanteForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(
                self.get_context_data(comprovante=comprovante, form_aprovar=form)
            )
        try:
            servico.aprovar_comprovante(
                comprovante,
                paid_at=form.cleaned_data["paid_at"],
                actor=request.user,
                request=request,
            )
        except servico.DecisaoJaTomada as erro:
            # Outro administrador decidiu primeiro. 409, e a tela mostra o
            # estado que valeu.
            return self.render_to_response(
                self.get_context_data(comprovante=self._comprovante(), erro=str(erro)),
                status=409,
            )
        except DomainError as erro:
            for mensagem in erro.mensagens:
                form.add_error(None, mensagem)
            return self.render_to_response(
                self.get_context_data(comprovante=comprovante, form_aprovar=form)
            )
        messages.success(
            request,
            "Comprovante aprovado. A mensalidade {} de {} está paga.".format(
                comprovante.fee.competencia_rotulo, comprovante.fee.minister.full_name
            ),
        )
        return redirect("admin_panel:minister_proof_detail", pk=comprovante.pk)


class RecusarComprovanteView(_ComprovanteBase):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        comprovante = self._comprovante()
        form = RecusarComprovanteForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(
                self.get_context_data(comprovante=comprovante, form_recusar=form)
            )
        try:
            servico.recusar_comprovante(
                comprovante,
                motivo=form.cleaned_data["motivo"],
                actor=request.user,
                request=request,
            )
        except servico.DecisaoJaTomada as erro:
            return self.render_to_response(
                self.get_context_data(comprovante=self._comprovante(), erro=str(erro)),
                status=409,
            )
        except DomainError as erro:
            for mensagem in erro.mensagens:
                form.add_error(None, mensagem)
            return self.render_to_response(
                self.get_context_data(comprovante=comprovante, form_recusar=form)
            )
        messages.success(
            request,
            "Comprovante recusado. O ministro verá o motivo e poderá enviar outro.",
        )
        return redirect("admin_panel:minister_proof_detail", pk=comprovante.pk)
