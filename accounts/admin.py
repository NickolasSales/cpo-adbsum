"""
Registro do usuario no Django Admin.

O Django Admin e ferramenta tecnica e emergencial. A interface oficial de
gestao de alunos sera construida em /admin-panel/ na Etapa 2.

Nenhum modelo critico de prova, tentativa ou certificado sera registrado aqui
nas etapas seguintes: uma edicao feita por esta tela passaria por cima das
regras de integridade implementadas na camada de servico.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm

from accounts.models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    change_password_form = AdminPasswordChangeForm

    ordering = ("full_name",)
    list_display = ("email", "full_name", "role", "is_active", "must_change_password")
    list_filter = ("role", "is_active", "is_staff", "must_change_password")
    search_fields = ("email", "full_name")
    readonly_fields = ("date_joined", "last_login")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Identificacao", {"fields": ("full_name", "role")}),
        (
            "Situacao",
            {"fields": ("is_active", "must_change_password")},
        ),
        (
            "Permissoes",
            {
                "classes": ("collapse",),
                "fields": ("is_staff", "is_superuser", "groups", "user_permissions"),
            },
        ),
        ("Datas", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "full_name",
                    "role",
                    "usable_password",
                    "password1",
                    "password2",
                ),
            },
        ),
    )
