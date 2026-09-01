"""
Rotas administrativas de tentativas (namespace admin_panel).

Resetar e POST. Um GET que anulasse uma tentativa poderia ser disparado por um
link colado num chat, e apagaria a validade do trabalho de um aluno sem que
ninguem tivesse decidido isso.
"""

from django.urls import path

from exams import views_attempts_admin as views

urlpatterns = [
    path(
        "tentativas/",
        views.AttemptListView.as_view(),
        name="attempt_list",
    ),
    path(
        "tentativas/<int:attempt_id>/",
        views.AttemptDetailView.as_view(),
        name="attempt_detail",
    ),
    path(
        "tentativas/<int:attempt_id>/resetar/",
        views.attempt_reset,
        name="attempt_reset",
    ),
]
