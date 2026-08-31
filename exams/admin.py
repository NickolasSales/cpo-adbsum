"""
Provas no Django Admin: registradas, porem somente leitura.

Decisao e motivo
----------------
Exam, Question e QuestionOption tem regras que nao vivem no modelo. Publicar
exige validar a estrutura inteira e congelar o total de pontos; duplicar exige
calcular a proxima versao da linhagem sob trava; alterar uma questao exige que
a prova esteja em rascunho. Nada disso passa pelo Django Admin.

Se estas telas aceitassem escrita, o Django Admin viraria um caminho paralelo
capaz de publicar uma prova sem gabarito valido, alterar o gabarito de uma
prova ja aplicada ou criar duas versoes com o mesmo numero. A interface
oficial e /admin-panel/, e ela e a unica que grava.

Nao registrar tambem resolveria, mas registrar somente leitura entrega algo
util: uma forma rapida de inspecionar e pesquisar dados em producao, com
filtros prontos, sem abrir o psql. O ganho e real e o risco e zero enquanto
add, change e delete estiverem desligados — que e o que as tres classes
abaixo fazem.

Uma excecao explicita: access_password_hash nao aparece em nenhuma tela.
Nao e senha e nao serve para nada aqui, e exibi-lo so ofereceria material
para quebra offline.

Tentativas (Etapa 4)
--------------------
Valem as mesmas tres proibicoes, com um motivo ainda mais direto: a resposta
de um aluno e o registro do que ele fez numa prova. Poder edita-la pelo
Django Admin seria uma porta lateral para alterar prova alheia sem passar por
nenhum servico, sem trava de concorrencia e sem entrar na trilha de
auditoria. As telas ficam registradas para inspecao tecnica — descobrir por
que uma tentativa expirou, conferir se o autosave gravou —, e nada mais.

O gabarito continua fora: AttemptOption mostra o texto da alternativa e a
posicao em que ela apareceu, e quem quiser ver is_correct usa a tela de
QuestionOption, que e administrativa e sempre foi.
"""

from django.contrib import admin

from exams.models import (
    Answer,
    AnswerOption,
    AttemptOption,
    AttemptQuestion,
    Exam,
    ExamAttempt,
    Question,
    QuestionOption,
)


class SomenteLeituraAdmin(admin.ModelAdmin):
    """Base que desliga toda escrita."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Exam)
class ExamAdmin(SomenteLeituraAdmin):
    list_display = (
        "title",
        "module",
        "version",
        "status",
        "total_points",
        "passing_score",
        "published_at",
    )
    list_filter = ("status", "module")
    search_fields = ("title",)
    ordering = ("module__order", "title", "-version")
    exclude = ("access_password_hash",)

    def get_readonly_fields(self, request, obj=None):
        return [
            campo.name
            for campo in self.model._meta.fields
            if campo.name != "access_password_hash"
        ]


@admin.register(Question)
class QuestionAdmin(SomenteLeituraAdmin):
    list_display = ("exam", "order", "type", "points", "required", "active")
    list_filter = ("type", "active", "exam__status")
    search_fields = ("text",)
    ordering = ("exam", "order")

    def get_readonly_fields(self, request, obj=None):
        return [campo.name for campo in self.model._meta.fields]


@admin.register(QuestionOption)
class QuestionOptionAdmin(SomenteLeituraAdmin):
    list_display = ("question", "order", "text", "is_correct")
    list_filter = ("is_correct",)
    search_fields = ("text",)
    ordering = ("question", "order")

    def get_readonly_fields(self, request, obj=None):
        return [campo.name for campo in self.model._meta.fields]


# ---------------------------------------------------------------------------
# Tentativas (Etapa 4)
# ---------------------------------------------------------------------------


@admin.register(ExamAttempt)
class ExamAttemptAdmin(SomenteLeituraAdmin):
    list_display = (
        "public_id",
        "student",
        "exam",
        "attempt_number",
        "status",
        "started_at",
        "expires_at",
        "submitted_at",
        "expired_at",
    )
    list_filter = ("status", "exam")
    search_fields = ("public_id", "student__full_name", "student__email")
    ordering = ("-started_at",)
    date_hierarchy = "started_at"

    def get_readonly_fields(self, request, obj=None):
        return [campo.name for campo in self.model._meta.fields]


@admin.register(AttemptQuestion)
class AttemptQuestionAdmin(SomenteLeituraAdmin):
    list_display = ("attempt", "display_order", "question", "public_token")
    list_filter = ("question__type",)
    ordering = ("attempt", "display_order")

    def get_readonly_fields(self, request, obj=None):
        return [campo.name for campo in self.model._meta.fields]


@admin.register(AttemptOption)
class AttemptOptionAdmin(SomenteLeituraAdmin):
    list_display = ("attempt_question", "display_order", "option", "public_token")
    ordering = ("attempt_question", "display_order")

    def get_readonly_fields(self, request, obj=None):
        return [campo.name for campo in self.model._meta.fields]


@admin.register(Answer)
class AnswerAdmin(SomenteLeituraAdmin):
    list_display = ("attempt_question", "saved_at", "updated_at")
    ordering = ("-saved_at",)

    def get_readonly_fields(self, request, obj=None):
        return [campo.name for campo in self.model._meta.fields]


@admin.register(AnswerOption)
class AnswerOptionAdmin(SomenteLeituraAdmin):
    list_display = ("answer", "attempt_option", "created_at")
    ordering = ("-created_at",)

    def get_readonly_fields(self, request, obj=None):
        return [campo.name for campo in self.model._meta.fields]
