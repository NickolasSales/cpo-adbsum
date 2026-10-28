"""
Mensalidades no painel administrativo: /admin-panel/ministros/mensalidades/*.

Somente ADMIN, pelo mesmo PainelAdminMixin do resto do painel. Ministro (que
nao e User) chega aqui como anonimo e vai para o login do CPO; aluno recebe
403. O portal do ministro nao tem rota financeira nesta fase.

Toda escrita e POST com CSRF e passa por ministers.mensalidades.
"""

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import FormView, ListView, TemplateView

from audit.models import AuditLog
from common.exceptions import DomainError
from common.views import PainelAdminMixin
from ministers import mensalidades as servico
from ministers.forms_mensalidades import (
    CorrecaoPagamentoForm,
    MensalidadeEdicaoForm,
    MensalidadeForm,
    MotivoForm,
    PagamentoForm,
)
from ministers.models import (
    FeeSituation,
    Minister,
    MinisterMonthlyFee,
    MonthlyFeeStatus,
    PaymentMethod,
)
from ministers.templatetags.financeiro import brl
from ministers.services import congregacoes_cadastradas

SECAO = "mensalidades"


def _formatar_competencias(datas):
    return [(data.strftime("%Y-%m"), data.strftime("%m/%Y")) for data in datas]


def _erros_no_form(form, erro):
    for mensagem in erro.mensagens:
        form.add_error(None, mensagem)


ROTULOS_DOS_CAMPOS = {
    "amount": "Valor",
    "due_date": "Vencimento",
    "notes": "Observações",
    "paid_at": "Data do pagamento",
    "payment_method": "Forma",
    "payment_reference": "Referência",
    "status": "Situação",
}


def _valor_legivel(campo, valor):
    """O valor gravado na trilha (texto ISO, codigo) como a tela o mostra."""
    if valor in (None, ""):
        return "—"
    if campo == "amount":
        return brl(valor)
    if campo in ("due_date", "paid_at"):
        try:
            ano, mes, dia = str(valor).split("-")
            return "{}/{}/{}".format(dia, mes, ano)
        except ValueError:
            return valor
    if campo == "payment_method" and valor in PaymentMethod.values:
        return PaymentMethod(valor).label
    if campo == "status" and valor in MonthlyFeeStatus.values:
        return MonthlyFeeStatus(valor).label
    return valor


