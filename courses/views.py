"""
Telas administrativas de modulos e matriculas, e area do aluno.

As views validam a requisicao, chamam um servico e apresentam o resultado.
"""

from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, FormView, ListView, TemplateView

from common.exceptions import DomainError
from common.mixins import StudentRequiredMixin, admin_required
from common.navigation import MENU_ADMIN, MENU_ADMIN_FUTURO
from common.views import PainelAdminMixin
from courses import services
from courses.forms import CAMPOS_DO_CERTIFICADO, EnrollmentForm, ModuleForm
from courses.models import Enrollment, EnrollmentStatus, Module
from exams import selectors as exams_selectors


# ---------------------------------------------------------------------------
# Modulos
# ---------------------------------------------------------------------------


def _dados_do_certificado(form):
    """
    Recolhe os campos do certificado ja validados pelo formulario.

    A leitura e por nome, a partir da lista branca — e nao um varrer de
    cleaned_data. Formulario e servico enxergam a mesma tupla, entao um campo
    novo precisa ser declarado nos dois lugares para chegar ao banco.
    """
    return {
        campo: form.cleaned_data.get(campo)
        for campo in CAMPOS_DO_CERTIFICADO
        if campo in form.cleaned_data
    }


class ModuleListView(PainelAdminMixin, ListView):
    template_name = "admin_panel/modules/list.html"
    context_object_name = "modulos"
    paginate_by = 25
    secao = "modulos"

    def get_queryset(self):
        # A contagem de matriculados vem por annotate, e nao por uma consulta
        # dentro do laco do template.
        consulta = Module.objects.annotate(
            total_matriculados=Count(
                "enrollments",
                filter=Q(enrollments__status=EnrollmentStatus.ACTIVE),
                distinct=True,
            )
        )

        situacao = (self.request.GET.get("situacao") or "").strip()
        if situacao == "ativos":
            consulta = consulta.filter(is_active=True)
        elif situacao == "inativos":
            consulta = consulta.filter(is_active=False)

        busca = (self.request.GET.get("q") or "").strip()
        if busca:
            consulta = consulta.filter(
                Q(name__icontains=busca) | Q(code__icontains=busca)
            )

        return consulta.order_by("order", "name")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["situacao"] = (self.request.GET.get("situacao") or "").strip()
        contexto["busca"] = (self.request.GET.get("q") or "").strip()
        contexto["total_geral"] = Module.objects.count()
        return contexto


class ModuleDetailView(PainelAdminMixin, DetailView):
    template_name = "admin_panel/modules/detail.html"
    context_object_name = "modulo"
    model = Module
    secao = "modulos"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        # com_contagem_de_historico e o que permite a parcial de acoes
        # decidir entre "Excluir" e "Revogar" sem uma consulta por linha. Sem
        # a anotacao, sem_historico_academico devolve False e a exclusao
        # simplesmente nao e oferecida.
        contexto["matriculas"] = (
            Enrollment.objects.filter(module=self.object)
            .select_related("student", "revoked_by")
            .com_contagem_de_historico()
            .order_by("student__full_name")
        )
        return contexto


class ModuleCreateView(PainelAdminMixin, FormView):
    template_name = "admin_panel/modules/form.html"
    form_class = ModuleForm
    secao = "modulos"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["titulo"] = "Novo modulo"
        return contexto

    def form_valid(self, form):
        try:
            modulo = services.create_module(
                name=form.cleaned_data["name"],
                code=form.cleaned_data["code"],
                description=form.cleaned_data.get("description", ""),
                order=form.cleaned_data.get("order") or 0,
                is_active=form.cleaned_data.get("is_active", True),
                dados_do_certificado=_dados_do_certificado(form),
                certificate_template=form.cleaned_data.get("certificate_template"),
                actor=self.request.user,
                request=self.request,
            )
        except DomainError as erro:
            form.add_error(None, str(erro))
            return self.form_invalid(form)

        messages.success(self.request, "Modulo {} criado.".format(modulo.code))
        return redirect("admin_panel:module_detail", pk=modulo.pk)


