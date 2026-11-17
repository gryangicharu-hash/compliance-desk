from django.urls import path

from . import views

app_name = "corebanking"
urlpatterns = [
    path("imports/", views.imports, name="imports"),
    path("imports/profiles/new/", views.new_profile, name="new_profile"),
    path("imports/<int:pk>/", views.batch, name="batch"),
    path("guarantors/", views.guarantors, name="guarantors"),
    path("guarantors/recalculate/", views.recalculate, name="recalculate"),
]
