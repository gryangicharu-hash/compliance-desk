from django.urls import path

from . import views

app_name = "screening"
urlpatterns = [
    path("", views.queue, name="queue"),
    path("lists/", views.lists, name="lists"),
    path("hits/<int:pk>/decide/", views.decide, name="decide"),
    path("members/<str:member_no>/rescreen/", views.rescreen_member, name="rescreen"),
]
