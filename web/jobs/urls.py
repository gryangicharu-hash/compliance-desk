from django.urls import path

from . import views

app_name = "jobs"
urlpatterns = [
    path("", views.job_list, name="list"),
    path("<str:job>/run/", views.run_now, name="run"),
]
