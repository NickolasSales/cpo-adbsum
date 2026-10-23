"""
Rotas administrativas do Controle de Ministros.

Sem app_name: concatenadas ao namespace admin_panel, como as rotas de cada
app do CPO. Todas abaixo de /admin-panel/ministros/.
"""

from django.urls import path

from ministers import views_admin as views

urlpatterns = [
    path("ministros/", views.MinisterDashboardView.as_view(), name="minister_dashboard"),
    path("ministros/cadastro/", views.MinisterListView.as_view(), name="minister_list"),
    path("ministros/novo/", views.MinisterCreateView.as_view(), name="minister_create"),
    path("ministros/importar/", views.MinisterImportUploadView.as_view(), name="minister_import"),
    path(
        "ministros/importar/preview/",
        views.MinisterImportPreviewView.as_view(),
        name="minister_import_preview",
    ),
    path(
        "ministros/importar/confirmar/",
        views.minister_import_confirm,
        name="minister_import_confirm",
    ),
    path(
        "ministros/importar/cancelar/",
        views.minister_import_cancel,
        name="minister_import_cancel",
    ),
    path("ministros/<int:pk>/", views.MinisterDetailView.as_view(), name="minister_detail"),
    path("ministros/<int:pk>/editar/", views.MinisterUpdateView.as_view(), name="minister_update"),
    path("ministros/<int:pk>/inativar/", views.minister_deactivate, name="minister_deactivate"),
    path("ministros/<int:pk>/reativar/", views.minister_reactivate, name="minister_reactivate"),
    path(
        "ministros/<int:pk>/acesso/criar/",
        views.MinisterAccountCreateView.as_view(),
        name="minister_account_create",
    ),
    path(
        "ministros/<int:pk>/acesso/bloquear/",
        views.minister_account_block,
        name="minister_account_block",
    ),
    path(
        "ministros/<int:pk>/acesso/desbloquear/",
        views.minister_account_unblock,
        name="minister_account_unblock",
    ),
    path(
        "ministros/<int:pk>/acesso/resetar-senha/",
        views.MinisterPasswordResetView.as_view(),
        name="minister_password_reset",
    ),
]
