"""
URLconf raiz.

Organizacao dos prefixos:

    /django-admin/   ferramenta tecnica, nao e a interface oficial
    /health/         verificacao de aplicacao e banco
    /login/ ...      autenticacao
    /admin-panel/    interface administrativa propria
    /aluno/          painel do aluno
    /certificados/   validacao publica, sem autenticacao
"""

from django.conf import settings
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
#
# O nome vem de APP_NAME como em qualquer outra tela. O Django Admin e uso
# tecnico, mas ainda e uma pagina que abre num navegador — deixar a identidade
# antiga escrita aqui a manteria viva no unico lugar onde ninguem procuraria.
admin.site.site_header = "{} - administracao tecnica".format(settings.APP_NAME)
admin.site.site_title = settings.APP_NAME
admin.site.index_title = "Ferramenta tecnica e emergencial"
