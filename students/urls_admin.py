"""
Rotas administrativas de alunos.

Sem app_name: estas rotas sao concatenadas ao namespace admin_panel, de modo
que todo o painel administrativo continua sendo um unico namespace enquanto
cada app mantem a propriedade das suas telas.
"""

from django.urls import path

from students import views

urlpatterns = [
    path("alunos/", views.StudentListView.as_view(), name="student_list"),
    path("alunos/novo/", views.StudentCreateView.as_view(), name="student_create"),
    path(
        "alunos/importar/",
        views.StudentImportUploadView.as_view(),
        name="student_import",
    ),
    path(
        "alunos/importar/preview/",
        views.StudentImportPreviewView.as_view(),
        name="student_import_preview",
    ),
    path(
        "alunos/importar/confirmar/",
        views.student_import_confirm,
        name="student_import_confirm",
    ),
    path(
        "alunos/importar/cancelar/",
        views.student_import_cancel,
        name="student_import_cancel",
    ),
    path("alunos/<int:pk>/", views.StudentDetailView.as_view(), name="student_detail"),
    path(
        "alunos/<int:pk>/editar/",
        views.StudentUpdateView.as_view(),
        name="student_update",
    ),
    path("alunos/<int:pk>/bloquear/", views.student_block, name="student_block"),
    path("alunos/<int:pk>/desbloquear/", views.student_unblock, name="student_unblock"),
]
