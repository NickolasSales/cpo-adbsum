"""
Portal do Ministro: /ministros/*.

Nenhuma rota daqui recebe id de ministro. Quem e o ministro vem da sessao
(ministers.auth.conta_autenticada), entao nao ha numero na URL para trocar e
ver o perfil de outra pessoa.

Todas as respostas autenticadas saem com no-store: sao dados pessoais, e um
computador compartilhado nao pode mostra-los pelo botao voltar depois do
logout.
"""

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.views.generic import FormView, TemplateView

from common.exceptions import DomainError
from ministers import auth, services
from ministers import mensalidades as financeiro
from ministers.forms import LoginMinistroForm, TrocarSenhaMinistroForm

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
        contexto["mensalidades"] = list(consulta)
        contexto["totais"] = totais
        contexto["em_aberto"] = totais["pendente"] + totais["vencido"]
        return contexto


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
