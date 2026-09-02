"""Rotas administrativas de modulos e matriculas (namespace admin_panel)."""

from django.urls import path

from courses import views

urlpatterns = [
    # Modulos
    path("modulos/", views.ModuleListView.as_view(), name="module_list"),
    path("modulos/novo/", views.ModuleCreateView.as_view(), name="module_create"),
    path("modulos/<int:pk>/", views.ModuleDetailView.as_view(), name="module_detail"),
    path(
        "modulos/<int:pk>/editar/",
        views.ModuleUpdateView.as_view(),
        name="module_update",
    ),
    path("modulos/<int:pk>/desativar/", views.module_disable, name="module_disable"),
    path("modulos/<int:pk>/ativar/", views.module_enable, name="module_enable"),
    # Matriculas
    path("matriculas/", views.EnrollmentListView.as_view(), name="enrollment_list"),
    path(
        "matriculas/nova/",
        views.EnrollmentCreateView.as_view(),
        name="enrollment_create",
    ),
    path(
        "matriculas/<int:pk>/bloquear/",
        views.enrollment_block,
        name="enrollment_block",
    ),
    path(
        "matriculas/<int:pk>/liberar/",
        views.enrollment_unblock,
        name="enrollment_unblock",
    ),
    path(
        "matriculas/<int:pk>/desativar/",
        views.enrollment_disable,
        name="enrollment_disable",
    ),
    path(
        "matriculas/<int:pk>/reativar/",
        views.enrollment_reactivate,
        name="enrollment_reactivate",
    ),
    path(
        "matriculas/<int:pk>/concluir/",
        views.enrollment_complete,
        name="enrollment_complete",
    ),
    # Revogacao, exclusao e restauracao (Etapa 9).
    #
    # Cada operacao tem duas rotas: a confirmacao, que e GET e nao altera
    # nada, e a escrita, que e POST e recusa GET com 405.
    path(
        "matriculas/<int:pk>/revogar/confirmar/",
        views.EnrollmentRevokeConfirmView.as_view(),
        name="enrollment_revoke_confirm",
    ),
    path(
        "matriculas/<int:pk>/revogar/",
        views.enrollment_revoke,
        name="enrollment_revoke",
    ),
    path(
        "matriculas/<int:pk>/excluir/confirmar/",
        views.EnrollmentDeleteConfirmView.as_view(),
        name="enrollment_delete_confirm",
    ),
    path(
        "matriculas/<int:pk>/excluir/",
        views.enrollment_delete,
        name="enrollment_delete",
    ),
    path(
        "matriculas/<int:pk>/restaurar/confirmar/",
        views.EnrollmentRestoreConfirmView.as_view(),
        name="enrollment_restore_confirm",
    ),
    path(
        "matriculas/<int:pk>/restaurar/",
        views.enrollment_restore,
        name="enrollment_restore",
    ),
]