class ModuleUpdateView(PainelAdminMixin, FormView):
    template_name = "admin_panel/modules/form.html"
    form_class = ModuleForm
    secao = "modulos"

    def get_modulo(self):
        return get_object_or_404(Module, pk=self.kwargs["pk"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_modulo()
        return kwargs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["modulo"] = self.get_modulo()
        contexto["titulo"] = "Editar modulo"
        return contexto

    def form_valid(self, form):
        modulo = self.get_modulo()
        try:
            services.update_module(
                modulo,
                name=form.cleaned_data["name"],
                code=form.cleaned_data["code"],
                description=form.cleaned_data.get("description", ""),
                order=form.cleaned_data.get("order") or 0,
                is_active=form.cleaned_data.get("is_active", True),
                dados_do_certificado=_dados_do_certificado(form),
                certificate_template=form.cleaned_data.get("certificate_template"),
                actor=self.request.user,
                request=self.request,
            )
        except DomainError as erro:
            form.add_error(None, str(erro))
            return self.form_invalid(form)

        messages.success(self.request, "Modulo {} atualizado.".format(modulo.code))
        return redirect("admin_panel:module_detail", pk=modulo.pk)


@require_POST
@admin_required
def module_disable(request, pk):
    modulo = get_object_or_404(Module, pk=pk)
    services.disable_module(modulo, actor=request.user, request=request)
    messages.success(request, "Modulo {} desativado.".format(modulo.code))
    return redirect(request.POST.get("proximo") or "admin_panel:module_list")


@require_POST
@admin_required
def module_enable(request, pk):
    modulo = get_object_or_404(Module, pk=pk)
    services.enable_module(modulo, actor=request.user, request=request)
    messages.success(request, "Modulo {} ativado.".format(modulo.code))
    return redirect(request.POST.get("proximo") or "admin_panel:module_list")


# ---------------------------------------------------------------------------
# Matriculas
# ---------------------------------------------------------------------------


class EnrollmentListView(PainelAdminMixin, ListView):
    template_name = "admin_panel/enrollments/list.html"
    context_object_name = "matriculas"
    paginate_by = 25
    secao = "matriculas"

    def get_queryset(self):
        consulta = (
            Enrollment.objects.select_related("student", "module", "revoked_by")
            .com_contagem_de_historico()
        )

        modulo = (self.request.GET.get("modulo") or "").strip()
        if modulo.isdigit():
            consulta = consulta.filter(module_id=int(modulo))

        # Sem filtro explicito, as revogadas nao aparecem. Revogar e o ato que
        # tira a matricula da visao do dia a dia; se ela continuasse na lista
        # padrao, o botao nao teria feito o que promete.
        #
        # "todas" e um valor a parte, e nao a ausencia de filtro: quem quer ver
        # revogada junto com o resto precisa pedir.
        situacao = (self.request.GET.get("situacao") or "").strip()
        if situacao in EnrollmentStatus.values:
            consulta = consulta.filter(status=situacao)
        elif situacao != "todas":
            consulta = consulta.operacionais()

        acesso = (self.request.GET.get("acesso") or "").strip()
        if acesso == "liberado":
            consulta = consulta.filter(access_enabled=True)
        elif acesso == "bloqueado":
            consulta = consulta.filter(access_enabled=False)

        busca = (self.request.GET.get("q") or "").strip()
        if busca:
            consulta = consulta.filter(
                Q(student__full_name__icontains=busca)
                | Q(student__email__icontains=busca)
            )

        return consulta.order_by("module__order", "module__name", "student__full_name")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["modulos"] = Module.objects.order_by("order", "name")
        contexto["situacoes"] = EnrollmentStatus.choices
        contexto["filtro_modulo"] = (self.request.GET.get("modulo") or "").strip()
        contexto["filtro_situacao"] = (self.request.GET.get("situacao") or "").strip()
        contexto["filtro_acesso"] = (self.request.GET.get("acesso") or "").strip()
        contexto["busca"] = (self.request.GET.get("q") or "").strip()
        # A contagem total continua sendo de tudo, revogadas incluidas: ela
        # responde "quantas matriculas existem", e nao "quantas a tela mostra".
        contexto["total_geral"] = Enrollment.objects.count()
        contexto["total_revogadas"] = Enrollment.objects.filter(
            status=EnrollmentStatus.REVOKED
        ).count()
        # A coluna de revogacao so faz sentido quando ela esta sendo
        # consultada. Numa lista de matriculas ativas seriam tres colunas
        # vazias empurrando o resto para fora da tela.
        contexto["mostrando_revogadas"] = contexto["filtro_situacao"] in (
            EnrollmentStatus.REVOKED,
            "todas",
        )
        return contexto


class EnrollmentCreateView(PainelAdminMixin, FormView):
    template_name = "admin_panel/enrollments/form.html"
    form_class = EnrollmentForm
    secao = "matriculas"

    def get_initial(self):
        inicial = super().get_initial()
        aluno = (self.request.GET.get("aluno") or "").strip()
        if aluno.isdigit():
            inicial["student"] = int(aluno)
        modulo = (self.request.GET.get("modulo") or "").strip()
        if modulo.isdigit():
            inicial["module"] = int(modulo)
        return inicial

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["titulo"] = "Nova matricula"
        return contexto

    def form_valid(self, form):
        try:
            matricula = services.create_enrollment(
                student=form.cleaned_data["student"],
                module=form.cleaned_data["module"],
                notes=form.cleaned_data.get("notes", ""),
                actor=self.request.user,
                request=self.request,
            )
        except DomainError as erro:
            form.add_error(None, str(erro))
            return self.form_invalid(form)

        messages.success(
            self.request,
            "{} matriculado em {}.".format(
                matricula.student.full_name, matricula.module.code
            ),
        )
        return redirect("admin_panel:enrollment_list")


def _acao_matricula(request, pk, servico, mensagem):
    """Executa uma acao de matricula e devolve o administrador de onde veio."""
    matricula = get_object_or_404(
        Enrollment.objects.select_related("student", "module"), pk=pk
    )
    try:
        servico(matricula, actor=request.user, request=request)
    except DomainError as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, mensagem.format(matricula=matricula))
    return redirect(request.POST.get("proximo") or "admin_panel:enrollment_list")


@require_POST
@admin_required
def enrollment_block(request, pk):
    return _acao_matricula(
        request,
        pk,
        services.block_enrollment_access,
        "Acesso de {matricula.student.full_name} bloqueado em {matricula.module.code}.",
    )


@require_POST
@admin_required
def enrollment_unblock(request, pk):
    return _acao_matricula(
        request,
        pk,
        services.unblock_enrollment_access,
        "Acesso de {matricula.student.full_name} liberado em {matricula.module.code}.",
    )


@require_POST
@admin_required
def enrollment_disable(request, pk):
    return _acao_matricula(
        request,
        pk,
        services.disable_enrollment,
        "Matricula de {matricula.student.full_name} em {matricula.module.code} desativada.",
    )


@require_POST
@admin_required
def enrollment_reactivate(request, pk):
    return _acao_matricula(
        request,
        pk,
        services.reactivate_enrollment,
        "Matricula de {matricula.student.full_name} em {matricula.module.code} reativada.",
    )


@require_POST
@admin_required
def enrollment_complete(request, pk):
    return _acao_matricula(
        request,
        pk,
        services.complete_enrollment,
        "Matricula de {matricula.student.full_name} em {matricula.module.code} concluida.",
    )


# ---------------------------------------------------------------------------
# Revogacao, exclusao e restauracao (Etapa 9)
#
# Mesmo desenho das provas: a confirmacao e uma tela GET, que nao altera nada;
# a escrita e POST e recusa GET com 405. Modal dependeria de JavaScript, e a
# tela que decide apagar ou revogar uma matricula nao pode ser a mais fragil
# do sistema.
# ---------------------------------------------------------------------------


def _mensagens_do_erro(erro):
    """Lista de mensagens de um DomainError, que pode carregar varias."""
    return getattr(erro, "mensagens", None) or [str(erro)]


def _matricula(pk):
    return get_object_or_404(
        Enrollment.objects.select_related("student", "module", "revoked_by"), pk=pk
    )


def _conflito_matricula(request, matricula, mensagens):
    """
    409 para o que o estado da matricula nao permite.

    Mesma escolha das provas: o pedido esta bem formado e o usuario tem
    permissao, mas o recurso esta num estado incompativel. Um redirect com
    mensagem verde faria o administrador acreditar que a segunda revogacao
    aconteceu.
    """
    return render(
        request,
        "admin_panel/enrollments/conflito.html",
        {
            "matricula": matricula,
            "mensagens": mensagens,
            "secao": "matriculas",
            "itens_menu": MENU_ADMIN,
            "itens_futuros": MENU_ADMIN_FUTURO,
        },
        status=409,
    )


class EnrollmentRevokeConfirmView(PainelAdminMixin, TemplateView):
    template_name = "admin_panel/enrollments/confirmar.html"
    secao = "matriculas"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        matricula = _matricula(self.kwargs["pk"])

        impedimentos = []
        if matricula.e_revogada:
            impedimentos.append("Esta matricula ja foi revogada.")

        contexto.update(
            {
                "matricula": matricula,
                "titulo": "Revogar matricula?",
                "url_da_acao": "admin_panel:enrollment_revoke",
                "rotulo_do_botao": "Revogar matricula",
                "classe_do_botao": "btn-danger",
                "impedimentos": impedimentos,
                "exige_motivo": True,
                "rotulo_do_motivo": "Motivo da revogacao",
                "exemplo_do_motivo": "Exemplo: aluno transferido de turma.",
                "avisos": [
                    "O aluno perdera o acesso a este modulo.",
                    "O historico academico existente sera preservado: "
                    "tentativas, notas e certificados.",
                    "Certificado ja emitido NAO e revogado por esta acao. "
                    "A revogacao do documento e um ato separado.",
                    "A matricula sai da lista padrao e passa a ser "
                    "consultavel pelo filtro Revogadas.",
                ],
            }
        )
        return contexto


class EnrollmentDeleteConfirmView(PainelAdminMixin, TemplateView):
    template_name = "admin_panel/enrollments/confirmar.html"
    secao = "matriculas"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        matricula = _matricula(self.kwargs["pk"])

        contexto.update(
            {
                "matricula": matricula,
                "titulo": "Excluir matricula permanentemente?",
                "url_da_acao": "admin_panel:enrollment_delete",
                "rotulo_do_botao": "Excluir matricula",
                "classe_do_botao": "btn-danger",
                "impedimentos": services.can_delete_enrollment(matricula),
                "exige_motivo": False,
                "avisos": [
                    "Esta matricula nao possui historico academico.",
                    "Esta operacao nao podera ser desfeita.",
                ],
            }
        )
        return contexto


class EnrollmentRestoreConfirmView(PainelAdminMixin, TemplateView):
    template_name = "admin_panel/enrollments/confirmar.html"
    secao = "matriculas"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        matricula = _matricula(self.kwargs["pk"])

        impedimentos = []
        if not matricula.e_revogada:
            impedimentos.append("Esta matricula nao esta revogada.")
        elif services.tem_certificado_ativo(matricula):
            impedimentos.append(
                "Esta matricula possui certificado ativo de conclusao. "
                "Revogue o certificado antes de restaurar acesso academico."
            )
        if not matricula.module.is_active:
            impedimentos.append(
                "O modulo {} esta inativo. Ative o modulo antes de restaurar "
                "a matricula.".format(matricula.module.code)
            )

        contexto.update(
            {
                "matricula": matricula,
                "titulo": "Restaurar matricula?",
                "url_da_acao": "admin_panel:enrollment_restore",
                "rotulo_do_botao": "Restaurar matricula",
                "classe_do_botao": "btn-success",
                "impedimentos": impedimentos,
                "exige_motivo": False,
                "avisos": [
                    "A matricula volta a ficar ativa e com acesso liberado.",
                    "O motivo e a data da revogacao serao limpos do registro; "
                    "a trilha de auditoria guarda os dois.",
                ],
            }
        )
        return contexto


@require_POST
@admin_required
def enrollment_revoke(request, pk):
    matricula = _matricula(pk)
    try:
        services.revoke_enrollment(
            matricula,
            actor=request.user,
            reason=request.POST.get("motivo") or "",
            request=request,
        )
    except services.MatriculaJaRevogada as erro:
        return _conflito_matricula(request, matricula, erro.mensagens)
    except DomainError as erro:
        # Motivo em branco cai aqui: erro de preenchimento, e nao conflito de
        # estado. A tela de confirmacao volta com a mensagem, sem 409.
        messages.error(request, str(erro))
        return redirect("admin_panel:enrollment_revoke_confirm", pk=matricula.pk)

    messages.success(
        request,
        "Matricula de {} em {} revogada. O historico academico foi "
        "preservado.".format(matricula.student.full_name, matricula.module.code),
    )
    return redirect("admin_panel:enrollment_list")


@require_POST
@admin_required
def enrollment_delete(request, pk):
    matricula = _matricula(pk)
    nome = matricula.student.full_name
    codigo = matricula.module.code

    try:
        services.delete_enrollment(matricula, actor=request.user, request=request)
    except DomainError as erro:
        # Vale mesmo que o POST tenha sido montado a mao contra uma matricula
        # com historico: quem recusa e o servico, e nao o botao escondido.
        return _conflito_matricula(request, matricula, _mensagens_do_erro(erro))

    messages.success(request, "Matricula de {} em {} excluida.".format(nome, codigo))
    return redirect("admin_panel:enrollment_list")


@require_POST
@admin_required
def enrollment_restore(request, pk):
    matricula = _matricula(pk)
    try:
        services.restore_revoked_enrollment(
            matricula, actor=request.user, request=request
        )
    except DomainError as erro:
        return _conflito_matricula(request, matricula, _mensagens_do_erro(erro))

    messages.success(
        request,
        "Matricula de {} em {} restaurada.".format(
            matricula.student.full_name, matricula.module.code
        ),
    )
    return redirect("admin_panel:enrollment_list")


# ---------------------------------------------------------------------------
# Area do aluno
# ---------------------------------------------------------------------------


class StudentModuleDetailView(StudentRequiredMixin, TemplateView):
    """
    Modulo visto pelo aluno.

    O acesso e resolvido pela matricula, nunca pelo id da URL. Sem matricula
    liberada a resposta e 404, e nao 403: um 403 confirmaria que aquele
    modulo existe, o que ja e informacao a mais para quem esta sondando.
    """

    template_name = "student/module_detail.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        matricula = services.matricula_liberada_ou_none(
            self.request.user, self.kwargs["pk"]
        )
        if matricula is None:
            raise Http404("Modulo indisponivel.")

        contexto["modulo"] = matricula.module
        contexto["matricula"] = matricula
        # A situacao de cada prova para este aluno — disponivel, em andamento,
        # enviada, encerrada — e decidida no selector, e nao no template. Duas
        # consultas para o modulo inteiro, sem uma por prova.
        contexto["provas"] = exams_selectors.provas_do_modulo_para_aluno(
            matricula.module, self.request.user, agora=timezone.now()
        )
        return contexto
