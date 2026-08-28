"""
Rotas do painel do aluno.

Mesma estrategia do painel administrativo: um unico namespace (student), com
cada app de dominio exportando as suas rotas. As provas entram na Etapa 4.
"""

from django.urls import path

from common import views
from courses import urls_student as courses_student

app_name = "student"

urlpatterns = [
    path("", views.StudentDashboardView.as_view(), name="dashboard"),
    *courses_student.urlpatterns,
]
