from django.urls import path

from . import views

app_name = "assurance"
urlpatterns = [
    path("", views.engagement_list, name="list"),
    path("governance/", views.governance, name="governance"),
    path("board-report/", views.board, name="board"),
    path("<str:ref>/", views.engagement, name="engagement"),
    path("<str:ref>/run-tests/", views.run_tests, name="run_tests"),
    path("<str:ref>/sample/", views.draw_sample, name="sample"),
    path("<str:ref>/steps/<int:step_no>/", views.update_step, name="step"),
    path("<str:ref>/findings/draft/", views.draft_findings, name="draft_findings"),
    path("<str:ref>/findings/new/", views.add_finding, name="add_finding"),
    path("<str:ref>/findings/<str:finding_ref>/status/", views.finding_status, name="finding_status"),
    path("<str:ref>/findings/<str:finding_ref>/respond/", views.respond, name="respond"),
    path("<str:ref>/status/", views.set_status, name="status"),
    path("<str:ref>/report.txt", views.report, name="report"),
]
