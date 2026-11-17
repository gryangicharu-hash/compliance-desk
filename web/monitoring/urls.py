from django.urls import path

from . import views

app_name = "monitoring"
urlpatterns = [
    path("", views.alerts, name="alerts"),
    path("alerts/<int:pk>/", views.alert_detail, name="alert"),
    path("alerts/<int:pk>/decide/", views.decide, name="decide"),
    path("cash-reports/", views.cash_reports, name="cash_reports"),
    path("cash-reports/<str:period_end>.csv", views.cash_report_csv, name="cash_report_csv"),
    path("cash-reports/<str:period_end>/submit/", views.submit, name="submit"),
]
