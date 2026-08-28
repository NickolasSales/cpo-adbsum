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
]
