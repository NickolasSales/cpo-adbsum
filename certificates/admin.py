"""
Certificate no Django Admin: leitura, nunca escrita.

O Django Admin e ferramenta tecnica de emergencia, nao a interface do produto.
Um certificado criado ali nasceria sem passar por issue_certificate: sem
validar aprovacao, sem concluir a matricula, sem registro na auditoria. E um
status alterado a mao produziria o estado que a interface publica le como
verdade, sem que nada nem ninguem tenha decidido isso.

Por isso: sem adicionar, sem alterar, sem excluir. Todos os campos em somente
leitura. A interface oficial e /admin-panel/certificados/.
"""

from django.contrib import admin

from certificates.models import Certificate


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = (
        "verification_code",
        "student_name_snapshot",
        "module_name_snapshot",
        "status",
        "issued_at",
    )
    list_filter = ("status", "template_version")
    search_fields = ("verification_code", "student_name_snapshot")
    date_hierarchy = "issued_at"
    ordering = ("-issued_at",)

    def get_readonly_fields(self, request, obj=None):
        return [campo.name for campo in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
