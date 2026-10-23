"""
Telas administrativas do Controle de Ministros: /admin-panel/ministros/*.

Mesmo painel, mesmo ADMIN e mesma protecao do CPO: tudo herda de
PainelAdminMixin ou usa admin_required. Aluno recebe 403; ministro, que nao e
User, chega aqui como anonimo e vai para o login do CPO.

As views validam a requisicao, chamam um servico e apresentam o resultado.
Toda acao que altera estado e POST com CSRF.
"""

import secrets
from datetime import timedelta

from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, FormView, ListView, TemplateView

from audit.models import AuditLog
from common.exceptions import DomainError
from common.mixins import admin_required
from common.views import PainelAdminMixin
from ministers import services
from ministers.forms import DefinirSenhaForm, ImportUploadForm, MinisterForm
from ministers.importers import analisar, confirmar, ler_arquivo
from ministers.models import Minister, MinisterAccount, MinisterialTitle

SECAO_DASHBOARD = "ministros_dashboard"
SECAO_CADASTRO = "ministros"

CHAVE_SESSAO_IMPORTACAO = "importacao_ministros"
VALIDADE_IMPORTACAO = timedelta(minutes=30)


def _voltar(request, padrao, **kwargs):
    """Volta para a tela de origem, se ela for deste site; senao, o padrao."""
    destino = request.POST.get("proximo") or ""
    if destino and url_has_allowed_host_and_scheme(
        destino, allowed_hosts={request.get_host()}
    ):
        return redirect(destino)
    return redirect(padrao, **kwargs)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class MinisterDashboardView(PainelAdminMixin, TemplateView):
    template_name = "admin_panel/ministers/dashboard.html"
    secao = SECAO_DASHBOARD

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        # Uma consulta so para todos os numeros.
        numeros = Minister.objects.aggregate(
            total=Count("id"),
            ativos=Count("id", filter=Q(is_active=True)),
            inativos=Count("id", filter=Q(is_active=False)),
            com_acesso=Count("id", filter=Q(account__isnull=False)),
            sem_acesso=Count("id", filter=Q(account__isnull=True)),
            acesso_bloqueado=Count("id", filter=Q(account__is_active=False)),
            troca_pendente=Count("id", filter=Q(account__must_change_password=True)),
        )
        contexto["numeros"] = numeros
        contexto["cards"] = [
            {"titulo": "Total de ministros", "valor": numeros["total"], "nota": "na relação", "filtro": ""},
            {"titulo": "Ativos", "valor": numeros["ativos"], "nota": "cadastro ativo", "filtro": "situacao=ativos"},
            {"titulo": "Inativos", "valor": numeros["inativos"], "nota": "cadastro inativo", "filtro": "situacao=inativos"},
            {"titulo": "Com acesso criado", "valor": numeros["com_acesso"], "nota": "{} bloqueado(s)".format(numeros["acesso_bloqueado"]), "filtro": "acesso=com"},
            {"titulo": "Sem acesso criado", "valor": numeros["sem_acesso"], "nota": "ainda não entram no portal", "filtro": "acesso=sem"},
        ]
        contexto["senha_padrao_configurada"] = services.senha_padrao_configurada()
        return contexto


# ---------------------------------------------------------------------------
# Lista
# ---------------------------------------------------------------------------