def _alteracoes(bloco):
    if not isinstance(bloco, dict):
        return []
    return [
        (ROTULOS_DOS_CAMPOS.get(campo, campo), _valor_legivel(campo, valor))
        for campo, valor in bloco.items()
    ]


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class FeeDashboardView(PainelAdminMixin, TemplateView):
    template_name = "admin_panel/ministers/fees/dashboard.html"
    secao = SECAO

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        existentes = servico.competencias_existentes()

        pedido = (self.request.GET.get("competencia") or "").strip()
        mes_corrente = timezone.localdate().replace(day=1)
        if pedido == "todas":
            competencia = None
        elif pedido:
            competencia = servico.competencia_ou_none(pedido)
        else:
            # Sem escolha: o mes corrente, mesmo que ainda nao tenha
            # mensalidade — e o mes que a secretaria esta cobrando. A
            # competencia mais recente com dados pode ser um mes futuro ja
            # gerado, e abrir nela esconderia o atraso de agora.
            competencia = mes_corrente
        if competencia is not None and competencia not in existentes:
            existentes = sorted(set(existentes) | {competencia}, reverse=True)

        consulta = MinisterMonthlyFee.objects.all()
        if competencia is not None:
            consulta = consulta.filter(reference_month=competencia)

        t = servico.totais(consulta)
        chave = competencia.strftime("%Y-%m") if competencia else ""
        lista = reverse("admin_panel:minister_fee_list")
        filtro_comp = "competencia={}&".format(chave) if chave else ""

        contexto.update(
            {
                "competencia": competencia,
                "competencia_chave": chave,
                "competencias": _formatar_competencias(existentes),
                "totais": t,
                "cards_valor": [
                    {"titulo": "Total previsto", "valor": t["previsto"], "nota": "pendentes, vencidas e pagas", "url": "{}?{}".format(lista, filtro_comp)},
                    {"titulo": "Total recebido", "valor": t["recebido"], "nota": "{} paga(s)".format(t["qtd_pagas"]), "url": "{}?{}situacao=PAID".format(lista, filtro_comp)},
                    {"titulo": "Total pendente", "valor": t["pendente"], "nota": "{} a vencer".format(t["qtd_pendentes"]), "url": "{}?{}situacao=PENDING".format(lista, filtro_comp)},
                    {"titulo": "Total vencido", "valor": t["vencido"], "nota": "{} vencida(s)".format(t["qtd_vencidas"]), "url": "{}?{}situacao=OVERDUE".format(lista, filtro_comp)},
                ],
                "cards_qtd": [
                    {"titulo": "Ministros com mensalidade", "valor": t["qtd_ministros"], "nota": "fora as canceladas"},
                    {"titulo": "Mensalidades pagas", "valor": t["qtd_pagas"], "nota": "de {} no total".format(t["qtd_total"])},
                    {"titulo": "Pendentes ou vencidas", "valor": t["qtd_pendentes"] + t["qtd_vencidas"], "nota": "{} vencida(s)".format(t["qtd_vencidas"])},
                    {"titulo": "Isentas / canceladas", "valor": t["qtd_isentas"] + t["qtd_canceladas"], "nota": "{} isenta(s), {} cancelada(s)".format(t["qtd_isentas"], t["qtd_canceladas"])},
                ],
            }
        )
        return contexto


# ---------------------------------------------------------------------------
# Lista
# ---------------------------------------------------------------------------


class FeeListView(PainelAdminMixin, ListView):
    template_name = "admin_panel/ministers/fees/list.html"
    context_object_name = "mensalidades"
    paginate_by = 25
    secao = SECAO

    def _filtros(self):
        g = self.request.GET
        return {
            "competencia": (g.get("competencia") or "").strip(),
            "situacao": (g.get("situacao") or "").strip(),
            "q": (g.get("q") or "").strip(),
            "congregacao": (g.get("congregacao") or "").strip(),
            "ministro": (g.get("ministro") or "").strip(),
        }

    def get_queryset(self):
        f = self._filtros()
        consulta = servico.mensalidades_queryset()

        competencia = servico.competencia_ou_none(f["competencia"])
        if competencia is not None:
            consulta = consulta.filter(reference_month=competencia)
        if f["situacao"] in FeeSituation.values:
            consulta = consulta.filter(servico.filtro_de_situacao(f["situacao"]))
        if f["q"]:
            consulta = consulta.filter(
                Q(minister__full_name__icontains=f["q"])
                | Q(minister__conamad_registration__icontains=f["q"])
                | Q(minister__field_registration__icontains=f["q"])
            )
        if f["congregacao"]:
            consulta = consulta.filter(minister__congregation=f["congregacao"])
        if f["ministro"].isdigit():
            consulta = consulta.filter(minister_id=int(f["ministro"]))

        return consulta.order_by("-reference_month", "minister__full_name", "id")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        f = self._filtros()
        contexto["filtros"] = f
        contexto["tem_filtro"] = any(f.values())
        contexto["competencias"] = _formatar_competencias(servico.competencias_existentes())
        contexto["situacoes"] = FeeSituation.choices
        contexto["congregacoes"] = congregacoes_cadastradas()
        contexto["hoje"] = timezone.localdate()
        if f["ministro"].isdigit():
            contexto["ministro_filtrado"] = Minister.objects.filter(pk=int(f["ministro"])).first()
        return contexto


# ---------------------------------------------------------------------------
# Ficha
# ---------------------------------------------------------------------------


