"""
Telas de contas administrativas.

    /admin-panel/administradores/                     lista
    /admin-panel/administradores/novo/                criar
    /admin-panel/administradores/<id>/                detalhe
    /admin-panel/administradores/<id>/editar/         editar
    /admin-panel/administradores/<id>/resetar-senha/  redefinir senha
    /admin-panel/administradores/<id>/bloquear/       POST
    /admin-panel/administradores/<id>/desbloquear/    POST

Todas exigem ADMIN. Um STUDENT autenticado recebe 403 — nao 404 —, porque a
existencia da area administrativa nao e segredo; o que ele nao tem e
permissao.

Todo `<id>` passa por administrador_ou_none, que filtra por papel. Apontar uma
destas rotas para o id de um ALUNO nao encontra nada e responde 404: as rotas
de administrador nao operam sobre alunos, nem por engano nem de proposito.
"""

from django.contrib import messages
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import FormView, ListView, TemplateView

from accounts import services
from accounts.forms_admin import AdminUserForm, ResetAdminPasswordForm
from common.exceptions import DomainError
from common.mixins import admin_required
from common.views import PainelAdminMixin

POR_PAGINA = 25


def _admin_ou_404(pk):
    admin = services.administrador_ou_none(pk)
    if admin is None:
        raise Http404("Administrador nao encontrado.")
    return admin


class AdminUserListView(PainelAdminMixin, ListView):
    """Lista paginada de contas administrativas."""

    template_name = "admin_panel/admins/list.html"
    context_object_name = "pagina"
    paginate_by = POR_PAGINA
    secao = "administradores"

    def get_queryset(self):
        consulta = services.administradores().order_by("full_name")

        busca = (self.request.GET.get("q") or "").strip()
        situacao = (self.request.GET.get("situacao") or "").strip()

        if busca:
            consulta = consulta.filter(
                Q(full_name__icontains=busca) | Q(email__icontains=busca)
            )
        if situacao == "ativos":
            consulta = consulta.filter(is_active=True)
        elif situacao == "bloqueados":
            consulta = consulta.filter(is_active=False)

        return consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["busca"] = (self.request.GET.get("q") or "").strip()
        contexto["filtro_situacao"] = (self.request.GET.get("situacao") or "").strip()
        contexto["total_ativos"] = services.administradores_ativos().count()
        return contexto


class AdminUserDetailView(PainelAdminMixin, TemplateView):
    template_name = "admin_panel/admins/detail.html"
    secao = "administradores"

    def get(self, request, *args, **kwargs):
        self.admin = _admin_ou_404(kwargs["pk"])
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["admin_alvo"] = self.admin
        # A interface esconde acoes que o servico recusaria. Esconder e
        # cortesia com quem usa a tela; a recusa de verdade esta no servico.
        contexto["e_voce"] = self.admin.pk == self.request.user.pk
        contexto["e_o_ultimo_ativo"] = (
            self.admin.is_active and services.administradores_ativos().count() <= 1
        )
        contexto["e_tecnico"] = self.admin.is_superuser or self.admin.is_staff
        return contexto


class AdminUserCreateView(PainelAdminMixin, FormView):
    template_name = "admin_panel/admins/form.html"
    form_class = AdminUserForm
    secao = "administradores"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["criando"] = True
        return kwargs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["titulo"] = "Novo administrador"
        contexto["criando"] = True
        return contexto

    def form_valid(self, form):
        try:
            admin = services.create_admin_user(
                full_name=form.cleaned_data["full_name"],
                email=form.cleaned_data["email"],
                password=form.senha,
                actor=self.request.user,
                request=self.request,
            )
        except DomainError as erro:
            for mensagem in erro.mensagens:
                form.add_error(None, mensagem)
            return self.form_invalid(form)

        messages.success(
            self.request,
            "{} agora tem acesso administrativo. Entregue a senha por canal "
            "seguro.".format(admin.full_name),
        )
        return redirect("admin_panel:admin_user_detail", pk=admin.pk)


class AdminUserUpdateView(PainelAdminMixin, FormView):
    template_name = "admin_panel/admins/form.html"
    form_class = AdminUserForm
    secao = "administradores"

    def dispatch(self, request, *args, **kwargs):
        self.admin = _admin_ou_404(kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {"full_name": self.admin.full_name, "email": self.admin.email}

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["titulo"] = "Editar administrador"
        contexto["criando"] = False
        contexto["admin_alvo"] = self.admin
        return contexto

    def form_valid(self, form):
        try:
            services.update_admin_user(
                self.admin,
                full_name=form.cleaned_data["full_name"],
                email=form.cleaned_data["email"],
                actor=self.request.user,
                request=self.request,
            )
        except DomainError as erro:
            for mensagem in erro.mensagens:
                form.add_error(None, mensagem)
            return self.form_invalid(form)

        messages.success(self.request, "Dados atualizados.")
        return redirect("admin_panel:admin_user_detail", pk=self.admin.pk)


class AdminUserPasswordResetView(PainelAdminMixin, FormView):
    template_name = "admin_panel/admins/reset_password.html"
    form_class = ResetAdminPasswordForm
    secao = "administradores"

    def dispatch(self, request, *args, **kwargs):
        self.admin = _admin_ou_404(kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["admin_alvo"] = self.admin
        contexto["e_voce"] = self.admin.pk == self.request.user.pk
        return contexto

    def form_valid(self, form):
        try:
            services.reset_admin_password(
                self.admin,
                new_password=form.cleaned_data["password1"],
                actor=self.request.user,
                request=self.request,
            )
        except DomainError as erro:
            for mensagem in erro.mensagens:
                form.add_error("password1", mensagem)
            return self.form_invalid(form)

        # Redefinir a propria senha derruba a propria sessao: o hash muda e a
        # chave de sessao deixa de validar. Avisar antes de o proximo clique
        # cair no login evita parecer erro do sistema.
        if self.admin.pk == self.request.user.pk:
            messages.info(
                self.request,
                "Sua senha foi redefinida. Entre novamente com a nova senha.",
            )
            return redirect(reverse("accounts:login"))

        messages.success(
            self.request,
            "Senha de {} redefinida. As sessoes abertas com a senha anterior "
            "deixaram de valer.".format(self.admin.full_name),
        )
        return redirect("admin_panel:admin_user_detail", pk=self.admin.pk)


@require_POST
@admin_required
def admin_user_block(request, pk):
    admin = _admin_ou_404(pk)
    try:
        services.block_admin_user(admin, actor=request.user, request=request)
    except DomainError as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, "{} foi bloqueado.".format(admin.full_name))
    return _voltar(request, admin)


def _voltar(request, admin):
    """
    Volta para a tela de onde veio, ou para o detalhe.

    Somente caminhos internos: um "proximo" absoluto vindo do POST viraria
    redirecionamento aberto — bastaria um formulario em outro site apontando
    para ca com proximo=https://... para usar o dominio da instituicao como
    trampolim.
    """
    destino = (request.POST.get("proximo") or "").strip()
    if destino.startswith("/") and not destino.startswith("//"):
        return redirect(destino)
    return redirect("admin_panel:admin_user_detail", pk=admin.pk)


@require_POST
@admin_required
def admin_user_unblock(request, pk):
    admin = _admin_ou_404(pk)
    try:
        services.unblock_admin_user(admin, actor=request.user, request=request)
    except DomainError as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, "{} foi desbloqueado.".format(admin.full_name))
    return redirect("admin_panel:admin_user_detail", pk=admin.pk)
