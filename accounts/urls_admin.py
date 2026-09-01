"""
Rotas administrativas de contas administrativas (namespace admin_panel).

Sem app_name: estas rotas sao concatenadas ao namespace admin_panel, como as
demais telas do painel.

Bloquear e desbloquear sao POST. Um GET que desativasse uma conta
administrativa poderia ser disparado por um link colado num chat, e o alvo
provavel seria justamente quem tem mais permissao.
"""

from django.urls import path

from accounts import views_admin as views

urlpatterns = [
    path(
        "administradores/",
        views.AdminUserListView.as_view(),
        name="admin_user_list",
    ),
    path(
        "administradores/novo/",
        views.AdminUserCreateView.as_view(),
        name="admin_user_create",
    ),
    path(
        "administradores/<int:pk>/",
        views.AdminUserDetailView.as_view(),
        name="admin_user_detail",
    ),
    path(
        "administradores/<int:pk>/editar/",
        views.AdminUserUpdateView.as_view(),
        name="admin_user_update",
    ),
    path(
        "administradores/<int:pk>/resetar-senha/",
        views.AdminUserPasswordResetView.as_view(),
        name="admin_user_password_reset",
    ),
    path(
        "administradores/<int:pk>/bloquear/",
        views.admin_user_block,
        name="admin_user_block",
    ),
    path(
        "administradores/<int:pk>/desbloquear/",
        views.admin_user_unblock,
        name="admin_user_unblock",
    ),
]
