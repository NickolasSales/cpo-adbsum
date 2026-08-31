"""
Rotas da area do aluno referentes a provas (namespace student).

Duas familias de URL, com identificadores de natureza diferente:

    /aluno/provas/<exam_id>/          a prova, pelo id publico dela
    /aluno/tentativas/<public_id>/    a tentativa, pelo UUID

O id da prova pode aparecer na URL: uma prova e a mesma para a turma inteira,
e saber que ela existe nao da acesso a nada — o portao e a matricula. Ja a
tentativa e de uma pessoa so, entao a URL usa o UUID e nunca a PK. Com PK
sequencial, trocar o numero da URL seria o primeiro teste de qualquer aluno
curioso, e a listagem de tentativas alheias sairia de graca.

Iniciar, salvar e finalizar sao POST. Nenhum GET aqui altera estado.
"""

from django.urls import path

from exams import views_student as views

urlpatterns = [
    path(
        "provas/<int:exam_id>/",
        views.ExamInstructionsView.as_view(),
        name="exam_instructions",
    ),
    path(
        "provas/<int:exam_id>/iniciar/",
        views.attempt_start,
        name="attempt_start",
    ),
    path(
        "tentativas/<uuid:public_id>/",
        views.AttemptView.as_view(),
        name="attempt",
    ),
    path(
        "tentativas/<uuid:public_id>/autosave/",
        views.attempt_autosave,
        name="attempt_autosave",
    ),
    path(
        "tentativas/<uuid:public_id>/finalizar/",
        views.attempt_submit,
        name="attempt_submit",
    ),
]

# Resultado da tentativa (Etapa 5). Mesmo identificador publico da prova: e a
# mesma tentativa vista de outro angulo, e dois identificadores para o mesmo
# objeto so criariam confusao.
urlpatterns += [
    path(
        "resultados/<uuid:public_id>/",
        views.AttemptResultView.as_view(),
        name="attempt_result",
    ),
]
