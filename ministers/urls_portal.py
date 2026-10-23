"""
Rotas do Portal do Ministro, sob /ministros/.

Namespace proprio (ministers), separado de /aluno/ e de /admin-panel/.
Nenhuma rota recebe id: o ministro e sempre o da sessao.
"""

from django.urls import path

from ministers import views_portal as views

app_name = "ministers"

urlpatterns = [
    path("", views.PainelMinistroView.as_view(), name="dashboard"),
    path("login/", views.LoginMinistroView.as_view(), name="login"),
    path("logout/", views.LogoutMinistroView.as_view(), name="logout"),
    path("perfil/", views.PerfilMinistroView.as_view(), name="profile"),
    path("alterar-senha/", views.TrocarSenhaMinistroView.as_view(), name="change_password"),
]
