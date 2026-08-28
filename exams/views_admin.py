"""
Telas administrativas de provas e questoes.

Padrao do projeto: a view valida a requisicao, chama um servico e apresenta o
resultado. Nenhuma regra de dominio mora aqui.

Duas respostas diferentes para "isso nao pode ser feito agora":

    GET de uma tela de edicao  -> redireciona ao detalhe, com mensagem
    POST que tentaria gravar   -> HTTP 409 Conflict

A distincao e proposital. Um GET costuma vir de link antigo ou de aba
esquecida aberta, e devolver 409 a quem so navegou seria hostil sem
necessidade. Um POST e uma tentativa de escrita, e ai o codigo precisa dizer
exatamente o que aconteceu: o recurso esta num estado que nao aceita a
operacao. Esconder o botao nunca e a protecao; estas duas respostas sao.
"""

from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, FormView, ListView, TemplateView

from common.exceptions import DomainError
from common.mixins import admin_required
from common.navigation import MENU_ADMIN, MENU_ADMIN_FUTURO
from common.views import PainelAdminMixin
from exams import selectors, services
from exams.forms import (
    ExamForm,
    ExamPasswordForm,
    QuestionForm,
    QuestionOptionFormSet,
)
from exams.models import Exam, ExamStatus, Question, QuestionType, TEXTO_VERDADEIRO
from exams.services.validation import erros_para_publicacao

SECAO = "provas"


def _mensagens(erro):
    """Lista de mensagens de um DomainError, que pode carregar varias."""
    return getattr(erro, "mensagens", None) or [str(erro)]


def _conflito(request, exam, mensagens):
    """
    Resposta a uma tentativa de escrita que o estado da prova nao permite.

    409 Conflict e o codigo certo: o pedido esta bem formado e o usuario tem
    permissao, mas o recurso esta num estado incompativel com a operacao.
    """
    return render(
        request,
        "admin_panel/exams/conflito.html",
        {
            "prova": exam,
            "mensagens": mensagens,
            "secao": SECAO,
            "itens_menu": MENU_ADMIN,
            "itens_futuros": MENU_ADMIN_FUTURO,
        },
        status=409,
    )


def _prova(pk):
    return get_object_or_404(Exam.objects.select_related("module"), pk=pk)


def _questao(exam_id, question_id):
    """
    Questao que pertence a esta prova, ou 404.

    O filtro por exam_id e o que impede editar a questao de outra prova
    trocando o numero na URL. Sem ele, /provas/10/questoes/50/editar/
    aceitaria a questao 50 de qualquer prova.
    """
    return get_object_or_404(
        Question.objects.select_related("exam", "exam__module"),
        pk=question_id,
        exam_id=exam_id,
    )


# ---------------------------------------------------------------------------
# Provas
# ---------------------------------------------------------------------------


class ExamListView(PainelAdminMixin, ListView):
    template_name = "admin_panel/exams/list.html"
    context_object_name = "provas"
    paginate_by = 25
    secao = SECAO

    def get_queryset(self):
        # select_related no modulo e annotate na contagem: sem os dois, uma
        # pagina de 25 provas faria 51 consultas.
        consulta = Exam.objects.select_related("module").annotate(
            total_questoes=Count("questions", filter=Q(questions__active=True), distinct=True),
            soma_pontos=Sum("questions__points", filter=Q(questions__active=True)),
        )

        modulo = (self.request.GET.get("modulo") or "").strip()
        if modulo.isdigit():
            consulta = consulta.filter(module_id=int(modulo))

        situacao = (self.request.GET.get("situacao") or "").strip()
        if situacao in ExamStatus.values:
            consulta = consulta.filter(status=situacao)

        busca = (self.request.GET.get("q") or "").strip()
        if busca:
            consulta = consulta.filter(title__icontains=busca)

        return consulta.order_by("module__order", "module__name", "title", "-version")

    def get_context_data(self, **kwargs):
        from courses.models import Module

        contexto = super().get_context_data(**kwargs)
        contexto["modulos"] = Module.objects.order_by("order", "name")
        contexto["situacoes"] = ExamStatus.choices
        contexto["filtro_modulo"] = (self.request.GET.get("modulo") or "").strip()
        contexto["filtro_situacao"] = (self.request.GET.get("situacao") or "").strip()
        contexto["busca"] = (self.request.GET.get("q") or "").strip()
        contexto["total_geral"] = Exam.objects.count()
        return contexto


