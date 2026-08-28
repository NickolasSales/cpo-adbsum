"""Rotas da area do aluno referentes a modulos (namespace student)."""

from django.urls import path

from courses import views

urlpatterns = [
    path(
        "modulos/<int:pk>/",
        views.StudentModuleDetailView.as_view(),
        name="module_detail",
    ),
]
