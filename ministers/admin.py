"""
Ministros no Django Admin: somente leitura, para suporte tecnico.

A interface oficial e /admin-panel/ministros/. Aqui nada se cria, edita ou
apaga — criar por aqui pularia a normalizacao e a auditoria dos servicos, e o
hash da senha nao tem por que aparecer num formulario.
"""

from django.contrib import admin

from ministers.models import Minister, MinisterAccount, MinisterFeeBatch, MinisterMonthlyFee


class SomenteLeitura(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [campo.name for campo in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Minister)
class MinisterAdmin(SomenteLeitura):
    list_display = (
        "full_name",
        "conamad_registration",
        "field_registration",
        "ministerial_title",
        "congregation",
        "is_active",
        "source",
    )
    list_filter = ("is_active", "ministerial_title", "source")
    search_fields = ("full_name", "conamad_registration", "field_registration")


@admin.register(MinisterAccount)
class MinisterAccountAdmin(SomenteLeitura):
    list_display = ("login_identifier", "minister", "is_active", "must_change_password", "last_login")
    list_filter = ("is_active", "must_change_password")
    search_fields = ("login_identifier", "minister__full_name")
    # O hash nao aparece nem para leitura.
    exclude = ("password_hash",)

    def get_readonly_fields(self, request, obj=None):
        return [c for c in super().get_readonly_fields(request, obj) if c != "password_hash"]


@admin.register(MinisterMonthlyFee)
class MinisterMonthlyFeeAdmin(SomenteLeitura):
    list_display = ("minister", "reference_month", "due_date", "amount", "status", "paid_at")
    list_filter = ("status", "reference_month", "payment_method")
    search_fields = ("minister__full_name", "minister__conamad_registration")
    list_select_related = ("minister",)


@admin.register(MinisterFeeBatch)
class MinisterFeeBatchAdmin(SomenteLeitura):
    list_display = ("reference_month", "created_count", "existing_count", "inactive_count", "total_amount", "created_by", "created_at")
    list_filter = ("reference_month",)
    # O token e o identificador de idempotencia do lote; nao ha motivo para exibi-lo.
    exclude = ("token",)

    def get_readonly_fields(self, request, obj=None):
        return [c for c in super().get_readonly_fields(request, obj) if c != "token"]