class FeeDetailView(PainelAdminMixin, TemplateView):
    template_name = "admin_panel/ministers/fees/detail.html"
    secao = SECAO

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        mensalidade = get_object_or_404(servico.mensalidades_queryset(), pk=kwargs["pk"])
        contexto["mensalidade"] = mensalidade
        contexto["situacao"] = mensalidade.situacao
        eventos = list(
            AuditLog.objects.filter(entity_type="MinisterMonthlyFee", entity_id=str(mensalidade.pk))
            .select_related("actor")
            .order_by("-timestamp", "-id")
        )
        for evento in eventos:
            evento.antes = _alteracoes(evento.metadata.get("antes"))
            evento.depois = _alteracoes(evento.metadata.get("depois"))
        contexto["eventos"] = eventos
        return contexto


# ---------------------------------------------------------------------------
# Cadastro e edicao
# ---------------------------------------------------------------------------


class FeeCreateView(PainelAdminMixin, FormView):
    template_name = "admin_panel/ministers/fees/form.html"
    form_class = MensalidadeForm
    secao = SECAO

    def get_initial(self):
        inicial = {}
        ministro_id = (self.request.GET.get("ministro") or "").strip()
        if ministro_id.isdigit():
            ministro = Minister.objects.filter(pk=int(ministro_id)).first()
            if ministro is not None:
                inicial["minister"] = ministro
                # Sugestao a partir do historico do proprio ministro. Nenhum
                # valor padrao vive no codigo.
                ultimo = servico.ultimo_valor_do_ministro(ministro)
                if ultimo is not None:
                    inicial["amount"] = ultimo
        competencia = servico.competencia_ou_none(self.request.GET.get("competencia"))
        if competencia is not None:
            inicial["reference_month"] = competencia.strftime("%Y-%m")
        return inicial

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["titulo"] = "Nova mensalidade"
        return contexto

    def form_valid(self, form):
        dados = form.cleaned_data
        try:
            mensalidade = servico.create_fee(
                minister=dados["minister"],
                reference_month=dados["reference_month"],
                due_date=dados["due_date"],
                amount=dados["amount"],
                notes=dados.get("notes", ""),
                permitir_inativo=bool(dados.get("confirmar_inativo")),
                actor=self.request.user,
                request=self.request,
            )
        except DomainError as erro:
            _erros_no_form(form, erro)
            return self.form_invalid(form)
        messages.success(
            self.request,
            "Mensalidade {} de {} criada.".format(
                mensalidade.competencia_rotulo, mensalidade.minister.full_name
            ),
        )
        return redirect("admin_panel:minister_fee_detail", pk=mensalidade.pk)


