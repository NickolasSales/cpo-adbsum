"""
Telas administrativas de tentativas.

    /admin-panel/tentativas/           lista com filtros
    /admin-panel/tentativas/<id>/      detalhe completo
    /admin-panel/tentativas/<id>/resetar/   POST, anula

A URL usa a PK. E area administrativa: nao existe aluno curioso trocando o
numero, e o public_id continua sendo o identificador que a area do aluno usa.

Esta e a unica tela do sistema que mostra IP e user-agent da tentativa, ao
lado das respostas e do gabarito. Tudo isso e uso administrativo e nunca
alimenta tela de aluno.
"""

from django.contrib import messages
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from django.views.generic import ListView, TemplateView

from common.exceptions import DomainError
from common.mixins import admin_required
from common.views import PainelAdminMixin
from courses.models import Module
from exams.models import (
    AttemptResult,
    AttemptStatus,
    Exam,
    ExamAttempt,
    GradingStatus,
    QuestionType,
)
from exams.services import grading, reset as reset_service

POR_PAGINA = 25

TIPOS_OBJETIVOS = {
    QuestionType.SINGLE_CHOICE,
    QuestionType.MULTIPLE_CHOICE,
    QuestionType.TRUE_FALSE,
}


def _tentativa_ou_404(pk):
    tentativa = (
        ExamAttempt.objects.select_related(
            "student", "exam", "exam__module", "reset_by"
        )
        .filter(pk=pk)
        .first()
    )
    if tentativa is None:
        raise Http404("Tentativa nao encontrada.")
    return tentativa


def _data_ou_none(texto):
    from datetime import date

    if not texto:
        return None
    try:
        ano, mes, dia = (int(p) for p in texto.split("-"))
        return date(ano, mes, dia)
    except (ValueError, TypeError):
        return None


class AttemptListView(PainelAdminMixin, ListView):
    """Todas as tentativas, com filtros pelas tres dimensoes."""

    template_name = "admin_panel/attempts/list.html"
    context_object_name = "pagina"
    paginate_by = POR_PAGINA
    secao = "tentativas"

    def get_queryset(self):
        consulta = ExamAttempt.objects.select_related(
            "student", "exam", "exam__module"
        ).order_by("-started_at", "-id")

        g = self.request.GET
        busca = (g.get("q") or "").strip()
        modulo = (g.get("modulo") or "").strip()
        prova = (g.get("prova") or "").strip()
        situacao = (g.get("situacao") or "").strip()
        correcao = (g.get("correcao") or "").strip()
        resultado = (g.get("resultado") or "").strip()

        if busca:
            consulta = consulta.filter(
                Q(student__full_name__icontains=busca)
                | Q(student__email__icontains=busca)
                | Q(exam__title__icontains=busca)
            )
        if modulo.isdigit():
            consulta = consulta.filter(exam__module_id=int(modulo))
        if prova.isdigit():
            consulta = consulta.filter(exam_id=int(prova))
        if situacao in AttemptStatus.values:
            consulta = consulta.filter(status=situacao)
        if correcao in GradingStatus.values:
            consulta = consulta.filter(grading_status=correcao)
        if resultado in AttemptResult.values:
            consulta = consulta.filter(result=resultado)

        # Datas invalidas sao ignoradas em silencio: o campo e conveniencia, e
        # alguem digitando "31/02" nao deve derrubar a tela.
        inicio = _data_ou_none((g.get("de") or "").strip())
        fim = _data_ou_none((g.get("ate") or "").strip())
        if inicio:
            consulta = consulta.filter(started_at__date__gte=inicio)
        if fim:
            consulta = consulta.filter(started_at__date__lte=fim)

        return consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        g = self.request.GET
        contexto.update(
            {
                "modulos": Module.objects.order_by("code"),
                "provas": Exam.objects.select_related("module").order_by("title"),
                "situacoes": AttemptStatus.choices,
                "correcoes": GradingStatus.choices,
                "resultados": AttemptResult.choices,
                "busca": (g.get("q") or "").strip(),
                "filtro_modulo": (g.get("modulo") or "").strip(),
                "filtro_prova": (g.get("prova") or "").strip(),
                "filtro_situacao": (g.get("situacao") or "").strip(),
                "filtro_correcao": (g.get("correcao") or "").strip(),
                "filtro_resultado": (g.get("resultado") or "").strip(),
                "filtro_de": (g.get("de") or "").strip(),
                "filtro_ate": (g.get("ate") or "").strip(),
            }
        )
        contexto["tem_filtro"] = any(
            contexto[chave]
            for chave in (
                "busca",
                "filtro_modulo",
                "filtro_prova",
                "filtro_situacao",
                "filtro_correcao",
                "filtro_resultado",
                "filtro_de",
                "filtro_ate",
            )
        )
        return contexto


