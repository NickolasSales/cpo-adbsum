from django.urls import path

from common import views

app_name = "common"

urlpatterns = [
    path("", views.root, name="root"),
    path("health/", views.health_check, name="health"),
]
