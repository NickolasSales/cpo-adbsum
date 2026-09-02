"""
Telas administrativas de alunos.

As views validam a requisicao, chamam um servico e apresentam o resultado.
Nenhuma regra de dominio vive aqui.
"""

import secrets
from datetime import timedelta

from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, FormView, ListView, TemplateView

from accounts.models import User, UserRole
from common.exceptions import DomainError
from common.mixins import admin_required
from common.views import PainelAdminMixin
from courses.models import Enrollment, EnrollmentStatus
from students import services
from students.forms import ImportUploadForm, ResetPasswordForm, StudentForm
from students.importers import analisar, confirmar, ler_arquivo

# A analise fica na sessao do servidor entre o preview e a confirmacao.
# Guardamos apenas as linhas cruas do arquivo, nunca decisoes ja tomadas: a
# confirmacao reexecuta a analise do zero contra o banco daquele instante.
CHAVE_SESSAO_IMPORTACAO = "importacao_alunos"
VALIDADE_IMPORTACAO = timedelta(minutes=30)


class StudentListView(PainelAdminMixin, ListView):
    """Lista paginada de alunos, com busca e filtro de situacao."""

    template_name = "admin_panel/students/list.html"
    context_object_name = "alunos"
    paginate_by = 25
    secao = "alunos"

    def get_queryset(self):
        # annotate evita uma consulta por aluno para contar os modulos, e o
        # select_related traz a origem do cadastro na mesma consulta.
        consulta = services.alunos_queryset().annotate(
            total_modulos=Count(
                "enrollments",
                filter=Q(enrollments__status=EnrollmentStatus.ACTIVE),
                distinct=True,
            )
        )

        busca = (self.request.GET.get("q") or "").strip()
        if busca:
            consulta = consulta.filter(
                Q(full_name__icontains=busca) | Q(email__icontains=busca)
            )

        situacao = (self.request.GET.get("situacao") or "").strip()
        if situacao == "ativos":
            consulta = consulta.filter(is_active=True)
        elif situacao == "bloqueados":
            consulta = consulta.filter(is_active=False)

        return consulta.order_by("full_name")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["busca"] = (self.request.GET.get("q") or "").strip()
        contexto["situacao"] = (self.request.GET.get("situacao") or "").strip()
        contexto["senha_configurada"] = services.senha_inicial_configurada()
        contexto["total_geral"] = services.alunos_queryset().count()
        return contexto


class StudentDetailView(PainelAdminMixin, DetailView):
    """Ficha do aluno com as matriculas."""

    template_name = "admin_panel/students/detail.html"
    context_object_name = "aluno"
    secao = "alunos"

    def get_queryset(self):
        return services.alunos_queryset()

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["perfil"] = getattr(self.object, "student_profile", None)
        contexto["matriculas"] = (
            Enrollment.objects.filter(student=self.object)
            .select_related("module", "revoked_by")
            .com_contagem_de_historico()
            .order_by("module__order", "module__name")
        )
        return contexto


class StudentCreateView(PainelAdminMixin, FormView):
    """
    Cadastro manual de aluno.

    A partir da Etapa 5 o administrador define a senha aqui mesmo. A senha
    digitada vai direto para o servico e nunca e devolvida ao HTML, gravada
    em log ou registrada na auditoria.
    """

    template_name = "admin_panel/students/form.html"
    form_class = StudentForm
    secao = "alunos"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["criando"] = True
        return kwargs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["titulo"] = "Novo aluno"
        return contexto

    def form_valid(self, form):
        try:
            aluno = services.create_student(
                full_name=form.cleaned_data["full_name"],
                email=form.cleaned_data["email"],
                password=form.senha,
                notes=form.cleaned_data.get("notes", ""),
                actor=self.request.user,
                request=self.request,
            )
        except DomainError as erro:
            form.add_error(None, str(erro))
            return self.form_invalid(form)

        # A mensagem nao repete a senha. Quem a definiu acabou de digita-la, e
        # o texto de sucesso viaja para a proxima tela pela sessao.
        messages.success(
            self.request,
            "Aluno {} cadastrado. Informe a senha ao aluno por um canal "
            "seguro.".format(aluno.full_name),
        )
        return redirect("admin_panel:student_detail", pk=aluno.pk)