class MinisterListView(PainelAdminMixin, ListView):
    template_name = "admin_panel/ministers/list.html"
    context_object_name = "ministros"
    paginate_by = 25
    secao = SECAO_CADASTRO

    def _filtros(self):
        g = self.request.GET
        return {
            "q": (g.get("q") or "").strip(),
            "congregacao": (g.get("congregacao") or "").strip(),
            "titulo": (g.get("titulo") or "").strip(),
            "situacao": (g.get("situacao") or "").strip(),
            "acesso": (g.get("acesso") or "").strip(),
        }

    def get_queryset(self):
        f = self._filtros()
        consulta = services.ministros_queryset()

        if f["q"]:
            consulta = consulta.filter(
                Q(full_name__icontains=f["q"])
                | Q(conamad_registration__icontains=f["q"])
                | Q(field_registration__icontains=f["q"])
            )
        if f["congregacao"]:
            consulta = consulta.filter(congregation=f["congregacao"])
        if f["titulo"] in MinisterialTitle.values:
            consulta = consulta.filter(ministerial_title=f["titulo"])
        if f["situacao"] == "ativos":
            consulta = consulta.filter(is_active=True)
        elif f["situacao"] == "inativos":
            consulta = consulta.filter(is_active=False)
        if f["acesso"] == "com":
            consulta = consulta.filter(account__isnull=False)
        elif f["acesso"] == "sem":
            consulta = consulta.filter(account__isnull=True)
        elif f["acesso"] == "bloqueado":
            consulta = consulta.filter(account__is_active=False)

        return consulta.order_by("full_name", "id")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        filtros = self._filtros()
        contexto["filtros"] = filtros
        contexto["tem_filtro"] = any(filtros.values())
        contexto["congregacoes"] = services.congregacoes_cadastradas()
        contexto["titulos"] = MinisterialTitle.choices
        contexto["total_geral"] = Minister.objects.count()
        return contexto


# ---------------------------------------------------------------------------
# Ficha
# ---------------------------------------------------------------------------


class MinisterDetailView(PainelAdminMixin, DetailView):
    template_name = "admin_panel/ministers/detail.html"
    context_object_name = "ministro"
    secao = SECAO_CADASTRO

    def get_queryset(self):
        return services.ministros_queryset()

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["acesso"] = MinisterAccount.objects.filter(minister=self.object).first()
        contexto["eventos"] = (
            AuditLog.objects.filter(entity_type="Minister", entity_id=str(self.object.pk))
            .select_related("actor")
            .order_by("-timestamp", "-id")[:10]
        )
        return contexto


# ---------------------------------------------------------------------------
# Cadastro e edicao
# ---------------------------------------------------------------------------


class MinisterCreateView(PainelAdminMixin, FormView):
    template_name = "admin_panel/ministers/form.html"
    form_class = MinisterForm
    secao = SECAO_CADASTRO

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["criando"] = True
        return kwargs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["titulo"] = "Novo ministro"
        contexto["congregacoes"] = services.congregacoes_cadastradas()
        return contexto

    def form_valid(self, form):
        try:
            ministro = services.create_minister(
                dados=form.dados_ministeriais(),
                password=form.senha,
                actor=self.request.user,
                request=self.request,
            )
        except DomainError as erro:
            for mensagem in erro.mensagens:
                form.add_error(None, mensagem)
            return self.form_invalid(form)

        if form.senha:
            messages.success(
                self.request,
                "Ministro {} cadastrado com acesso ao portal. Entregue a senha "
                "por um canal seguro.".format(ministro.full_name),
            )
        else:
            messages.success(self.request, "Ministro {} cadastrado.".format(ministro.full_name))
        return redirect("admin_panel:minister_detail", pk=ministro.pk)


