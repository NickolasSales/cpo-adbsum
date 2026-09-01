"""
Rotas do painel administrativo.

O painel e um unico namespace (admin_panel), mas cada app de dominio e dona
das suas telas: este modulo apenas concatena as listas que elas exportam.
Uma etapa nova acrescenta um import aqui e nada mais.
"""

from django.urls import path

from accounts import urls_admin as accounts_admin
from audit import urls_admin as audit_admin
from certificates import urls_admin as certificates_admin
from common import views
from courses import urls_admin as courses_admin
from exams import urls_admin as exams_admin
from exams import urls_attempts_admin as exams_attempts_admin
from students import urls_admin as students_admin

app_name = "admin_panel"

urlpatterns = [
    path("", views.AdminDashboardView.as_view(), name="dashboard"),
    *accounts_admin.urlpatterns,
    *students_admin.urlpatterns,
    *courses_admin.urlpatterns,
    *exams_admin.urlpatterns,
    *exams_attempts_admin.urlpatterns,
    *certificates_admin.urlpatterns,
    *audit_admin.urlpatterns,
]
