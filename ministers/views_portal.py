"""
Portal do Ministro: /ministros/*.

Nenhuma rota daqui recebe id de ministro. Quem e o ministro vem da sessao
(ministers.auth.conta_autenticada), entao nao ha numero na URL para trocar e
ver o perfil de outra pessoa.

Todas as respostas autenticadas saem com no-store: sao dados pessoais, e um
computador compartilhado nao pode mostra-los pelo botao voltar depois do
logout.
"""

import datetime

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.views.generic import FormView, TemplateView

from common.exceptions import DomainError
from ministers import auth, cobranca, comprovantes, guia, pix, services
from ministers import mensalidades as financeiro
from ministers.forms import LoginMinistroForm, TrocarSenhaMinistroForm
from ministers.forms_comprovantes import ACEITOS_NO_NAVEGADOR, EnviarComprovanteForm
from ministers.models import (
    TAMANHO_MAXIMO_DO_COMPROVANTE,
    FeeSituation,
    MinisterMonthlyFee,
    ProofStatus,
)

PREFIXO_DO_PORTAL = "/ministros/"


def _destino_seguro(request, destino):
    """O next so vale se for deste site E deste portal."""
    if (
        destino
        and destino.startswith(PREFIXO_DO_PORTAL)
        and url_has_allowed_host_and_scheme(
            destino,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        return destino
    return reverse("ministers:dashboard")


@method_decorator(never_cache, name="dispatch")
class MinistroRequiredMixin:
    """
    Exige um ministro autenticado no portal.

    Sem sessao de ministro, o destino e o login do PORTAL — inclusive para um
    aluno ou administrador logado no CPO, que para o portal e um visitante.
    Com a troca de senha pendente, so a tela de troca e o logout respondem.
    """

    libera_troca_pendente = False

    def dispatch(self, request, *args, **kwargs):
        self.acesso = auth.conta_autenticada(request)
        if self.acesso is None:
            login = reverse("ministers:login")
            return redirect("{}?next={}".format(login, request.path))
        self.ministro = self.acesso.minister
        if self.acesso.must_change_password and not self.libera_troca_pendente:
            return redirect("ministers:change_password")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["ministro"] = self.ministro
        contexto["acesso"] = self.acesso
        return contexto


@method_decorator(never_cache, name="dispatch")
class LoginMinistroView(FormView):
    template_name = "ministers/portal/login.html"
    form_class = LoginMinistroForm

    def dispatch(self, request, *args, **kwargs):
        if auth.conta_autenticada(request) is not None:
            return redirect("ministers:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            acesso = auth.autenticar(
                self.request,
                form.cleaned_data["identificador"],
                form.cleaned_data["senha"],
            )
        except auth.FalhaDeLogin as falha:
            form.add_error(None, str(falha))
            return self.form_invalid(form)

        auth.entrar(self.request, acesso)
        if acesso.must_change_password:
            messages.info(
                self.request,
                "Bem-vindo! Por segurança, defina agora uma senha só sua.",
            )
            return redirect("ministers:change_password")
        return redirect(
            _destino_seguro(
                self.request,
                self.request.POST.get("next") or self.request.GET.get("next"),
            )
        )

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["next"] = self.request.GET.get("next", "")
        return contexto


@method_decorator(require_POST, name="dispatch")
class LogoutMinistroView(View):
    """Somente POST: um GET permitiria deslogar alguem por um link ou imagem."""

    def post(self, request):
        auth.sair(request)
        messages.info(request, "Você saiu do Portal do Ministro.")
        return redirect("ministers:login")


class PainelMinistroView(MinistroRequiredMixin, TemplateView):
    template_name = "ministers/portal/dashboard.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["resumo"] = financeiro.totais(financeiro.mensalidades_do_ministro(self.ministro))
        return contexto


class MensalidadesMinistroView(MinistroRequiredMixin, TemplateView):
    """
    As mensalidades do ministro da sessao. Somente leitura.

    A consulta parte de self.ministro, que o mixin tira da sessao; nada da
    URL, da query string ou do corpo entra nela. Situacao e totais sao os
    mesmos do painel administrativo (ministers.mensalidades): "Vencido"
    calculado, isentas e canceladas visiveis no historico mas fora da divida.

    Sem auditoria por visualizacao: o acesso ao portal ja fica registrado no
    login, e um evento por pagina aberta encheria a trilha sem dizer nada.
    """

    template_name = "ministers/portal/mensalidades.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        consulta = financeiro.mensalidades_do_ministro(self.ministro)
        totais = financeiro.totais(consulta)
        # Os totais vem da consulta sem anotacao: comprovante aguardando
        # analise nao tira a mensalidade do aberto. So a aprovacao tira.
        contexto["mensalidades"] = list(
            comprovantes.anotar_analise(cobranca.anotar_cobranca(consulta))
        )
        contexto["totais"] = totais
        contexto["em_aberto"] = totais["pendente"] + totais["vencido"]
        return contexto


SITUACOES_COBRAVEIS = (FeeSituation.PENDING, FeeSituation.OVERDUE)


def _mensalidade_da_competencia(ministro, ano, mes):
    """
    A mensalidade do ministro DA SESSAO naquela competencia, ou 404.

    O filtro e sempre minister=ministro: a URL so escolhe o mes. Mes que nao
    existe, competencia que o ministro nao tem e competencia de outro
    ministro dao o mesmo 404 — a resposta nao revela se alguem mais tem
    mensalidade naquele mes.
    """
    try:
        competencia = datetime.date(int(ano), int(mes), 1)
    except ValueError:
        raise Http404
    mensalidade = (
        MinisterMonthlyFee.objects.filter(minister=ministro, reference_month=competencia)
        .only(*financeiro.COLUNAS_DO_PORTAL)
        .first()
    )
    if mensalidade is None:
        raise Http404
    return mensalidade


def _cobranca_vigente(mensalidade, situacao):
    """A cobranca vigente, so se ainda houver o que pagar."""
    if situacao not in SITUACOES_COBRAVEIS:
        return None
    return (
        cobranca.vigentes()
        .filter(fee=mensalidade)
        .only("fee_id", "txid", "pix_payload", "payment_instructions_snapshot")
        .first()
    )


def _cobranca_para_pagar(mensalidade, situacao):
    """
    A cobranca que o ministro pode PAGAR agora. Com comprovante aguardando
    analise, nenhuma: o Pix e a guia somem ate a secretaria conferir, para
    que ninguem pague duas vezes enquanto espera.
    """
    atual = _cobranca_vigente(mensalidade, situacao)
    if atual is None or comprovantes.tem_comprovante_em_analise(mensalidade):
        return None
    return atual


class CobrancaMinistroView(MinistroRequiredMixin, TemplateView):
    """
    "Ver cobranca": /ministros/mensalidades/AAAA/MM/. Somente leitura.

    Pendente ou vencida com cobranca: QR Code, Copia e Cola e guia. Paga,
    isenta e cancelada: so a situacao, sem nada que convide a pagar. Abrir,
    copiar e baixar nao mudam a situacao e nao gravam nada — nem cobranca:
    a que nao existe continua nao existindo ate o administrador gerar.
    """

    template_name = "ministers/portal/cobranca.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        mensalidade = _mensalidade_da_competencia(self.ministro, kwargs["ano"], kwargs["mes"])
        situacao = mensalidade.situacao
        vigente = _cobranca_vigente(mensalidade, situacao)
        historico = comprovantes.do_portal(mensalidade)
        for numero, enviado in enumerate(historico, start=1):
            enviado.numero = numero
        aguardando = next((c for c in historico if c.status == ProofStatus.SUBMITTED), None)
        ultimo = historico[-1] if historico else None
        # Com comprovante em analise, o Pix nao e oferecido (a mesma regra de
        # _cobranca_para_pagar, que vale para a guia).
        atual = vigente if aguardando is None else None
        contexto.update(
            {
                "mensalidade": mensalidade,
                "situacao": situacao,
                "cobranca": atual,
                "tem_cobranca": vigente is not None,
                "qr_svg": pix.svg_qr(atual.pix_payload) if atual else "",
                "cobravel": situacao in SITUACOES_COBRAVEIS,
                "historico": historico,
                "aguardando": aguardando,
                "recusado": ultimo if ultimo is not None and ultimo.status == ProofStatus.REJECTED else None,
                "pode_enviar": comprovantes.motivo_para_nao_enviar(
                    mensalidade, tem_cobranca=vigente is not None, tem_em_analise=aguardando is not None
                )
                is None,
            }
        )
        return contexto


class GuiaMinistroView(MinistroRequiredMixin, View):
    """A guia em PDF da competencia, gerada na hora a partir do snapshot."""

    def get(self, request, ano, mes):
        mensalidade = _mensalidade_da_competencia(self.ministro, ano, mes)
        atual = _cobranca_para_pagar(mensalidade, mensalidade.situacao)
        if atual is None:
            raise Http404
        # A guia le o snapshot inteiro, e nao so as colunas da tela.
        return guia.resposta_da_guia(cobranca.vigentes().get(pk=atual.pk))


class EnviarComprovanteView(MinistroRequiredMixin, FormView):
    """
    /ministros/mensalidades/AAAA/MM/comprovante/. GET mostra o formulario,
    POST envia (CSRF). O formulario so tem o arquivo: valor, data, situacao
    e mensalidade nao vem do navegador. A mensalidade e a da competencia DO
    MINISTRO DA SESSAO — de outro ministro, 404.
    """

    template_name = "ministers/portal/comprovante.html"
    form_class = EnviarComprovanteForm
    # O FormView do Django trata PUT como POST. Aqui, nao.
    http_method_names = ["get", "post", "head", "options"]

    def _mensalidade(self):
        if not hasattr(self, "_atual"):
            self._atual = _mensalidade_da_competencia(
                self.ministro, self.kwargs["ano"], self.kwargs["mes"]
            )
        return self._atual

    def _url_da_cobranca(self):
        return reverse(
            "ministers:fee_charge", kwargs={"ano": self.kwargs["ano"], "mes": self.kwargs["mes"]}
        )

    def get(self, request, *args, **kwargs):
        mensalidade = self._mensalidade()
        motivo = comprovantes.motivo_para_nao_enviar(
            mensalidade,
            tem_cobranca=cobranca.cobranca_vigente(mensalidade) is not None,
            tem_em_analise=comprovantes.tem_comprovante_em_analise(mensalidade),
        )
        if motivo:
            messages.info(request, motivo)
            return redirect(self._url_da_cobranca())
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        mensalidade = self._mensalidade()
        contexto["mensalidade"] = mensalidade
        contexto["situacao"] = mensalidade.situacao
        contexto["referencia"] = cobranca.competencia_por_extenso(mensalidade.reference_month)
        contexto["url_da_cobranca"] = self._url_da_cobranca()
        contexto["aceitos"] = ACEITOS_NO_NAVEGADOR
        contexto["limite"] = TAMANHO_MAXIMO_DO_COMPROVANTE
        return contexto

    def form_valid(self, form):
        mensalidade = self._mensalidade()
        try:
            comprovantes.enviar_comprovante(
                mensalidade, form.cleaned_data["arquivo"], request=self.request
            )
        except comprovantes.EnvioRecusado as erro:
            # A situacao nao permite (paga, ou comprovante ja em analise —
            # inclusive o proprio, num clique duplo). Nada foi gravado; a
            # cobranca mostra o estado atual.
            messages.info(self.request, str(erro))
            return redirect(self._url_da_cobranca())
        except DomainError as erro:
            for mensagem in erro.mensagens:
                form.add_error("arquivo", mensagem)
            return self.form_invalid(form)
        messages.success(
            self.request,
            "Comprovante recebido! Sua mensalidade de {} está aguardando conferência. "
            "Você poderá acompanhar o status por aqui.".format(
                cobranca.competencia_por_extenso(mensalidade.reference_month)
            ),
        )
        return redirect(self._url_da_cobranca())


class ArquivoDoComprovanteView(MinistroRequiredMixin, View):
    """
    Um comprovante que o PROPRIO ministro enviou, pelo numero dentro da
    competencia (1 = o primeiro enviado). A consulta parte da mensalidade do
    ministro da sessao: o comprovante de outro ministro nao e alcancavel nem
    conhecendo o id, o nome do arquivo ou o caminho.
    """

    def get(self, request, ano, mes, numero):
        mensalidade = _mensalidade_da_competencia(self.ministro, ano, mes)
        numero = int(numero)
        if numero < 1:
            raise Http404
        enviado = (
            mensalidade.payment_proofs.order_by("submitted_at", "id")[numero - 1 : numero].first()
        )
        if enviado is None:
            raise Http404
        nome = comprovantes.nome_para_download(enviado, mensalidade.reference_month, numero)
        return comprovantes.resposta_do_arquivo(enviado, nome=nome, inline=True)


class PerfilMinistroView(MinistroRequiredMixin, TemplateView):
    """Somente leitura. Dados ministeriais sao mantidos pela secretaria."""

    template_name = "ministers/portal/profile.html"


class TrocarSenhaMinistroView(MinistroRequiredMixin, FormView):
    template_name = "ministers/portal/change_password.html"
    form_class = TrocarSenhaMinistroForm
    libera_troca_pendente = True

    def form_valid(self, form):
        try:
            acesso = services.change_own_password(
                self.acesso,
                current_password=form.cleaned_data["senha_atual"],
                new_password=form.cleaned_data["nova_senha1"],
                request=self.request,
            )
        except DomainError as erro:
            for mensagem in erro.mensagens:
                form.add_error(None, mensagem)
            return self.form_invalid(form)

        # A assinatura da sessao muda com a senha. Sem atualiza-la, o proprio
        # ministro seria deslogado logo depois de trocar.
        auth.atualizar_sessao(self.request, acesso)
        messages.success(self.request, "Senha alterada com sucesso.")
        return redirect("ministers:dashboard")
