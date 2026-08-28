from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.EntrarView.as_view(), name="login"),
    path("logout/", views.SairView.as_view(), name="logout"),
    path("alterar-senha/", views.TrocarSenhaView.as_view(), name="change_password"),
]
