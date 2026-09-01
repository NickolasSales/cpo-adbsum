"""
Rotas de certificado na area do aluno (namespace student).

Dois identificadores, cada um no seu lugar:

    emitir  usa o public_id da TENTATIVA — o certificado ainda nao existe
    baixar  usa o verification_code do CERTIFICADO — ele ja e o identificador
            publico do documento, e criar um segundo so para a area do aluno
            duplicaria vocabulario sem ganho

Emitir e POST. Baixar e GET, e nao altera nada: o PDF e gerado na hora a
partir de campos que ja estao gravados.
"""

from django.urls import path

from certificates import views_student as views

urlpatterns = [
    path(
        "certificados/",
        views.StudentCertificateListView.as_view(),
        name="certificate_list",
    ),
    path(
        "certificados/emitir/<uuid:public_id>/",
        views.certificate_issue,
        name="certificate_issue",
    ),
    path(
        "certificados/<uuid:verification_code>/baixar/",
        views.certificate_download,
        name="certificate_download",
    ),
]
