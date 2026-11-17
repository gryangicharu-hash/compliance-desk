from django.urls import path

from . import file_views, views

app_name = "members"
urlpatterns = [
    path("", views.member_list, name="list"),
    path("new/", views.onboard, name="onboard"),
    path("<str:member_no>/", views.member_detail, name="detail"),
    path("<str:member_no>/approve/", views.approve, name="approve"),
    path("<str:member_no>/review/", views.review, name="review"),
    path("<str:member_no>/edit/", file_views.edit, name="edit"),
    path("<str:member_no>/exit/", file_views.exit_member, name="exit"),
    path("<str:member_no>/documents/", file_views.upload_document, name="upload_document"),
    path("<str:member_no>/documents/<int:pk>/", file_views.view_document, name="document"),
    path("<str:member_no>/documents/<int:pk>/replace/", file_views.supersede_document, name="supersede_document"),
    path("<str:member_no>/release/", file_views.request_release, name="request_release"),
    path("<str:member_no>/release/<int:pk>/<str:decision>/", file_views.decide_release, name="decide_release"),
]
