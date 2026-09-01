"""
URLconf raiz do CPO Provas.

Organizacao dos prefixos:

    /django-admin/   ferramenta tecnica, nao e a interface oficial
    /health/         verificacao de aplicacao e banco
    /login/ ...      autenticacao
    /admin-panel/    interface administrativa propria
    /aluno/          painel do aluno
    /certificados/   validacao publica, sem autenticacao
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("admin-panel/", include("common.urls_admin_panel")),
    path("aluno/", include("common.urls_student")),
    path("", include("certificates.urls_public")),
    path("", include("common.urls")),
]

# Identificacao do Django Admin, para deixar evidente que ele nao e a
# interface administrativa do produto.
admin.site.site_header = "CPO Provas - administracao tecnica"
admin.site.site_title = "CPO Provas"
admin.site.index_title = "Ferramenta tecnica e emergencial"
