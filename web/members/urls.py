from django.urls import path

from . import views

app_name = "members"
urlpatterns = [
    path("", views.member_list, name="list"),
    path("new/", views.onboard, name="onboard"),
    path("<str:member_no>/", views.member_detail, name="detail"),
    path("<str:member_no>/approve/", views.approve, name="approve"),
    path("<str:member_no>/review/", views.review, name="review"),
]
