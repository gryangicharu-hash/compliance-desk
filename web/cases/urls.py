from django.urls import path

from . import views

app_name = "cases"
urlpatterns = [
    path("", views.case_list, name="list"),
    path("open/<str:member_no>/", views.open_case, name="open"),
    path("<str:ref>/", views.case_detail, name="detail"),
    path("<str:ref>/str-draft.xml", views.str_download, name="str_xml"),
    path("<str:ref>/<str:action>/", views.decide, name="decide"),
]