class AttemptDetailView(PainelAdminMixin, TemplateView):
    """
    Tudo sobre uma tentativa, para inspecao administrativa.

    Inclui gabarito, resposta do aluno, pontos por questao, IP e user-agent.
    Nada disso pode alimentar tela de aluno, e nao alimenta: a area do aluno
    monta o resultado a partir de campos escalares da tentativa, sem tocar em
    questao nenhuma.
    """

    template_name = "admin_panel/attempts/detail.html"
    secao = "tentativas"

    def get(self, request, *args, **kwargs):
        self.tentativa = _tentativa_ou_404(kwargs["attempt_id"])
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from django.utils import timezone

        contexto = super().get_context_data(**kwargs)
        tentativa = self.tentativa

        contexto["tentativa"] = tentativa
        contexto["prova"] = tentativa.exam
        contexto["anulada"] = tentativa.status == AttemptStatus.RESET
        contexto["pode_resetar"] = reset_service.pode_resetar(tentativa)
        contexto["janela_aberta"] = reset_service.janela_aberta(
            tentativa.exam, timezone.now()
        )

        corrigida = tentativa.grading_status == GradingStatus.GRADED
        contexto["corrigida"] = corrigida
        contexto["nota"] = (
            grading.nota_para_exibicao(tentativa.final_score) if corrigida else ""
        )
        contexto["nota_minima"] = grading.nota_para_exibicao(
            tentativa.passing_score_snapshot
        )

        contexto["linhas"] = self._linhas(tentativa)
        contexto["certificado"] = self._certificado(tentativa)
        return contexto

    def _linhas(self, tentativa):
        """Questao a questao, com gabarito ao lado da resposta do aluno."""
        linhas = []
        consulta = (
            tentativa.questions.select_related("question", "graded_by")
            .prefetch_related("options__option", "answer__selected_options")
            .order_by("display_order")
        )
        for linha in consulta:
            resposta = getattr(linha, "answer", None)
            objetiva = linha.question.type in TIPOS_OBJETIVOS

            marcadas = set()
            if resposta is not None:
                marcadas = {
                    escolha.attempt_option_id
                    for escolha in resposta.selected_options.all()
                }

            alternativas = []
            if objetiva:
                for opcao in sorted(
                    linha.options.all(), key=lambda o: o.display_order
                ):
                    alternativas.append(
                        {
                            "texto": opcao.option.text,
                            "correta": opcao.option.is_correct,
                            "marcada": opcao.pk in marcadas,
                        }
                    )

            linhas.append(
                {
                    "linha": linha,
                    "objetiva": objetiva,
                    "alternativas": alternativas,
                    "texto": getattr(resposta, "text", "") or "",
                    "em_branco": resposta is None
                    or (not marcadas and not (getattr(resposta, "text", "") or "")),
                }
            )
        return linhas

    def _certificado(self, tentativa):
        from certificates.services import certificado_da_tentativa

        return certificado_da_tentativa(tentativa)


@require_POST
@admin_required
def attempt_reset(request, attempt_id):
    """
    Anula a tentativa. POST, CSRF, motivo obrigatorio.

    Uma segunda anulacao responde 409: o administrador precisa saber que o
    segundo clique nao fez nada, em vez de receber uma confirmacao falsa.
    """
    tentativa = _tentativa_ou_404(attempt_id)
    motivo = request.POST.get("motivo") or ""

    try:
        _, resumo = reset_service.reset_attempt(
            tentativa, actor=request.user, reason=motivo, request=request
        )
    except reset_service.TentativaJaAnulada as erro:
        # 409, e nao um redirect silencioso: quem clicou duas vezes precisa
        # saber que a segunda nao fez nada. Uma confirmacao de sucesso levaria
        # o administrador a acreditar que anulou de novo.
        from django.shortcuts import render

        return render(
            request,
            "admin_panel/exams/conflito.html",
            {"mensagens": erro.mensagens, "prova": tentativa.exam},
            status=409,
        )
    except DomainError as erro:
        messages.error(request, str(erro))
        return redirect("admin_panel:attempt_detail", attempt_id=tentativa.pk)

    partes = ["Tentativa anulada."]
    if resumo["certificate_revoked"]:
        partes.append("O certificado foi revogado.")
    if resumo["enrollment_reactivated"]:
        partes.append("A matricula voltou a ficar ativa.")
    messages.success(request, " ".join(partes))

    if not resumo["janela_aberta"]:
        # Aviso explicito, e nao um bypass: resetar libera o slot da
        # tentativa, e nao reabre a prova.
        messages.warning(
            request,
            "A tentativa foi resetada, porem a janela da prova esta encerrada. "
            "O aluno nao podera iniciar novamente ate que exista uma prova ou "
            "janela valida.",
        )

    return redirect("admin_panel:attempt_detail", attempt_id=tentativa.pk)