class ExamDetailView(PainelAdminMixin, DetailView):
    template_name = "admin_panel/exams/detail.html"
    context_object_name = "prova"
    secao = SECAO

    def get_queryset(self):
        return Exam.objects.select_related("module", "parent_exam", "created_by")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        prova = self.object

        contexto["questoes_ativas"] = prova.questions.filter(active=True).count()
        contexto["questoes_totais"] = prova.questions.count()
        contexto["pontos"] = prova.pontos_vigentes

        # O resumo de publicacao. Mostrado antes de publicar para que o
        # administrador confira a escala e a janela sem precisar abrir outra
        # tela, e depois como registro do que foi congelado.
        if prova.e_rascunho:
            contexto["pendencias"] = erros_para_publicacao(prova)
        else:
            contexto["pendencias"] = []

        contexto["linhagem"] = (
            Exam.objects.da_linhagem_de(prova)
            .select_related("module")
            .order_by("version")
        )
        return contexto


class ExamCreateView(PainelAdminMixin, FormView):
    template_name = "admin_panel/exams/form.html"
    form_class = ExamForm
    secao = SECAO

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["titulo"] = "Nova prova"
        return contexto

    def form_valid(self, form):
        dados = dict(form.cleaned_data)
        module = dados.pop("module")
        try:
            prova = services.create_exam(
                module=module,
                actor=self.request.user,
                request=self.request,
                **dados,
            )
        except DomainError as erro:
            for mensagem in _mensagens(erro):
                form.add_error(None, mensagem)
            return self.form_invalid(form)

        messages.success(self.request, "Prova '{}' criada.".format(prova.title))
        return redirect("admin_panel:exam_detail", pk=prova.pk)


