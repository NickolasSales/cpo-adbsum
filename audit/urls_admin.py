"""
Rotas da trilha de auditoria (namespace admin_panel).

Somente GET. Nao existe rota de escrita aqui, e a ausencia e a
funcionalidade: uma trilha alteravel pela mesma interface que ela audita nao
serve para investigar nada.
"""

from django.urls import path

from audit import views_admin as views

urlpatterns = [
    path("logs/", views.AuditLogListView.as_view(), name="audit_log_list"),
    path(
        "logs/<int:log_id>/",
        views.AuditLogDetailView.as_view(),
        name="audit_log_detail",
    ),
]