class _AcaoNaMensalidade(PainelAdminMixin, FormView):
    """
    Base das telas de acao sobre UMA mensalidade: carrega a linha, mostra o
    resumo dela ao lado do formulario e devolve para a ficha.
    """

    template_name = "admin_panel/ministers/fees/action.html"
    secao = SECAO
    titulo = ""
    texto_de_apoio = ""
    botao = "Salvar"
    botao_perigoso = False
    mensagem_sucesso = ""

    def dispatch(self, request, *args, **kwargs):
        self.mensalidade = get_object_or_404(servico.mensalidades_queryset(), pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(
            {
                "mensalidade": self.mensalidade,
                "situacao": self.mensalidade.situacao,
                "titulo": self.titulo,
                "texto_de_apoio": self.texto_de_apoio,
                "botao": self.botao,
                "botao_perigoso": self.botao_perigoso,
            }
        )
        return contexto

    def aplicar(self, dados):
        raise NotImplementedError

    def form_valid(self, form):
        try:
            self.aplicar(form.cleaned_data)
        except DomainError as erro:
            _erros_no_form(form, erro)
            return self.form_invalid(form)
        messages.success(self.request, self.mensagem_sucesso)
        return redirect("admin_panel:minister_fee_detail", pk=self.mensalidade.pk)

    def _contexto_servico(self):
        return {"actor": self.request.user, "request": self.request}


class FeeUpdateView(_AcaoNaMensalidade):
    form_class = MensalidadeEdicaoForm
    titulo = "Editar mensalidade"
    texto_de_apoio = (
        "Só mensalidades pendentes são editadas. Ministro e competência não "
        "mudam: para outra competência, cancele esta e crie outra."
    )
    mensagem_sucesso = "Mensalidade alterada."

    def get_initial(self):
        m = self.mensalidade
        return {"due_date": m.due_date, "amount": m.amount, "notes": m.notes}

    def aplicar(self, dados):
        servico.update_fee(
            self.mensalidade,
            due_date=dados["due_date"],
            amount=dados["amount"],
            notes=dados.get("notes", ""),
            **self._contexto_servico(),
        )


class FeePaymentView(_AcaoNaMensalidade):
    form_class = PagamentoForm
    titulo = "Registrar pagamento"
    texto_de_apoio = "A mensalidade passa a Pago. Vale também para mensalidade vencida."
    botao = "Registrar pagamento"
    mensagem_sucesso = "Pagamento registrado."

    def get_initial(self):
        return {"paid_at": timezone.localdate()}

    def aplicar(self, dados):
        servico.register_payment(
            self.mensalidade,
            paid_at=dados["paid_at"],
            payment_method=dados["payment_method"],
            payment_reference=dados.get("payment_reference", ""),
            notes=dados.get("notes"),
            **self._contexto_servico(),
        )


class FeePaymentCorrectView(_AcaoNaMensalidade):
    form_class = CorrecaoPagamentoForm
    titulo = "Corrigir pagamento"
    texto_de_apoio = (
        "Para acertar data, forma ou referência de um pagamento já registrado. "
        "Os valores anteriores ficam na trilha de auditoria, com o motivo."
    )
    botao = "Salvar correção"
    mensagem_sucesso = "Pagamento corrigido."

    def get_initial(self):
        m = self.mensalidade
        return {
            "paid_at": m.paid_at,
            "payment_method": m.payment_method,
            "payment_reference": m.payment_reference,
        }

    def aplicar(self, dados):
        servico.correct_payment(
            self.mensalidade,
            paid_at=dados["paid_at"],
            payment_method=dados["payment_method"],
            payment_reference=dados.get("payment_reference", ""),
            reason=dados["reason"],
            **self._contexto_servico(),
        )


class _AcaoComMotivo(_AcaoNaMensalidade):
    form_class = MotivoForm
    funcao = None

    def aplicar(self, dados):
        type(self).funcao(self.mensalidade, reason=dados["reason"], **self._contexto_servico())


class FeePaymentReverseView(_AcaoComMotivo):
    funcao = servico.reverse_payment
    titulo = "Estornar pagamento"
    texto_de_apoio = (
        "A mensalidade volta a Pendente. Data, forma e referência do pagamento "
        "saem da mensalidade e ficam registradas na trilha de auditoria."
    )
    botao = "Estornar pagamento"
    botao_perigoso = True
    mensagem_sucesso = "Pagamento estornado. A mensalidade voltou a pendente."


class FeeCancelView(_AcaoComMotivo):
    funcao = servico.cancel_fee
    titulo = "Cancelar mensalidade"
    texto_de_apoio = (
        "A mensalidade não é excluída: fica cancelada, fora dos totais, e pode "
        "ser reativada."
    )
    botao = "Cancelar mensalidade"
    botao_perigoso = True
    mensagem_sucesso = "Mensalidade cancelada."


class FeeExemptView(_AcaoComMotivo):
    funcao = servico.exempt_fee
    titulo = "Isentar mensalidade"
    texto_de_apoio = "O ministro fica dispensado desta competência. Pode ser revertido."
    botao = "Isentar"
    mensagem_sucesso = "Mensalidade isentada."


class FeeReactivateView(_AcaoComMotivo):
    funcao = servico.reactivate_fee
    titulo = "Reativar mensalidade"
    texto_de_apoio = "A mensalidade volta a Pendente, com o valor e o vencimento que tinha."
    botao = "Reativar"
    mensagem_sucesso = "Mensalidade reativada."

