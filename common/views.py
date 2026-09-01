"""
Views do esqueleto da aplicacao.

A app common concentra o que e transversal: health check, raiz do site e os
paineis de entrada de cada papel. As telas de dominio (alunos, modulos,
provas, correcoes, certificados) serao acrescentadas pelas apps proprias nas
etapas seguintes, sob os mesmos prefixos /admin-panel/ e /aluno/.
"""

import logging

from django.db import connections
from django.http import JsonResponse
from django.shortcuts import redirect, resolve_url
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from common.mixins import AdminRequiredMixin, StudentRequiredMixin
from common.navigation import MENU_ADMIN, MENU_ADMIN_FUTURO, url_do_painel

logger = logging.getLogger("cpo.common")


@require_GET
@never_cache
def health_check(request):
    """
    Verifica aplicacao e banco.

    Devolve apenas dois campos de estado. Nenhum detalhe de infraestrutura
    aparece na resposta: nem host, nem porta, nem versao, nem traceback. Um
    endpoint de health e publico por natureza e nao pode virar fonte de
    reconhecimento para quem esta sondando o servidor.
    """
    banco_ok = True

    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        banco_ok = False
        # Apenas o tipo da excecao vai para o log. A mensagem do driver
        # carrega host, porta e usuario do banco.
        logger.error(
            "Health check: falha na verificacao do banco (%s)", exc.__class__.__name__
        )

    corpo = {
        "status": "ok" if banco_ok else "error",
        "database": "ok" if banco_ok else "error",
    }
    return JsonResponse(corpo, status=200 if banco_ok else 503)


def root(request):
    """
    Raiz do site.

    Encaminha o usuario autenticado ao painel do seu papel e o anonimo ao
    login. Nao existe pagina publica na raiz nesta etapa.
    """
    return redirect(resolve_url(url_do_painel(request.user)))


class PainelAdminMixin(AdminRequiredMixin):
    """
    Base das telas do painel administrativo.

    Alem de exigir o papel ADMIN, injeta o que o layout lateral precisa. As
    telas das proximas etapas herdam daqui e ganham o menu de graca, sem
    repetir contexto.
    """

    secao = None

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["secao"] = self.secao
        contexto["itens_menu"] = MENU_ADMIN
        contexto["itens_futuros"] = MENU_ADMIN_FUTURO
        return contexto


class AdminDashboardView(PainelAdminMixin, TemplateView):
    """Painel administrativo com os indicadores ja disponiveis."""

    template_name = "admin_panel/dashboard.html"
    secao = "dashboard"

    def get_context_data(self, **kwargs):
        from accounts.models import User, UserRole
        from certificates.models import Certificate, CertificateStatus
        from courses.models import Module
        from exams.models import Exam, ExamAttempt, ExamStatus, GradingStatus

        contexto = super().get_context_data(**kwargs)

        alunos = User.objects.filter(role=UserRole.STUDENT)
        total_alunos = alunos.count()
        alunos_ativos = alunos.filter(is_active=True).count()

        modulos = Module.objects.all()
        total_modulos = modulos.count()
        modulos_ativos = modulos.filter(is_active=True).count()

        provas = Exam.objects.all()
        total_provas = provas.count()
        provas_publicadas = provas.filter(status=ExamStatus.PUBLISHED).count()

        aguardando = ExamAttempt.objects.filter(
            grading_status=GradingStatus.AWAITING_REVIEW
        ).count()

        certificados = Certificate.objects.all()
        total_certificados = certificados.count()
        certificados_validos = certificados.filter(
            status=CertificateStatus.ACTIVE
        ).count()

        # Cards com valor real ganham link. Os dominios ainda inexistentes
        # continuam sem numero e sem destino, para nao prometer tela que nao
        # existe.
        contexto["cards"] = [
            {
                "titulo": "Alunos",
                "valor": total_alunos,
                "nota": "{} ativo(s)".format(alunos_ativos),
                "url": "admin_panel:student_list",
            },
            {
                "titulo": "Modulos",
                "valor": total_modulos,
                "nota": "{} ativo(s)".format(modulos_ativos),
                "url": "admin_panel:module_list",
            },
            {
                "titulo": "Provas",
                "valor": total_provas,
                "nota": "{} publicada(s)".format(provas_publicadas),
                "url": "admin_panel:exam_list",
            },
            {
                "titulo": "Aguardando correcao",
                "valor": aguardando,
                "nota": "tentativa(s) na fila",
                "url": "admin_panel:correction_list",
            },
            {
                "titulo": "Certificados",
                "valor": total_certificados,
                "nota": "{} valido(s)".format(certificados_validos),
                "url": "admin_panel:certificate_list",
            },
        ]
        return contexto


class StudentDashboardView(StudentRequiredMixin, TemplateView):
    """Painel do aluno com os modulos liberados."""

    template_name = "student/dashboard.html"

    def get_context_data(self, **kwargs):
        from courses.services import modulos_do_aluno

        contexto = super().get_context_data(**kwargs)
        # Criterio unico, definido em courses.services: matricula ativa, com
        # acesso liberado, em modulo ativo.
        contexto["modulos"] = modulos_do_aluno(self.request.user)
        return contexto