class MinisterUpdateView(PainelAdminMixin, FormView):
    template_name = "admin_panel/ministers/form.html"
    form_class = MinisterForm
    secao = SECAO_CADASTRO

    def dispatch(self, request, *args, **kwargs):
        self.ministro = get_object_or_404(Minister, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        m = self.ministro
        return {
            "full_name": m.full_name,
            "conamad_registration": m.conamad_registration,
            "field_registration": m.field_registration,
            "ministerial_title": m.ministerial_title,
            "position": m.position,
            "congregation": m.congregation,
            "consecration_year": m.consecration_year,
            "birth_date": m.birth_date,
            "sex": m.sex,
            "notes": m.notes,
        }

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["titulo"] = "Editar ministro"
        contexto["ministro"] = self.ministro
        contexto["congregacoes"] = services.congregacoes_cadastradas()
        return contexto

    def form_valid(self, form):
        try:
            services.update_minister(
                self.ministro,
                dados=form.dados_ministeriais(),
                actor=self.request.user,
                request=self.request,
            )
        except DomainError as erro:
            for mensagem in erro.mensagens:
                form.add_error(None, mensagem)
            return self.form_invalid(form)
        messages.success(self.request, "Dados do ministro atualizados.")
        return redirect("admin_panel:minister_detail", pk=self.ministro.pk)


@require_POST
@admin_required
def minister_deactivate(request, pk):
    ministro = get_object_or_404(Minister, pk=pk)
    services.deactivate_minister(ministro, actor=request.user, request=request)
    messages.success(request, "{} foi inativado.".format(ministro.full_name))
    return _voltar(request, "admin_panel:minister_detail", pk=pk)


@require_POST
@admin_required
def minister_reactivate(request, pk):
    ministro = get_object_or_404(Minister, pk=pk)
    services.reactivate_minister(ministro, actor=request.user, request=request)
    messages.success(request, "{} foi reativado.".format(ministro.full_name))
    return _voltar(request, "admin_panel:minister_detail", pk=pk)


# ---------------------------------------------------------------------------
# Acesso ao portal
# ---------------------------------------------------------------------------


class _SenhaDoMinistroView(PainelAdminMixin, FormView):
    template_name = "admin_panel/ministers/password.html"
    form_class = DefinirSenhaForm
    secao = SECAO_CADASTRO
    titulo = ""
    texto_de_apoio = ""

    def dispatch(self, request, *args, **kwargs):
        self.ministro = get_object_or_404(Minister, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["ministro"] = self.ministro
        contexto["titulo"] = self.titulo
        contexto["texto_de_apoio"] = self.texto_de_apoio
        return contexto

    def aplicar(self, senha):
        raise NotImplementedError

    def form_valid(self, form):
        try:
            self.aplicar(form.cleaned_data["password1"])
        except DomainError as erro:
            for mensagem in erro.mensagens:
                form.add_error(None, mensagem)
            return self.form_invalid(form)
        return redirect("admin_panel:minister_detail", pk=self.ministro.pk)


class MinisterAccountCreateView(_SenhaDoMinistroView):
    titulo = "Criar acesso ao portal"
    texto_de_apoio = (
        "O login será a inscrição CONAMAD do ministro. Entregue a senha por "
        "um canal seguro: ela não é exibida de novo depois de salva."
    )

    def aplicar(self, senha):
        services.create_account(
            self.ministro, password=senha, actor=self.request.user, request=self.request
        )
        messages.success(self.request, "Acesso ao portal criado para {}.".format(self.ministro.full_name))


class MinisterPasswordResetView(_SenhaDoMinistroView):
    titulo = "Resetar senha"
    texto_de_apoio = (
        "A senha atual não é exibida — ela só existe como hash. As sessões "
        "abertas com a senha anterior deixam de valer."
    )

    def get(self, request, *args, **kwargs):
        if not MinisterAccount.objects.filter(minister=self.ministro).exists():
            messages.info(request, "Este ministro ainda não possui acesso ao portal.")
            return redirect("admin_panel:minister_detail", pk=self.ministro.pk)
        return super().get(request, *args, **kwargs)

    def aplicar(self, senha):
        services.reset_password(
            self.ministro, new_password=senha, actor=self.request.user, request=self.request
        )
        messages.success(self.request, "Senha de {} redefinida.".format(self.ministro.full_name))


@require_POST
@admin_required
def minister_account_block(request, pk):
    ministro = get_object_or_404(Minister, pk=pk)
    try:
        services.block_account(ministro, actor=request.user, request=request)
    except DomainError as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, "Acesso de {} bloqueado.".format(ministro.full_name))
    return _voltar(request, "admin_panel:minister_detail", pk=pk)


@require_POST
@admin_required
def minister_account_unblock(request, pk):
    ministro = get_object_or_404(Minister, pk=pk)
    try:
        services.unblock_account(ministro, actor=request.user, request=request)
    except DomainError as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, "Acesso de {} liberado.".format(ministro.full_name))
    return _voltar(request, "admin_panel:minister_detail", pk=pk)


