from django.urls import include, path

from members import views as member_views

urlpatterns = [
    path("", member_views.home, name="home"),
    path("accounts/", include("accounts.urls")),
    path("members/", include("members.urls")),
    path("audit-trail/", include("audittrail.urls")),
]

handler403 = "accounts.views.forbidden"
