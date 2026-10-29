"""
Rotas administrativas do Controle de Ministros.

Sem app_name: concatenadas ao namespace admin_panel, como as rotas de cada
app do CPO. Todas abaixo de /admin-panel/ministros/.
"""

from django.urls import path

from ministers import views_admin as views
from ministers import views_cobranca as cobranca
from ministers import views_comprovantes as comprovantes
from ministers import views_geracao as geracao
from ministers import views_mensalidades as fees

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
    # Mensalidades (Fase 2A)
    path("ministros/mensalidades/", fees.FeeDashboardView.as_view(), name="minister_fee_dashboard"),
    path("ministros/mensalidades/lista/", fees.FeeListView.as_view(), name="minister_fee_list"),
    path("ministros/mensalidades/nova/", fees.FeeCreateView.as_view(), name="minister_fee_create"),
    # Geracao em massa (Fase 2B)
    path("ministros/mensalidades/gerar/", geracao.GeracaoView.as_view(), name="minister_fee_generate"),
    path(
        "ministros/mensalidades/gerar/preview/",
        geracao.GeracaoPreviewView.as_view(),
        name="minister_fee_generate_preview",
    ),
    path(
        "ministros/mensalidades/gerar/confirmar/",
        geracao.geracao_confirmar,
        name="minister_fee_generate_confirm",
    ),
    path(
        "ministros/mensalidades/gerar/cancelar/",
        geracao.geracao_cancelar,
        name="minister_fee_generate_cancel",
    ),
    path(
        "ministros/mensalidades/gerar/resultado/<int:pk>/",
        geracao.GeracaoResultadoView.as_view(),
        name="minister_fee_generate_result",
    ),
    # Cobranca Pix (Fase 2D)
    path(
        "ministros/configuracoes/pagamento/",
        cobranca.ConfiguracaoPagamentoView.as_view(),
        name="minister_billing_settings",
    ),
    path("ministros/comprovantes/", comprovantes.ComprovantesView.as_view(), name="minister_proof_list"),
    path(
        "ministros/comprovantes/<int:pk>/",
        comprovantes.ComprovanteDetalheView.as_view(),
        name="minister_proof_detail",
    ),
    path(
        "ministros/comprovantes/<int:pk>/arquivo/",
        comprovantes.ComprovanteArquivoView.as_view(),
        name="minister_proof_file",
    ),
    path(
        "ministros/comprovantes/<int:pk>/baixar/",
        comprovantes.ComprovanteDownloadView.as_view(),
        name="minister_proof_download",
    ),
    path(
        "ministros/comprovantes/<int:pk>/aprovar/",
        comprovantes.AprovarComprovanteView.as_view(),
        name="minister_proof_approve",
    ),
    path(
        "ministros/comprovantes/<int:pk>/recusar/",
        comprovantes.RecusarComprovanteView.as_view(),
        name="minister_proof_reject",
    ),
    path("ministros/mensalidades/cobrancas/", cobranca.CobrancasView.as_view(), name="minister_charges"),
    path(
        "ministros/mensalidades/cobrancas/gerar/",
        cobranca.cobrancas_gerar,
        name="minister_charges_generate",
    ),
    path(
        "ministros/mensalidades/<int:pk>/cobranca/gerar/",
        cobranca.cobranca_gerar,
        name="minister_fee_charge_generate",
    ),
    path("ministros/mensalidades/<int:pk>/guia.pdf", cobranca.guia_admin, name="minister_fee_guide"),
    path("ministros/mensalidades/<int:pk>/", fees.FeeDetailView.as_view(), name="minister_fee_detail"),
    path(
        "ministros/mensalidades/<int:pk>/editar/",
        fees.FeeUpdateView.as_view(),
        name="minister_fee_update",
    ),
    path(
        "ministros/mensalidades/<int:pk>/pagamento/",
        fees.FeePaymentView.as_view(),
        name="minister_fee_payment",
    ),
    path(
        "ministros/mensalidades/<int:pk>/pagamento/corrigir/",
        fees.FeePaymentCorrectView.as_view(),
        name="minister_fee_payment_correct",
    ),
    path(
        "ministros/mensalidades/<int:pk>/pagamento/estornar/",
        fees.FeePaymentReverseView.as_view(),
        name="minister_fee_payment_reverse",
    ),
    path(
        "ministros/mensalidades/<int:pk>/cancelar/",
        fees.FeeCancelView.as_view(),
        name="minister_fee_cancel",
    ),
    path(
        "ministros/mensalidades/<int:pk>/isentar/",
        fees.FeeExemptView.as_view(),
        name="minister_fee_exempt",
    ),
    path(
        "ministros/mensalidades/<int:pk>/reativar/",
        fees.FeeReactivateView.as_view(),
        name="minister_fee_reactivate",
    ),
]
