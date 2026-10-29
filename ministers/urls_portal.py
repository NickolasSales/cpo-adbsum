"""
Rotas do Portal do Ministro, sob /ministros/.

Namespace proprio (ministers), separado de /aluno/ e de /admin-panel/.
Nenhuma rota recebe id: o ministro e sempre o da sessao.
"""

from django.urls import path, re_path

from ministers import views_portal as views

app_name = "ministers"

urlpatterns = [
    path("", views.PainelMinistroView.as_view(), name="dashboard"),
    path("login/", views.LoginMinistroView.as_view(), name="login"),
    path("logout/", views.LogoutMinistroView.as_view(), name="logout"),
    path("perfil/", views.PerfilMinistroView.as_view(), name="profile"),
    path("mensalidades/", views.MensalidadesMinistroView.as_view(), name="fees"),
    # A competencia, e nao um id: /ministros/mensalidades/2026/10/. O
    # ministro continua vindo da sessao; o ano e o mes so escolhem qual das
    # mensalidades DELE.
    re_path(
        r"^mensalidades/(?P<ano>[0-9]{4})/(?P<mes>[0-9]{2})/$",
        views.CobrancaMinistroView.as_view(),
        name="fee_charge",
    ),
    re_path(
        r"^mensalidades/(?P<ano>[0-9]{4})/(?P<mes>[0-9]{2})/guia\.pdf$",
        views.GuiaMinistroView.as_view(),
        name="fee_guide",
    ),
    # Comprovante (Fase 2E): GET mostra o formulario, POST envia. Os
    # arquivos ja enviados sao numerados DENTRO da competencia (1, 2...),
    # na ordem de envio: nao ha id de comprovante na URL do portal.
    re_path(
        r"^mensalidades/(?P<ano>[0-9]{4})/(?P<mes>[0-9]{2})/comprovante/$",
        views.EnviarComprovanteView.as_view(),
        name="fee_proof",
    ),
    re_path(
        r"^mensalidades/(?P<ano>[0-9]{4})/(?P<mes>[0-9]{2})/comprovantes/(?P<numero>[0-9]{1,3})/$",
        views.ArquivoDoComprovanteView.as_view(),
        name="fee_proof_file",
    ),
    path("alterar-senha/", views.TrocarSenhaMinistroView.as_view(), name="change_password"),
]
