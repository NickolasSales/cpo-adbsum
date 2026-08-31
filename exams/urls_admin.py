"""Rotas administrativas de provas e questoes (namespace admin_panel)."""

from django.urls import path

from exams import views_admin as views
from exams import views_grading as grading_views

urlpatterns = [
    # Provas
    path("provas/", views.ExamListView.as_view(), name="exam_list"),
    path("provas/nova/", views.ExamCreateView.as_view(), name="exam_create"),
    path("provas/<int:pk>/", views.ExamDetailView.as_view(), name="exam_detail"),
    path(
        "provas/<int:pk>/editar/", views.ExamUpdateView.as_view(), name="exam_update"
    ),
    # Acoes: sempre POST. Publicar, fechar, duplicar e remover senha mudam o
    # estado da prova e nao podem ser disparadas por um link.
    path("provas/<int:pk>/publicar/", views.exam_publish, name="exam_publish"),
    path("provas/<int:pk>/fechar/", views.exam_close, name="exam_close"),
    path("provas/<int:pk>/duplicar/", views.exam_duplicate, name="exam_duplicate"),
    path(
        "provas/<int:pk>/senha/",
        views.ExamPasswordView.as_view(),
        name="exam_password",
    ),
    path(
        "provas/<int:pk>/senha/remover/",
        views.exam_password_remove,
        name="exam_password_remove",
    ),
    # Gabarito e preview
    path("provas/<int:pk>/gabarito/", views.GabaritoView.as_view(), name="exam_gabarito"),
    path("provas/<int:pk>/preview/", views.ExamPreviewView.as_view(), name="exam_preview"),
    # Questoes
    path(
        "provas/<int:exam_id>/questoes/",
        views.QuestionListView.as_view(),
        name="question_list",
    ),
    path(
        "provas/<int:exam_id>/questoes/nova/",
        views.QuestionCreateView.as_view(),
        name="question_create",
    ),
    path(
        "provas/<int:exam_id>/questoes/<int:question_id>/editar/",
        views.QuestionUpdateView.as_view(),
        name="question_update",
    ),
    path(
        "provas/<int:exam_id>/questoes/<int:question_id>/excluir/",
        views.question_delete,
        name="question_delete",
    ),
]

# ---------------------------------------------------------------------------
# Correcao e notas (Etapa 5)
#
# O identificador e o public_id da tentativa, o mesmo UUID que o aluno usa.
# Nao ha ganho em expor a PK sequencial numa tela nova, e um identificador
# unico para as duas areas evita dois vocabularios para a mesma coisa.
#
# Salvar e finalizar sao POST. Exportar e GET porque e consulta: nao altera
# nada e precisa poder ser reproduzida colando a URL com os mesmos filtros.
# ---------------------------------------------------------------------------

urlpatterns += [
    path(
        "correcoes/",
        grading_views.CorrectionListView.as_view(),
        name="correction_list",
    ),
    path(
        "correcoes/<uuid:public_id>/",
        grading_views.CorrectionDetailView.as_view(),
        name="correction_detail",
    ),
    path(
        "correcoes/<uuid:public_id>/salvar/",
        grading_views.correction_save,
        name="correction_save",
    ),
    path(
        "correcoes/<uuid:public_id>/finalizar/",
        grading_views.correction_finalize,
        name="correction_finalize",
    ),
    path("notas/", grading_views.GradeListView.as_view(), name="grade_list"),
    path(
        "notas/exportar/", grading_views.grade_export, name="grade_export"
    ),
    path(
        "notas/<uuid:public_id>/",
        grading_views.GradeDetailView.as_view(),
        name="grade_detail",
    ),
]
