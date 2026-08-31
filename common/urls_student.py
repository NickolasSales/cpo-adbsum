"""
Rotas do painel do aluno.

Mesma estrategia do painel administrativo: um unico namespace (student), com
cada app de dominio exportando as suas rotas.
"""

from django.urls import path

from common import views
from courses import urls_student as courses_student
from exams import urls_student as exams_student

app_name = "student"

urlpatterns = [
    path("", views.StudentDashboardView.as_view(), name="dashboard"),
    *courses_student.urlpatterns,
    *exams_student.urlpatterns,
]
