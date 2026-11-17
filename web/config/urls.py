from django.urls import include, path

from django.db import connection
from django.http import HttpResponse

from members import views as member_views


def healthz(request):
    """For the load balancer or uptime monitor: the app is up and can reach the database."""
    with connection.cursor() as cur:
        cur.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("", member_views.home, name="home"),
    path("healthz", healthz),
    path("accounts/", include("accounts.urls")),
    path("members/", include("members.urls")),
    path("audit-trail/", include("audittrail.urls")),
    path("screening/", include("screening.urls")),
    path("cases/", include("cases.urls")),
    path("monitoring/", include("monitoring.urls")),
    path("data/", include("corebanking.urls")),
    path("jobs/", include("jobs.urls")),
    path("audit/", include("assurance.urls")),
]

handler403 = "accounts.views.forbidden"
