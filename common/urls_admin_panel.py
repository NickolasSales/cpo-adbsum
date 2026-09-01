"""
Rotas do painel administrativo.

O painel e um unico namespace (admin_panel), mas cada app de dominio e dona
das suas telas: este modulo apenas concatena as listas que elas exportam.
Uma etapa nova acrescenta um import aqui e nada mais.
"""

from django.urls import path

from certificates import urls_admin as certificates_admin
from common import views
from courses import urls_admin as courses_admin
from exams import urls_admin as exams_admin
from students import urls_admin as students_admin

app_name = "admin_panel"

urlpatterns = [
    path("", views.AdminDashboardView.as_view(), name="dashboard"),
    *students_admin.urlpatterns,
    *courses_admin.urlpatterns,
    *exams_admin.urlpatterns,
    *certificates_admin.urlpatterns,
]