class StudentPasswordResetView(PainelAdminMixin, FormView):
    """
    Redefinicao da senha de um aluno pelo administrador.

    Existe porque o aluno nao troca mais a propria senha: sem esta tela,
    esquecer a senha viraria um beco sem saida.

    GET mostra o formulario, POST aplica. A senha vigente nunca e exibida —
    ela so existe como hash, e nem o administrador a conhece.
    """

    template_name = "admin_panel/students/reset_password.html"
    form_class = ResetPasswordForm
    secao = "alunos"

    def dispatch(self, request, *args, **kwargs):
        self.aluno = get_object_or_404(services.alunos_queryset(), pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["aluno"] = self.aluno
        contexto["titulo"] = "Resetar senha"
        return contexto

    def form_valid(self, form):
        try:
            services.reset_student_password(
                self.aluno,
                new_password=form.cleaned_data["password1"],
                actor=self.request.user,
                request=self.request,
            )
        except DomainError as erro:
            form.add_error(None, str(erro))
            return self.form_invalid(form)

        messages.success(
            self.request,
            "Senha de {} redefinida. As sessoes abertas com a senha anterior "
            "deixam de valer.".format(self.aluno.full_name),
        )
        return redirect("admin_panel:student_detail", pk=self.aluno.pk)


class StudentUpdateView(PainelAdminMixin, FormView):
    """
    Edicao de aluno.

    O formulario expoe apenas nome, e-mail e observacoes. Papel e permissoes
    nao aparecem e nao sao aceitos pelo servico, entao nem um POST forjado
    consegue promover um aluno a administrador.
    """

    template_name = "admin_panel/students/form.html"
    form_class = StudentForm
    secao = "alunos"

    def get_aluno(self):
        return get_object_or_404(services.alunos_queryset(), pk=self.kwargs["pk"])

    def get_initial(self):
        aluno = self.get_aluno()
        perfil = getattr(aluno, "student_profile", None)
        return {
            "full_name": aluno.full_name,
            "email": aluno.email,
            "notes": perfil.notes if perfil else "",
        }

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["aluno"] = self.get_aluno()
        contexto["titulo"] = "Editar aluno"
        contexto["senha_configurada"] = True
        return contexto

    def form_valid(self, form):
        aluno = self.get_aluno()
        try:
            services.update_student(
                aluno,
                full_name=form.cleaned_data["full_name"],
                email=form.cleaned_data["email"],
                notes=form.cleaned_data.get("notes", ""),
                actor=self.request.user,
                request=self.request,
            )
        except DomainError as erro:
            form.add_error(None, str(erro))
            return self.form_invalid(form)

        messages.success(self.request, "Dados do aluno atualizados.")
        return redirect("admin_panel:student_detail", pk=aluno.pk)


def _obter_aluno(pk):
    return get_object_or_404(User.objects.filter(role=UserRole.STUDENT), pk=pk)


@require_POST
@admin_required
def student_block(request, pk):
    """
    Bloqueia o aluno. Somente POST, com CSRF.

    Uma acao que altera estado nunca pode ser alcancavel por GET: bastaria um
    link ou uma imagem em outra pagina para dispara-la.
    """
    aluno = _obter_aluno(pk)
    try:
        services.block_student(aluno, actor=request.user, request=request)
    except DomainError as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, "{} foi bloqueado.".format(aluno.full_name))
    return redirect(request.POST.get("proximo") or "admin_panel:student_list")


@require_POST
@admin_required
def student_unblock(request, pk):
    aluno = _obter_aluno(pk)
    try:
        services.unblock_student(aluno, actor=request.user, request=request)
    except DomainError as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, "{} foi desbloqueado.".format(aluno.full_name))
    return redirect(request.POST.get("proximo") or "admin_panel:student_list")


# ---------------------------------------------------------------------------
# Importacao
# ---------------------------------------------------------------------------


