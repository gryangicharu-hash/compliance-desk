from django.urls import path

from . import staff_views, views

app_name = "accounts"
urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("two-factor/setup/", views.mfa_setup, name="mfa_setup"),
    path("two-factor/", views.mfa_verify, name="mfa_verify"),
    path("logout/", views.logout_view, name="logout"),
    path("password/", views.password_change, name="password"),
    path("staff/", staff_views.staff_list, name="staff"),
    path("staff/<int:pk>/<str:action>/", staff_views.staff_action, name="staff_action"),
]
