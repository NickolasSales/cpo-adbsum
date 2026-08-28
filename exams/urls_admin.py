"""Rotas administrativas de provas e questoes (namespace admin_panel)."""

from django.urls import path

from exams import views_admin as views

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