class ExamUpdateView(PainelAdminMixin, FormView):
    template_name = "admin_panel/exams/form.html"
    form_class = ExamForm
    secao = SECAO

    def get_prova(self):
        return _prova(self.kwargs["pk"])

    def get(self, request, *args, **kwargs):
        prova = self.get_prova()
        if not prova.estrutura_editavel:
            messages.error(
                request,
                "A prova '{}' esta {} e nao pode ser editada. Duplique-a para "
                "criar uma versao editavel.".format(
                    prova.title, prova.get_status_display().lower()
                ),
            )
            return redirect("admin_panel:exam_detail", pk=prova.pk)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        prova = self.get_prova()
        if not prova.estrutura_editavel:
            return _conflito(
                request,
                prova,
                [
                    "Somente provas em rascunho podem ser editadas. Esta prova "
                    "esta {}.".format(prova.get_status_display().lower())
                ],
            )
        return super().post(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        prova = self.get_prova()
        kwargs["exam"] = prova
        if self.request.method == "GET":
            kwargs["initial"] = {
                "module": prova.module_id,
                "title": prova.title,
                "description": prova.description,
                "instructions": prova.instructions,
                "open_at": prova.open_at,
                "close_at": prova.close_at,
                "duration_minutes": prova.duration_minutes,
                "passing_score": prova.passing_score,
                "max_attempts": prova.max_attempts,
                "failure_message": prova.failure_message,
                "randomize_questions": prova.randomize_questions,
                "randomize_options": prova.randomize_options,
                "show_score_after_submission": prova.show_score_after_submission,
            }
        return kwargs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["prova"] = self.get_prova()
        contexto["titulo"] = "Editar prova"
        return contexto

    def form_valid(self, form):
        prova = self.get_prova()
        dados = dict(form.cleaned_data)
        module = dados.pop("module")
        try:
            services.update_exam(
                prova,
                module=module,
                actor=self.request.user,
                request=self.request,
                **dados,
            )
        except DomainError as erro:
            for mensagem in _mensagens(erro):
                form.add_error(None, mensagem)
            return self.form_invalid(form)

        messages.success(self.request, "Prova '{}' atualizada.".format(prova.title))
        return redirect("admin_panel:exam_detail", pk=prova.pk)


@require_POST
@admin_required
def exam_publish(request, pk):
    prova = _prova(pk)
    try:
        services.publish_exam(prova, actor=request.user, request=request)
    except DomainError as erro:
        # Sempre 409, qualquer que seja o numero de problemas. Fazer a
        # resposta depender da quantidade de erros deixaria o comportamento
        # da rota imprevisivel para quem a consome e para quem a testa.
        return _conflito(request, prova, _mensagens(erro))

    messages.success(
        request,
        "Prova '{}' publicada com {} pontos no total.".format(
            prova.title, prova.total_points
        ),
    )
    return redirect("admin_panel:exam_detail", pk=prova.pk)


@require_POST
@admin_required
def exam_close(request, pk):
    prova = _prova(pk)
    try:
        services.close_exam(prova, actor=request.user, request=request)
    except DomainError as erro:
        return _conflito(request, prova, _mensagens(erro))

    messages.success(request, "Prova '{}' fechada.".format(prova.title))
    return redirect("admin_panel:exam_detail", pk=prova.pk)


@require_POST
@admin_required
def exam_duplicate(request, pk):
    prova = _prova(pk)
    try:
        copia = services.duplicate_exam(prova, actor=request.user, request=request)
    except DomainError as erro:
        return _conflito(request, prova, _mensagens(erro))

    messages.success(
        request,
        "Versao {} criada a partir da versao {}. Ela nasce como rascunho.".format(
            copia.version, prova.version
        ),
    )
    return redirect("admin_panel:exam_detail", pk=copia.pk)


class ExamPasswordView(PainelAdminMixin, FormView):
    template_name = "admin_panel/exams/password.html"
    form_class = ExamPasswordForm
    secao = SECAO

    def get_prova(self):
        return _prova(self.kwargs["pk"])

    def get(self, request, *args, **kwargs):
        prova = self.get_prova()
        if prova.e_fechada:
            messages.error(
                request, "Uma prova fechada nao aceita alteracao de senha."
            )
            return redirect("admin_panel:exam_detail", pk=prova.pk)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["prova"] = self.get_prova()
        return contexto

    def form_valid(self, form):
        prova = self.get_prova()
        try:
            services.set_exam_password(
                prova,
                form.cleaned_data["senha"],
                actor=self.request.user,
                request=self.request,
            )
        except DomainError as erro:
            for mensagem in _mensagens(erro):
                form.add_error(None, mensagem)
            return self.form_invalid(form)

        messages.success(self.request, "Senha da prova configurada.")
        return redirect("admin_panel:exam_detail", pk=prova.pk)


@require_POST
@admin_required
def exam_password_remove(request, pk):
    prova = _prova(pk)
    try:
        services.remove_exam_password(prova, actor=request.user, request=request)
    except DomainError as erro:
        return _conflito(request, prova, _mensagens(erro))

    messages.success(request, "Senha da prova removida.")
    return redirect("admin_panel:exam_detail", pk=prova.pk)


# ---------------------------------------------------------------------------
# Questoes
# ---------------------------------------------------------------------------


class QuestionListView(PainelAdminMixin, TemplateView):
    template_name = "admin_panel/exams/questions.html"
    secao = SECAO

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        prova = _prova(self.kwargs["exam_id"])

        contexto["prova"] = prova
        contexto["questoes"] = (
            prova.questions.prefetch_related("options").order_by("order", "id")
        )
        contexto["pontos"] = prova.pontos_vigentes
        return contexto


class _QuestionFormMixin(PainelAdminMixin):
    template_name = "admin_panel/exams/question_form.html"
    form_class = QuestionForm
    secao = SECAO

    def get_prova(self):
        return _prova(self.kwargs["exam_id"])

    def get_formset(self, dados=None, inicial=None):
        return QuestionOptionFormSet(data=dados, initial=inicial, prefix="opcoes")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["prova"] = self.get_prova()
        contexto.setdefault("formset", self.get_formset())
        contexto["tipos_com_alternativas"] = [
            QuestionType.SINGLE_CHOICE,
            QuestionType.MULTIPLE_CHOICE,
        ]
        return contexto

    def _opcoes_do_formset(self, formset):
        # O formset ja passou por is_valid() em quem chamou, entao
        # cleaned_data existe. Linhas em branco sao simplesmente puladas: o
        # formulario oferece cinco e quase nunca todas sao usadas.
        opcoes = []
        for indice, dados in enumerate(formset.cleaned_data, start=1):
            texto = (dados.get("text") or "").strip()
            if not texto:
                continue
            opcoes.append(
                {
                    "text": texto,
                    "is_correct": bool(dados.get("is_correct")),
                    "order": indice,
                }
            )
        return opcoes


class QuestionCreateView(_QuestionFormMixin, FormView):
    def get(self, request, *args, **kwargs):
        prova = self.get_prova()
        if not prova.estrutura_editavel:
            messages.error(
                request,
                "A prova esta {} e nao aceita novas questoes.".format(
                    prova.get_status_display().lower()
                ),
            )
            return redirect("admin_panel:exam_detail", pk=prova.pk)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["titulo"] = "Nova questao"
        return contexto

    def post(self, request, *args, **kwargs):
        prova = self.get_prova()
        if not prova.estrutura_editavel:
            return _conflito(
                request,
                prova,
                [
                    "A prova esta {} e nao aceita novas questoes.".format(
                        prova.get_status_display().lower()
                    )
                ],
            )

        form = self.get_form()
        formset = self.get_formset(dados=request.POST)
        if not form.is_valid() or not formset.is_valid():
            return self.render_to_response(
                self.get_context_data(form=form, formset=formset)
            )

        try:
            services.create_question(
                prova,
                type=form.cleaned_data["type"],
                text=form.cleaned_data["text"],
                points=form.cleaned_data["points"],
                required=form.cleaned_data.get("required", True),
                order=form.cleaned_data.get("order"),
                internal_explanation=form.cleaned_data.get("internal_explanation", ""),
                active=form.cleaned_data.get("active", True),
                opcoes=self._opcoes_do_formset(formset),
                resposta_verdadeira=form.resposta_verdadeira_bool,
                actor=request.user,
                request=request,
            )
        except DomainError as erro:
            for mensagem in _mensagens(erro):
                form.add_error(None, mensagem)
            return self.render_to_response(
                self.get_context_data(form=form, formset=formset)
            )

        messages.success(request, "Questao criada.")
        return redirect("admin_panel:question_list", exam_id=prova.pk)


class QuestionUpdateView(_QuestionFormMixin, FormView):
    def get_questao(self):
        return _questao(self.kwargs["exam_id"], self.kwargs["question_id"])

    def get(self, request, *args, **kwargs):
        prova = self.get_prova()
        self.get_questao()  # 404 se a questao nao for desta prova
        if not prova.estrutura_editavel:
            messages.error(
                request,
                "A prova esta {} e as questoes nao podem ser editadas.".format(
                    prova.get_status_display().lower()
                ),
            )
            return redirect("admin_panel:exam_detail", pk=prova.pk)
        return super().get(request, *args, **kwargs)

    def get_initial(self):
        questao = self.get_questao()

        # Em Verdadeiro ou Falso, qual radio vem marcado. Nos outros tipos o
        # campo nem aparece na tela.
        resposta_verdadeira = ""
        if questao.type == QuestionType.TRUE_FALSE:
            correta = questao.options.filter(is_correct=True).first()
            marcada_verdadeiro = (
                correta is not None
                and (correta.text or "").strip() == TEXTO_VERDADEIRO
            )
            resposta_verdadeira = "true" if marcada_verdadeiro else "false"

        return {
            "type": questao.type,
            "text": questao.text,
            "points": questao.points,
            "order": questao.order,
            "required": questao.required,
            "active": questao.active,
            "internal_explanation": questao.internal_explanation,
            "resposta_verdadeira": resposta_verdadeira,
        }

    def get_context_data(self, **kwargs):
        questao = self.get_questao()
        if "formset" not in kwargs:
            inicial = [
                {"text": opcao.text, "is_correct": opcao.is_correct}
                for opcao in questao.options.all()
            ]
            kwargs["formset"] = self.get_formset(inicial=inicial)
        contexto = super().get_context_data(**kwargs)
        contexto["titulo"] = "Editar questao"
        contexto["questao"] = questao
        return contexto

    def post(self, request, *args, **kwargs):
        prova = self.get_prova()
        questao = self.get_questao()
        if not prova.estrutura_editavel:
            return _conflito(
                request,
                prova,
                [
                    "A prova esta {} e as questoes nao podem ser editadas.".format(
                        prova.get_status_display().lower()
                    )
                ],
            )

        form = self.get_form()
        formset = self.get_formset(dados=request.POST)
        if not form.is_valid() or not formset.is_valid():
            return self.render_to_response(
                self.get_context_data(form=form, formset=formset)
            )

        try:
            services.update_question(
                questao,
                type=form.cleaned_data["type"],
                text=form.cleaned_data["text"],
                points=form.cleaned_data["points"],
                required=form.cleaned_data.get("required", True),
                order=form.cleaned_data.get("order"),
                internal_explanation=form.cleaned_data.get("internal_explanation", ""),
                active=form.cleaned_data.get("active", True),
                opcoes=self._opcoes_do_formset(formset),
                resposta_verdadeira=form.resposta_verdadeira_bool,
                actor=request.user,
                request=request,
            )
        except DomainError as erro:
            for mensagem in _mensagens(erro):
                form.add_error(None, mensagem)
            return self.render_to_response(
                self.get_context_data(form=form, formset=formset)
            )

        messages.success(request, "Questao atualizada.")
        return redirect("admin_panel:question_list", exam_id=prova.pk)


@require_POST
@admin_required
def question_delete(request, exam_id, question_id):
    prova = _prova(exam_id)
    questao = _questao(exam_id, question_id)
    try:
        services.delete_question(questao, actor=request.user, request=request)
    except DomainError as erro:
        return _conflito(request, prova, _mensagens(erro))

    messages.success(request, "Questao excluida.")
    return redirect("admin_panel:question_list", exam_id=prova.pk)


# ---------------------------------------------------------------------------
# Gabarito e preview
# ---------------------------------------------------------------------------


class GabaritoView(PainelAdminMixin, TemplateView):
    """
    Gabarito completo. ADMIN somente, por heranca de PainelAdminMixin.

    A tela mais sensivel do painel: mostra a resposta certa e a explicacao
    interna de cada questao. Nao ha versao equivalente para o aluno e nao
    existe rota fora de /admin-panel/.
    """

    template_name = "admin_panel/exams/gabarito.html"
    secao = SECAO

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        prova = _prova(self.kwargs["pk"])
        contexto["prova"] = prova
        contexto["questoes"] = selectors.gabarito(prova)
        return contexto


class ExamPreviewView(PainelAdminMixin, TemplateView):
    """
    Como o aluno vera a prova.

    Consome exams.selectors.questoes_para_aluno, exatamente a funcao que a
    tela do aluno usara na Etapa 4. Nao existe um caminho de dados "de
    preview" separado — se existisse, testar o preview nao provaria nada
    sobre a tela real.

    O contexto nao contem is_correct nem internal_explanation: as estruturas
    devolvidas pelo selector nao possuem esses atributos.
    """

    template_name = "admin_panel/exams/preview.html"
    secao = SECAO

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        prova = _prova(self.kwargs["pk"])
        contexto["prova"] = prova
        contexto["questoes"] = selectors.questoes_para_aluno(prova)
        return contexto
