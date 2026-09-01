"""Rotas administrativas de certificados (namespace admin_panel)."""

from django.urls import path

from certificates import views_admin as views

urlpatterns = [
    path(
        "certificados/",
        views.CertificateListView.as_view(),
        name="certificate_list",
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
