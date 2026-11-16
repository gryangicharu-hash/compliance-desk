from django.urls import path

from . import views

app_name = "audittrail"
urlpatterns = [path("", views.event_list, name="list")]
