"""
Rotas administrativas de certificados (namespace admin_panel).

Duas familias sob o mesmo prefixo:

    /certificados/          os documentos emitidos
    /certificados/modelos/  os modelos que definem como eles sao desenhados

A rota de modelos vem ANTES da rota de detalhe por id. `/modelos/` casaria
com `<int:certificate_id>` se a ordem fosse outra? Nao — "modelos" nao e
inteiro, e o conversor recusaria. A ordem aqui e por leitura, e nao por
necessidade: quem abre o arquivo ve o assunto novo junto do antigo.
"""

from django.urls import path

from certificates import views_admin as views
from certificates import views_templates as views_modelos

urlpatterns = [
    path(
        "certificados/",
        views.CertificateListView.as_view(),
        name="certificate_list",
    ),
    # --- modelos (Etapa 10) ----------------------------------------------
    #
    # Escrita sempre POST. Ativar um modelo troca a aparencia de todo
    # documento emitido dali para a frente; um GET faria disso um link.
    path(
        "certificados/modelos/",
        views_modelos.TemplateListView.as_view(),
        name="certificate_template_list",
    ),
    path(
        "certificados/modelos/novo/",
        views_modelos.TemplateCreateView.as_view(),
        name="certificate_template_create",
    ),
    path(
        "certificados/modelos/<int:pk>/editar/",
        views_modelos.TemplateEditView.as_view(),
        name="certificate_template_edit",
    ),
    path(
        "certificados/modelos/<int:pk>/dados/",
        views_modelos.template_update,
        name="certificate_template_update",
    ),
    path(
        "certificados/modelos/<int:pk>/campos/",
        views_modelos.template_save_fields,
        name="certificate_template_save_fields",
    ),
    # O editor visual salva por aqui, em JSON. A rota de cima continua
    # existindo para o formulario classico e para os scripts que o imitam.
    path(
        "certificados/modelos/<int:pk>/elementos/",
        views_modelos.template_save_elements,
        name="certificate_template_save_elements",
    ),
    path(
        "certificados/modelos/<int:pk>/arte/enviar/",
        views_modelos.template_background,
        name="certificate_template_background",
    ),
    path(
        "certificados/modelos/<int:pk>/arte/",
        views_modelos.TemplateBackgroundView.as_view(),
        name="certificate_template_art",
    ),
    path(
        "certificados/modelos/<int:pk>/preview.pdf",
        views_modelos.TemplatePreviewView.as_view(),
        name="certificate_template_preview",
    ),
    path(
        "certificados/modelos/<int:pk>/ativar/",
        views_modelos.template_activate,
        name="certificate_template_activate",
    ),
    path(
        "certificados/modelos/<int:pk>/arquivar/",
        views_modelos.template_archive,
        name="certificate_template_archive",
    ),
    path(
        "certificados/modelos/<int:pk>/duplicar/",
        views_modelos.template_duplicate,
        name="certificate_template_duplicate",
    ),
    path(
        "certificados/<int:certificate_id>/",
        views.CertificateDetailView.as_view(),
        name="certificate_detail",
    ),
    path(
        "certificados/<int:certificate_id>/baixar/",
        views.certificate_download_admin,
        name="certificate_download_admin",
    ),
    path(
        "certificados/<int:certificate_id>/revogar/",
        views.certificate_revoke,
        name="certificate_revoke",
    ),
]
