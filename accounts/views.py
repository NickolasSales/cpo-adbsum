"""Views de autenticacao."""

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import PermissionDenied
from django.shortcuts import resolve_url
from django.urls import reverse_lazy
from django.views.generic import FormView

from accounts.forms import EmailAuthenticationForm, TrocarSenhaForm
from audit.models import AuditEvent
from audit.services import record
from common.navigation import url_do_painel


class EntrarView(LoginView):
    """
    Tela de login.

    O registro de LOGIN_SUCCESS e LOGIN_FAILED nao acontece aqui: fica nos
    signals de autenticacao (accounts/signals.py), que cobrem tambem o
    /django-admin/ e qualquer outro caminho de login.
    """

    template_name = "accounts/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        # get_redirect_url ja valida o parametro next contra host e esquema,
        # entao um next apontando para fora do site e descartado pelo Django.
        destino = self.get_redirect_url()
        return destino or resolve_url(url_do_painel(self.request.user))


class SairView(LogoutView):
    """
    Encerra a sessao.

    Aceita apenas POST, que e o comportamento do Django 5: um GET permitiria
    deslogar o usuario por meio de uma imagem ou link em outro site.
    """

    next_page = reverse_lazy("accounts:login")


class TrocarSenhaView(LoginRequiredMixin, FormView):
    """
    Troca de senha do usuario autenticado. Somente ADMIN.

    A partir da Etapa 5 a senha do aluno e definida pelo administrador, e o
    aluno nao a altera. A rota continua existindo porque o ADMIN continua
    trocando a propria senha por aqui; para o STUDENT ela responde 403.

    403 e nao 404: a rota existe e o aluno sabe que existe — ela e o mesmo
    endereco que ele usava antes. O que mudou foi a permissao, e e isso que a
    resposta precisa dizer. Um 404 sugeriria que a tela sumiu e produziria
    chamado de suporte por um comportamento que e intencional.

    O link para esta tela tambem foi removido da area do aluno; o 403 e a
    barreira de verdade, porque esconder o link nao impede ninguem de digitar
    o endereco.
    """

    template_name = "accounts/change_password.html"
    form_class = TrocarSenhaForm

    def dispatch(self, request, *args, **kwargs):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_student:
            raise PermissionDenied(
                "A senha dos alunos e definida pela administracao."
            )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["troca_obrigatoria"] = self.request.user.must_change_password
        return contexto

    def form_valid(self, form):
        user = form.save()

        if user.must_change_password:
            user.must_change_password = False
            user.save(update_fields=["must_change_password"])

        # Sem isto o proprio usuario seria deslogado, porque a sessao carrega
        # um hash derivado da senha antiga.
        update_session_auth_hash(self.request, user)

        # Nem a senha anterior nem a nova entram na metadata.
        record(
            AuditEvent.PASSWORD_CHANGED,
            request=self.request,
            actor=user,
            student=user if user.is_student else None,
            entity_type="User",
            entity_id=user.pk,
        )

        messages.success(self.request, "Senha alterada com sucesso.")
        return super().form_valid(form)

    def get_success_url(self):
        return resolve_url(url_do_painel(self.request.user))
