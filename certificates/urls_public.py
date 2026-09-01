"""
Rota publica de validacao de certificado.

Namespace proprio (certificates) e prefixo curto de proposito: este endereco
vai impresso dentro de um QR Code e, quando o leitor falha, alguem digita a
mao. Quanto menor e mais previsivel, melhor.

    /certificados/validar/<uuid>/

Fica fora de /aluno/ e de /admin-panel/ porque nao pertence a nenhuma das duas
areas: quem abre normalmente nao tem conta no sistema.
"""

from django.urls import path

from certificates import views_public as views

app_name = "certificates"

urlpatterns = [
    path(
        "certificados/validar/<uuid:verification_code>/",
        views.CertificateValidateView.as_view(),
        name="validate",
    ),
]
