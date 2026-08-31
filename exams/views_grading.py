"""
Telas administrativas de correcao e notas.

Duas telas de lista e duas de detalhe:

    /admin-panel/correcoes/            fila do que espera avaliador
    /admin-panel/correcoes/<uuid>/     corrigir uma tentativa
    /admin-panel/notas/                notas fechadas
    /admin-panel/notas/<uuid>/         detalhe de uma nota
    /admin-panel/notas/exportar/       CSV do que estiver filtrado

Todas exigem ADMIN, por PainelAdminMixin. Um STUDENT autenticado recebe 403 —
nao 404 — porque aqui a rota nao e sobre um recurso dele: e uma area
administrativa cuja existencia nao e segredo.

O identificador na URL e o public_id da tentativa, o mesmo UUID que o aluno ja
usa. Nao ha ganho em expor a PK sequencial numa tela nova, e usar o mesmo
identificador nas duas areas evita ter dois vocabularios para a mesma coisa.

Esta e area administrativa: o gabarito PODE aparecer aqui, e aparece — sem ele
nao ha como conferir a correcao automatica. O que nunca acontece e o contrario:
nenhuma destas views alimenta tela de aluno.

O que o navegador pode influenciar
----------------------------------
Na correcao: qual questao, quantos pontos, qual comentario. Nada mais.
Somatorios, nota, resultado e situacao sao calculados pelo servico. Um POST
que traga final_score, result ou points_snapshot e simplesmente ignorado,
porque nao ha nada aqui que leia esses nomes.
"""

import csv

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import ListView, TemplateView

from common.exceptions import DomainError
from common.mixins import admin_required
from common.views import PainelAdminMixin
from courses.models import Module
from exams.models import (
    AttemptResult,
    ExamAttempt,
    GradingStatus,
    QuestionGradingStatus,
)
from exams.services import grading

POR_PAGINA = 25


# ---------------------------------------------------------------------------
# Filtros compartilhados pelas duas listas
# ---------------------------------------------------------------------------


def _aplicar_filtros(consulta, request):
    """
    Filtros comuns de correcoes e notas.

    Uma funcao so para as duas telas: os filtros sao os mesmos e, separados,
    um dia divergiriam no formato de data ou no nome do parametro.
    """
    busca = (request.GET.get("q") or "").strip()
    modulo = (request.GET.get("modulo") or "").strip()
    prova = (request.GET.get("prova") or "").strip()
    de = (request.GET.get("de") or "").strip()
    ate = (request.GET.get("ate") or "").strip()

    if busca:
        from django.db.models import Q

        consulta = consulta.filter(
            Q(student__full_name__icontains=busca)
            | Q(student__email__icontains=busca)
            | Q(exam__title__icontains=busca)
        )
    if modulo.isdigit():
        consulta = consulta.filter(exam__module_id=int(modulo))
    if prova.isdigit():
        consulta = consulta.filter(exam_id=int(prova))

    # Datas invalidas sao ignoradas em silencio, e nao viram erro 500: o campo
    # e um filtro de conveniencia, e um usuario digitando "31/02" nao deve
    # derrubar a tela.
    inicio = _data_ou_none(de)
    fim = _data_ou_none(ate)
    if inicio:
        consulta = consulta.filter(submitted_at__date__gte=inicio)
    if fim:
        consulta = consulta.filter(submitted_at__date__lte=fim)

    filtros = {
        "busca": busca,
        "filtro_modulo": modulo,
        "filtro_prova": prova,
        "filtro_de": de,
        "filtro_ate": ate,
    }
    return consulta, filtros


def _data_ou_none(texto):
    from datetime import date

    if not texto:
        return None
    try:
        ano, mes, dia = (int(p) for p in texto.split("-"))
        return date(ano, mes, dia)
    except (ValueError, TypeError):
        return None


def _contexto_de_filtros(contexto, request):
    contexto["modulos"] = Module.objects.order_by("code")
    from exams.models import Exam

    contexto["provas"] = Exam.objects.order_by("title").only("pk", "title")
    return contexto


def _tentativa_ou_404(public_id, *, situacoes=None):
    """
    Tentativa pelo identificador publico, com o que as telas precisam.

    404 para UUID malformado tambem: um identificador invalido nao e um erro
    do servidor, e simplesmente nao existe.
    """
    import uuid as _uuid

    try:
        _uuid.UUID(str(public_id))
    except (ValueError, AttributeError, TypeError):
        raise Http404("Tentativa inexistente.")

    consulta = ExamAttempt.objects.select_related(
        "student", "exam", "exam__module"
    ).filter(public_id=public_id)

    if situacoes is not None:
        consulta = consulta.filter(grading_status__in=situacoes)

    tentativa = consulta.first()
    if tentativa is None:
        raise Http404("Tentativa inexistente.")
    return tentativa


