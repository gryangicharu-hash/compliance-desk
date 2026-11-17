from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.security import role_required

from .models import JobRun
from .services import JOBS, run_job

CAN_VIEW = (Role.COMPLIANCE_OFFICER, Role.SENIOR_MANAGEMENT, Role.AUDITOR, Role.ADMINISTRATOR)
RUN_NOW = ("fetch_un_list", "refresh_guarantors", "run_monitoring")
SCHEDULE = [
    ("fetch_un_list", "Every day, early morning", "Downloads the UN Security Council consolidated list; if it changed, loads it and re-screens every member."),
    ("import_inbox", "Every night", "Imports member, loan and transaction files the core banking system left in the import folder."),
    ("refresh_guarantors", "Every night", "Recalculates guarantor network flags and re-assesses members whose flags changed."),
    ("run_monitoring", "Every night", "Runs the monitoring rules over recent transactions and raises alerts."),
    ("send_reminders", "Every night", "Emails compliance officers a count of what is due (no names or case details)."),
]


@role_required(*CAN_VIEW)
def job_list(request):
    last = {j: JobRun.objects.filter(job=j).first() for j in JOBS}
    return render(request, "jobs/list.html", {
        "schedule": [(j, when, what, last[j]) for j, when, what in SCHEDULE],
        "runs": JobRun.objects.all()[:60], "run_now": RUN_NOW,
        "can_run": request.user.has_role(Role.COMPLIANCE_OFFICER, Role.ADMINISTRATOR)})


@require_POST
@role_required(Role.COMPLIANCE_OFFICER, Role.ADMINISTRATOR)
def run_now(request, job):
    if job not in RUN_NOW:
        raise Http404
    from audittrail.services import record
    record(request.user.username, "job.run_requested", job, {})
    run = run_job(job, JOBS[job])
    level = messages.error if run.status == JobRun.Status.FAILED else messages.success
    level(request, f"{job.replace('_', ' ').capitalize()}: {run.get_status_display().lower()}."
                   + (f" {run.error}" if run.error else ""))
    return redirect("jobs:list")
