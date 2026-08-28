"""
Registro do AuditLog no Django Admin, exclusivamente para leitura.

A trilha de auditoria nao pode ser editada nem apagada pela interface. O
registro existe apenas para consulta tecnica emergencial; a tela propria de
logos do painel administrativo vem na Etapa 8.
"""

from django.contrib import admin

from audit.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "event", "actor", "student", "ip_address")
    list_filter = ("event", "timestamp")
    search_fields = ("actor__email", "student__email", "entity_type", "entity_id")
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)

    def get_readonly_fields(self, request, obj=None):
        return [campo.name for campo in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
