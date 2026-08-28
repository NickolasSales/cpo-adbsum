"""
StudentProfile no Django Admin, somente leitura.

Estrategia geral do projeto: o Django Admin e ferramenta tecnica de
emergencia, nunca um atalho para contornar a camada de servico. Um perfil
criado por ali nasceria sem o User correspondente configurado como aluno, sem
senha inicial e sem auditoria. Por isso a tela e apenas de consulta, e a
criacao continua exclusivamente em /admin-panel/alunos/novo/.
"""

from django.contrib import admin

from students.models import StudentProfile


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "source", "created_at")
    list_filter = ("source",)
    search_fields = ("user__email", "user__full_name")
    ordering = ("user__full_name",)

    def get_readonly_fields(self, request, obj=None):
        return [campo.name for campo in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