class StudentImportUploadView(PainelAdminMixin, FormView):
    """
    Primeira etapa da importacao: recebe o arquivo e analisa.

    O upload nao grava nada no banco. O arquivo e lido, as linhas sao
    guardadas na sessao e o administrador e levado ao preview.
    """

    template_name = "admin_panel/students/import_upload.html"
    form_class = ImportUploadForm
    secao = "alunos"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["senha_configurada"] = services.senha_inicial_configurada()
        return contexto

    def form_valid(self, form):
        try:
            linhas = ler_arquivo(form.cleaned_data["arquivo"])
        except DomainError as erro:
            form.add_error("arquivo", str(erro))
            return self.form_invalid(form)

        # O token amarra o preview exibido a confirmacao que sera enviada.
        # Se o administrador subir outro arquivo em outra aba, a confirmacao
        # antiga deixa de valer em vez de aplicar o lote errado.
        self.request.session[CHAVE_SESSAO_IMPORTACAO] = {
            "token": secrets.token_urlsafe(16),
            "arquivo": form.cleaned_data["arquivo"].name,
            "criado_em": timezone.now().isoformat(),
            "linhas": linhas,
        }
        return redirect("admin_panel:student_import_preview")


def _lote_da_sessao(request):
    """
    Recupera o lote em analise, ou None se ausente ou expirado.

    O arquivo nao fica guardado indefinidamente: passados 30 minutos a
    sessao e limpa e o administrador recomeca o upload.
    """
    lote = request.session.get(CHAVE_SESSAO_IMPORTACAO)
    if not lote:
        return None

    criado_em = parse_datetime_seguro(lote.get("criado_em"))
    if criado_em is None or timezone.now() - criado_em > VALIDADE_IMPORTACAO:
        request.session.pop(CHAVE_SESSAO_IMPORTACAO, None)
        return None
    return lote


def parse_datetime_seguro(valor):
    from django.utils.dateparse import parse_datetime

    try:
        return parse_datetime(valor) if valor else None
    except (TypeError, ValueError):
        return None


class StudentImportPreviewView(PainelAdminMixin, TemplateView):
    """
    Segunda etapa: mostra o que aconteceria, sem alterar nada.

    Esta view e estritamente somente leitura.
    """

    template_name = "admin_panel/students/import_preview.html"
    secao = "alunos"

    def get(self, request, *args, **kwargs):
        if _lote_da_sessao(request) is None:
            messages.info(
                request, "Envie o arquivo novamente para revisar a importacao."
            )
            return redirect("admin_panel:student_import")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        lote = _lote_da_sessao(self.request)
        analise = analisar(lote["linhas"])

        contexto["arquivo"] = lote["arquivo"]
        contexto["token"] = lote["token"]
        contexto["analise"] = analise
        contexto["resumo"] = analise.resumo()
        contexto["linhas"] = analise.linhas
        return contexto


@require_POST
@admin_required
def student_import_confirm(request):
    """
    Terceira etapa: aplica o lote.

    A analise e refeita dentro de confirmar(), a partir das linhas cruas
    guardadas na sessao. Nada do que o navegador envia participa da decisao
    sobre o que importar — o formulario carrega apenas o token que identifica
    o lote.
    """
    lote = _lote_da_sessao(request)
    if lote is None:
        messages.info(request, "A analise expirou. Envie o arquivo novamente.")
        return redirect("admin_panel:student_import")

    if request.POST.get("token") != lote["token"]:
        messages.error(
            request,
            "Esta confirmacao nao corresponde ao arquivo em analise. "
            "Revise o preview e confirme novamente.",
        )
        return redirect("admin_panel:student_import_preview")

    try:
        resultado = confirmar(
            lote["linhas"], actor=request.user, request=request
        )
    except DomainError as erro:
        messages.error(request, str(erro))
        return redirect("admin_panel:student_import_preview")

    request.session.pop(CHAVE_SESSAO_IMPORTACAO, None)

    messages.success(
        request,
        "Importacao concluida: {} aluno(s) criado(s) e {} matricula(s) "
        "criada(s).".format(
            resultado["alunos_criados"], resultado["matriculas_criadas"]
        ),
    )
    return redirect("admin_panel:student_list")


@require_POST
@admin_required
def student_import_cancel(request):
    request.session.pop(CHAVE_SESSAO_IMPORTACAO, None)
    messages.info(request, "Importacao cancelada. Nada foi alterado.")
    return redirect("admin_panel:student_import")
