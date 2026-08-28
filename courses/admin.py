"""
Modulos e matriculas no Django Admin.

Module e administravel, porque criar um modulo nao envolve regra de dominio
alem da normalizacao do codigo, que o proprio save() aplica. Ainda assim a
tela nao substitui /admin-panel/modulos/: uma alteracao feita aqui nao gera
registro de auditoria, e por isso a interface oficial continua sendo a do
painel.

Enrollment e somente leitura. Criar matricula envolve validar o papel do
usuario, o estado do modulo e a inexistencia de matricula anterior — regras
que vivem em courses.services. Uma linha inserida por esta tela passaria por
cima de todas elas e poderia, por exemplo, matricular um administrador.
"""

from django.contrib import admin

from courses.models import Enrollment, Module


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "order", "created_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name")
    ordering = ("order", "name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "module", "status", "access_enabled", "enrolled_at")
    list_filter = ("status", "access_enabled", "module")
    search_fields = ("student__email", "student__full_name", "module__code")
    ordering = ("module__order", "student__full_name")

    def get_readonly_fields(self, request, obj=None):
        return [campo.name for campo in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