# ---------------------------------------------------------------------------
# Correcoes
# ---------------------------------------------------------------------------


class CorrectionListView(PainelAdminMixin, ListView):
    """Fila de tentativas que aguardam um avaliador humano."""

    template_name = "admin_panel/grading/list.html"
    context_object_name = "pagina"
    paginate_by = POR_PAGINA
    secao = "correcoes"

    def get_queryset(self):
        consulta, self.filtros = _aplicar_filtros(
            grading.tentativas_para_corrigir(), self.request
        )
        return consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(self.filtros)
        contexto["total_geral"] = grading.tentativas_para_corrigir().count()

        # Uma consulta por tentativa da pagina, e nao da tabela inteira: o
        # numero de pendentes so faz sentido linha a linha, e a pagina tem no
        # maximo 25.
        contexto["tentativas"] = [
            {
                "tentativa": tentativa,
                "pendentes": len(grading.questoes_manuais_pendentes(tentativa)),
            }
            for tentativa in contexto["pagina"]
        ]
        return _contexto_de_filtros(contexto, self.request)


class CorrectionDetailView(PainelAdminMixin, TemplateView):
    """
    Correcao de uma tentativa, questao por questao.

    Objetivas aparecem em bloco somente leitura, com o gabarito ao lado: e o
    que permite conferir a correcao automatica. Somente as manuais tem campo
    editavel, e o servico recusa nota manual em questao objetiva mesmo que o
    POST venha forjado.
    """

    template_name = "admin_panel/grading/detail.html"
    secao = "correcoes"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        tentativa = _tentativa_ou_404(self.kwargs["public_id"])

        contexto["tentativa"] = tentativa
        contexto["linhas"] = _montar_linhas(tentativa)
        contexto["pendentes"] = grading.questoes_manuais_pendentes(tentativa)
        contexto["ja_finalizada"] = (
            tentativa.grading_status == GradingStatus.GRADED
        )
        contexto["nota_exibida"] = grading.nota_para_exibicao(tentativa.final_score)
        return contexto


def _montar_linhas(tentativa):
    """
    Prepara cada questao para a tela de correcao.

    Monta aqui, e nao no template: decidir o que e resposta do aluno e o que e
    gabarito exige olhar o tipo da questao, e essa e logica de apresentacao que
    nao cabe em tag de template.
    """
    linhas = []
    for linha in grading.linhas_da_correcao(tentativa):
        resposta = getattr(linha, "answer", None)

        marcadas = []
        corretas = []
        for alternativa in sorted(
            linha.options.all(), key=lambda o: o.display_order
        ):
            if alternativa.option.is_correct:
                corretas.append(alternativa.option.text)

        if resposta is not None:
            marcadas = [
                selecao.attempt_option.option.text
                for selecao in resposta.selected_options.all()
            ]

        linhas.append(
            {
                "id": linha.pk,
                "numero": linha.display_order + 1,
                "questao": linha.question,
                "tipo": linha.question.get_type_display(),
                "automatica": grading.e_automatica(linha),
                "valor": linha.points_snapshot or linha.question.points,
                "texto_resposta": (resposta.text_answer if resposta else "") or "",
                "marcadas": marcadas,
                "corretas": corretas,
                "pontos": linha.awarded_points,
                "comentario": linha.grader_comment,
                "situacao": linha.grading_status,
                "pendente": linha.grading_status == QuestionGradingStatus.PENDING,
            }
        )
    return linhas


@admin_required
@require_POST
def correction_save(request, public_id):
    """Salva a nota de UMA questao manual. Nao finaliza a correcao."""
    tentativa = _tentativa_ou_404(public_id)

    try:
        grading.save_manual_grade(
            tentativa,
            question_id=request.POST.get("questao"),
            points=request.POST.get("pontos"),
            comment=request.POST.get("comentario", ""),
            actor=request.user,
            request=request,
        )
    except DomainError as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, "Nota registrada.")

    return redirect("admin_panel:correction_detail", public_id=tentativa.public_id)


@admin_required
@require_POST
def correction_finalize(request, public_id):
    """
    Fecha a nota da tentativa.

    Recusa com 409 e a lista do que falta quando ha questao manual sem nota:
    o pedido era valido, o estado e que nao permite atende-lo.
    """
    tentativa = _tentativa_ou_404(public_id)

    try:
        grading.finalize_grading(tentativa, actor=request.user, request=request)
    except grading.ManuaisPendentes as erro:
        contexto = {
            "tentativa": tentativa,
            "linhas": _montar_linhas(tentativa),
            "pendentes": erro.numeros,
            "erro_de_finalizacao": str(erro),
            "ja_finalizada": False,
            "nota_exibida": "",
            "secao": "correcoes",
        }
        from common.navigation import MENU_ADMIN, MENU_ADMIN_FUTURO

        contexto["itens_menu"] = MENU_ADMIN
        contexto["itens_futuros"] = MENU_ADMIN_FUTURO
        from django.shortcuts import render

        return render(
            request, "admin_panel/grading/detail.html", contexto, status=409
        )
    except DomainError as erro:
        messages.error(request, str(erro))
        return redirect(
            "admin_panel:correction_detail", public_id=tentativa.public_id
        )

    messages.success(request, "Correcao finalizada.")
    return redirect("admin_panel:grade_detail", public_id=tentativa.public_id)


