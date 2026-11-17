from django.urls import path

from . import views

app_name = "restrictions"
urlpatterns = [
    path("", views.overview, name="overview"),
    path("download.csv", views.download, name="download"),
]