# ---------------------------------------------------------------------------
# Importacao
# ---------------------------------------------------------------------------


class MinisterImportUploadView(PainelAdminMixin, FormView):
    """Upload: le e guarda as linhas cruas na sessao. Nao grava ministro."""

    template_name = "admin_panel/ministers/import_upload.html"
    form_class = ImportUploadForm
    secao = SECAO_CADASTRO

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["senha_padrao_configurada"] = services.senha_padrao_configurada()
        return contexto

    def form_valid(self, form):
        try:
            lote = ler_arquivo(form.cleaned_data["arquivo"])
        except DomainError as erro:
            form.add_error("arquivo", str(erro))
            return self.form_invalid(form)

        lote.update(
            {
                "token": secrets.token_urlsafe(16),
                "arquivo": form.cleaned_data["arquivo"].name[:200],
                "criado_em": timezone.now().isoformat(),
            }
        )
        self.request.session[CHAVE_SESSAO_IMPORTACAO] = lote
        return redirect("admin_panel:minister_import_preview")


def _lote_da_sessao(request):
    lote = request.session.get(CHAVE_SESSAO_IMPORTACAO)
    if not lote:
        return None
    try:
        criado_em = parse_datetime(lote.get("criado_em") or "")
    except (TypeError, ValueError):
        criado_em = None
    if criado_em is None or timezone.now() - criado_em > VALIDADE_IMPORTACAO:
        request.session.pop(CHAVE_SESSAO_IMPORTACAO, None)
        return None
    return lote


class MinisterImportPreviewView(PainelAdminMixin, TemplateView):
    """Mostra o que aconteceria. Estritamente somente leitura."""

    template_name = "admin_panel/ministers/import_preview.html"
    secao = SECAO_CADASTRO

    def get(self, request, *args, **kwargs):
        if _lote_da_sessao(request) is None:
            messages.info(request, "Envie o arquivo novamente para revisar a importação.")
            return redirect("admin_panel:minister_import")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        lote = _lote_da_sessao(self.request)
        analise = analisar(lote["linhas"])
        contexto["lote"] = lote
        contexto["analise"] = analise
        contexto["resumo"] = analise.resumo()
        contexto["linhas"] = analise.linhas
        contexto["senha_padrao_configurada"] = services.senha_padrao_configurada()
        return contexto


@require_POST
@admin_required
def minister_import_confirm(request):
    """
    Grava o lote. Do navegador vem apenas o token do lote e a escolha de
    criar acesso — nenhuma linha, nenhum id, nenhum status.
    """
    lote = _lote_da_sessao(request)
    if lote is None:
        messages.info(request, "A análise expirou. Envie o arquivo novamente.")
        return redirect("admin_panel:minister_import")

    if not secrets.compare_digest(request.POST.get("token") or "", lote["token"]):
        messages.error(
            request,
            "Esta confirmação não corresponde ao arquivo em análise. Revise o "
            "preview e confirme novamente.",
        )
        return redirect("admin_panel:minister_import_preview")

    criar_acesso = request.POST.get("criar_acesso") == "1"
    try:
        resultado = confirmar(
            lote["linhas"], criar_acesso=criar_acesso, actor=request.user, request=request
        )
    except DomainError as erro:
        messages.error(request, str(erro))
        return redirect("admin_panel:minister_import_preview")

    request.session.pop(CHAVE_SESSAO_IMPORTACAO, None)
    messages.success(
        request,
        "Importação concluída: {} ministro(s) cadastrado(s), {} acesso(s) "
        "criado(s). {} já existiam e {} linha(s) foram recusadas.".format(
            resultado["criados"],
            resultado["acessos"],
            resultado["existentes"],
            resultado["recusadas"],
        ),
    )
    return redirect("admin_panel:minister_list")


@require_POST
@admin_required
def minister_import_cancel(request):
    request.session.pop(CHAVE_SESSAO_IMPORTACAO, None)
    messages.info(request, "Importação cancelada. Nada foi alterado.")
    return redirect("admin_panel:minister_import")