# ---------------------------------------------------------------------------
# Notas
# ---------------------------------------------------------------------------


class GradeListView(PainelAdminMixin, ListView):
    """Notas fechadas."""

    template_name = "admin_panel/grades/list.html"
    context_object_name = "pagina"
    paginate_by = POR_PAGINA
    secao = "notas"

    def get_queryset(self):
        consulta, self.filtros = _aplicar_filtros(
            grading.tentativas_corrigidas(), self.request
        )
        self.filtro_resultado = (self.request.GET.get("resultado") or "").strip()
        if self.filtro_resultado in AttemptResult.values:
            consulta = consulta.filter(result=self.filtro_resultado)
        return consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(self.filtros)
        contexto["filtro_resultado"] = self.filtro_resultado
        contexto["resultados"] = AttemptResult.choices
        contexto["total_geral"] = grading.tentativas_corrigidas().count()

        contexto["notas"] = [
            {
                "tentativa": tentativa,
                "nota": grading.nota_para_exibicao(tentativa.final_score),
                "minima": grading.nota_para_exibicao(
                    tentativa.passing_score_snapshot
                ),
            }
            for tentativa in contexto["pagina"]
        ]
        return _contexto_de_filtros(contexto, self.request)


class GradeDetailView(PainelAdminMixin, TemplateView):
    """Detalhe de uma nota fechada, questao por questao."""

    template_name = "admin_panel/grades/detail.html"
    secao = "notas"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        tentativa = _tentativa_ou_404(
            self.kwargs["public_id"], situacoes=[GradingStatus.GRADED]
        )

        contexto["tentativa"] = tentativa
        contexto["linhas"] = _montar_linhas(tentativa)
        contexto["nota_exibida"] = grading.nota_para_exibicao(tentativa.final_score)
        contexto["minima_exibida"] = grading.nota_para_exibicao(
            tentativa.passing_score_snapshot
        )
        return contexto


COLUNAS_CSV = [
    "nome",
    "email",
    "modulo",
    "prova",
    "tentativa",
    "pontos",
    "nota",
    "nota_minima",
    "resultado",
    "data",
]


@admin_required
def grade_export(request):
    """
    Exporta as notas filtradas em CSV.

    O que NAO entra: senha, resposta do aluno, gabarito, comentario do
    avaliador e token. O arquivo circula por e-mail e pendrive; ele carrega o
    resultado, nao o conteudo da prova.

    BOM UTF-8 no inicio: sem ele o Excel em portugues abre o arquivo como
    Latin-1 e todo nome acentuado chega torto. O BOM e feio e e a unica coisa
    que faz o Excel acertar sem o usuario configurar importacao.

    GET, e nao POST: e uma consulta, nao altera nada, e precisa poder ser
    reproduzida colando a URL com os mesmos filtros.
    """
    consulta, _ = _aplicar_filtros(grading.tentativas_corrigidas(), request)
    resultado = (request.GET.get("resultado") or "").strip()
    if resultado in AttemptResult.values:
        consulta = consulta.filter(result=resultado)

    resposta = HttpResponse(content_type="text/csv; charset=utf-8")
    resposta["Content-Disposition"] = 'attachment; filename="notas-{}.csv"'.format(
        timezone.now().strftime("%Y-%m-%d")
    )
    resposta.write("﻿")

    escritor = csv.writer(resposta, delimiter=";")
    escritor.writerow(COLUNAS_CSV)

    for tentativa in consulta.iterator(chunk_size=200):
        escritor.writerow(
            [
                tentativa.student.full_name,
                tentativa.student.email,
                tentativa.exam.module.code,
                tentativa.exam.title,
                tentativa.attempt_number,
                grading.nota_para_exibicao(tentativa.obtained_points),
                grading.nota_para_exibicao(tentativa.final_score),
                grading.nota_para_exibicao(tentativa.passing_score_snapshot),
                tentativa.get_result_display() if tentativa.result else "",
                timezone.localtime(tentativa.graded_at).strftime("%d/%m/%Y %H:%M")
                if tentativa.graded_at
                else "",
            ]
        )

    return resposta
