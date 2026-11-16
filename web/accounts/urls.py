from django.urls import path

from . import views

app_name = "accounts"
urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("two-factor/setup/", views.mfa_setup, name="mfa_setup"),
    path("two-factor/", views.mfa_verify, name="mfa_verify"),
    path("logout/", views.logout_view, name="logout"),
]
