import secrets
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role, User
from accounts.security import role_required
from audittrail.services import record

from . import services
from .models import Engagement, Finding, ProgrammeStep, RiskAssessmentApproval, SEVERITIES, TrainingRecord

AUDITOR = (Role.AUDITOR,)
MANAGEMENT = (Role.COMPLIANCE_OFFICER, Role.SENIOR_MANAGEMENT)
CAN_VIEW = AUDITOR + MANAGEMENT
GOVERNANCE_EDIT = (Role.COMPLIANCE_OFFICER, Role.ADMINISTRATOR)
DATE = forms.DateInput(attrs={"type": "date"})


class EngagementForm(forms.Form):
    kind = forms.ChoiceField(choices=Engagement.Kind.choices, label="Type")
    title = forms.CharField(max_length=200, initial="AML/CFT compliance audit")
    period_start = forms.DateField(widget=DATE, label="Period from")
    period_end = forms.DateField(widget=DATE, label="Period to")
    scope = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)


class SampleForm(forms.Form):
    population = forms.ChoiceField(choices=[])
    size = forms.IntegerField(min_value=1, max_value=500, initial=25, label="How many")
    seed = forms.IntegerField(required=False, min_value=0, help_text="Leave empty for a new random seed; it is recorded either way.")
    risk_based = forms.BooleanField(required=False, label="Risk-based (all high-risk members plus a random spread of the rest)")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["population"].choices = [(k, label) for k, (label, _) in services.populations().items()]


class StepForm(forms.Form):
    result = forms.ChoiceField(choices=[("", "Not yet done")] + ProgrammeStep.Result.choices, required=False)
    workpaper_ref = forms.CharField(max_length=60, required=False, label="Working paper")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)


class FindingForm(forms.Form):
    title = forms.CharField(max_length=200)
    regulation = forms.CharField(max_length=80)
    severity = forms.ChoiceField(choices=SEVERITIES)
    condition = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="What was found")
    recommendation = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))


class StatusForm(forms.Form):
    status = forms.ChoiceField(choices=Finding.Status.choices)
    evidence = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Evidence of the fix (to close)")


class ResponseForm(forms.Form):
    response = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Management response")
    owner = forms.CharField(max_length=120, label="Responsible person")
    due_on = forms.DateField(widget=DATE, label="Agreed date")


class TrainingForm(forms.Form):
    user = forms.ModelChoiceField(queryset=User.objects.filter(is_active=True).order_by("username"), label="Staff member")
    course = forms.CharField(max_length=160, initial="AML/CFT annual refresher")
    completed_on = forms.DateField(widget=DATE)
    valid_until = forms.DateField(widget=DATE)

    def clean(self):
        d = super().clean()
        if d.get("completed_on") and d.get("valid_until") and d["valid_until"] < d["completed_on"]:
            self.add_error("valid_until", "Must be on or after the completion date.")
        return d


class ApprovalForm(forms.Form):
    approved_on = forms.DateField(widget=DATE, label="Date the board approved it")
    board_minute_ref = forms.CharField(max_length=80, label="Board minute reference")
    document_name = forms.CharField(max_length=200, initial="Institutional ML/TF risk assessment")


def _is_auditor(request) -> bool:
    return request.user.has_role(Role.AUDITOR)


# ---------------------------------------------------------------- engagements

@role_required(*CAN_VIEW)
def engagement_list(request):
    form = EngagementForm(request.POST or None)
    if request.method == "POST":
        if not _is_auditor(request):
            raise Http404
        if form.is_valid():
            try:
                e = services.open_engagement(user=request.user, **form.cleaned_data)
                messages.success(request, f"{e.ref} opened with the standard programme of {e.steps.count()} steps.")
                return redirect("assurance:engagement", ref=e.ref)
            except ValidationError as exc:
                form.add_error(None, exc.messages)
    return render(request, "assurance/list.html", {
        "engagements": Engagement.objects.select_related("lead_auditor"), "form": form, "is_auditor": _is_auditor(request)})


@role_required(*CAN_VIEW)
def engagement(request, ref):
    e = get_object_or_404(Engagement.objects.select_related("lead_auditor"), ref=ref)
    findings = list(e.findings.select_related("response", "response__responded_by"))
    for f in findings:      # one form per finding, with distinct element ids
        f.response_form = ResponseForm(auto_id=f"r{f.ref}_%s")
        f.status_form = StatusForm(auto_id=f"s{f.ref}_%s")
    ctx = {"e": e, "findings": sorted(findings, key=lambda f: (f.severity_rank, f.ref)),
           "is_auditor": _is_auditor(request), "is_management": request.user.has_role(*MANAGEMENT)}
    if ctx["is_auditor"]:
        run = e.runs.select_related("run_by").first()
        ctx.update({
            "run": run, "results": list(run.results.all()) if run else [], "runs": e.runs.select_related("run_by"),
            "samples": e.samples.select_related("created_by"), "steps": e.steps.all(),
            "sample_form": SampleForm(), "finding_form": FindingForm(), "follow_up": services.follow_up(e),
            "step_results": ProgrammeStep.Result.choices})
    return render(request, "assurance/engagement.html", ctx)


def _engagement_for_auditor(request, ref) -> Engagement:
    e = get_object_or_404(Engagement, ref=ref)
    if e.status == Engagement.Status.CLOSED:
        messages.error(request, "This engagement is closed.")
        raise _Closed(ref)
    return e


class _Closed(Exception):
    pass


def _auditor_action(fn):
    """POST-only, auditor-only actions on an open engagement; errors shown as messages."""
    @require_POST
    @role_required(*AUDITOR)
    def wrapped(request, ref, **kwargs):
        try:
            e = _engagement_for_auditor(request, ref)
            fn(request, e, **kwargs)
        except _Closed:
            pass
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("assurance:engagement", ref=ref)
    return wrapped


@_auditor_action
def run_tests(request, e):
    run = services.run_tests(e, user=request.user)
    failed = sum(not r.passed for r in run.results.all())
    messages.success(request, f"17 tests run over the current data: {failed} with exceptions. "
                              f"Data fingerprint {run.data_fingerprint[:16]}…")


@_auditor_action
def draw_sample(request, e):
    form = SampleForm(request.POST)
    if not form.is_valid():
        raise ValidationError("Choose a population and a sample size.")
    seed = form.cleaned_data["seed"]
    seed = secrets.randbelow(10**9) if seed is None else seed
    s = services.draw_sample(e, user=request.user, population=form.cleaned_data["population"],
                             size=form.cleaned_data["size"], seed=seed, risk_based=form.cleaned_data["risk_based"])
    messages.success(request, f"Sample drawn: {len(s.selected)} of {s.population_size} (seed {s.seed}).")


@_auditor_action
def update_step(request, e, step_no):
    step = get_object_or_404(ProgrammeStep, engagement=e, step_no=step_no)
    form = StepForm(request.POST)
    if not form.is_valid():
        raise ValidationError("Check the step's fields.")
    for k, v in form.cleaned_data.items():
        setattr(step, k, v)
    step.save()
    record(request.user.username, "audit.step_updated", e.ref, {"step": step_no, "result": step.result})
    messages.success(request, f"Step {step_no} saved.")


@_auditor_action
def draft_findings(request, e):
    run = e.runs.first()
    if run is None:
        raise ValidationError("Run the automated tests first.")
    made = services.draft_findings(e, run, user=request.user)
    messages.success(request, f"{len(made)} finding(s) drafted from failed tests. Edit the wording before reporting."
                     if made else "No new findings: every failed test already has one.")


@_auditor_action
def add_finding(request, e):
    form = FindingForm(request.POST)
    if not form.is_valid():
        raise ValidationError("Fill in every field of the finding.")
    f = services.add_finding(e, user=request.user, **form.cleaned_data)
    messages.success(request, f"Finding {f.ref} raised.")


@_auditor_action
def finding_status(request, e, finding_ref):
    f = get_object_or_404(Finding, engagement=e, ref=finding_ref)
    form = StatusForm(request.POST)
    if not form.is_valid():
        raise ValidationError("Choose a status.")
    services.set_finding_status(f, user=request.user, **form.cleaned_data)
    messages.success(request, f"{f.ref} is now {f.get_status_display().lower()}.")


@_auditor_action
def set_status(request, e):
    status = request.POST.get("status")
    if status not in (Engagement.Status.REPORTING, Engagement.Status.CLOSED):
        raise ValidationError("Unknown status.")
    e.status = status
    e.save(update_fields=["status"])
    record(request.user.username, "audit.engagement_status", e.ref, {"status": status})
    messages.success(request, f"{e.ref} moved to {e.get_status_display().lower()}.")


@require_POST
@role_required(*MANAGEMENT)
def respond(request, ref, finding_ref):
    f = get_object_or_404(Finding, engagement__ref=ref, ref=finding_ref)
    form = ResponseForm(request.POST)
    if form.is_valid():
        try:
            services.respond(f, user=request.user, **form.cleaned_data)
            messages.success(request, f"Response to {f.ref} recorded.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    else:
        messages.error(request, "Give the response, the responsible person and the agreed date.")
    return redirect("assurance:engagement", ref=ref)


@role_required(*CAN_VIEW)
def report(request, ref):
    e = get_object_or_404(Engagement, ref=ref)
    if not _is_auditor(request) and e.status not in (Engagement.Status.REPORTING, Engagement.Status.CLOSED):
        raise Http404          # management sees the report once the auditor issues it
    try:
        from accounts.security import db_role
        with db_role("auditor"):       # test runs are auditors' papers; management reads the issued report
            text = services.report_text(e)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("assurance:engagement", ref=ref)
    record(request.user.username, "audit.report_downloaded", e.ref, {})
    resp = HttpResponse(text, content_type="text/plain; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{e.ref}-AML-audit-report.txt"'
    return resp


# ---------------------------------------------------------------- governance records

@role_required(Role.COMPLIANCE_OFFICER, Role.SENIOR_MANAGEMENT, Role.AUDITOR, Role.ADMINISTRATOR)
def governance(request):
    can_edit = request.user.has_role(*GOVERNANCE_EDIT)
    tform, aform = TrainingForm(prefix="t"), ApprovalForm(prefix="a")
    if request.method == "POST":
        if not can_edit:
            raise Http404
        if "t-user" in request.POST:
            tform = TrainingForm(request.POST, prefix="t")
            if tform.is_valid():
                t = TrainingRecord.objects.create(recorded_by=request.user, **tform.cleaned_data)
                record(request.user.username, "governance.training_recorded", t.user.username,
                       {"course": t.course, "valid_until": t.valid_until})
                messages.success(request, f"Training recorded for {t.user}.")
                return redirect("assurance:governance")
        else:
            aform = ApprovalForm(request.POST, prefix="a")
            if aform.is_valid():
                from django.conf import settings
                a = RiskAssessmentApproval.objects.create(recorded_by=request.user,
                                                          risk_settings_version=settings.RISK_SETTINGS_VERSION,
                                                          **aform.cleaned_data)
                record(request.user.username, "governance.risk_assessment_approved", a.board_minute_ref,
                       {"approved_on": a.approved_on})
                messages.success(request, "Board approval recorded.")
                return redirect("assurance:governance")
    today = timezone.localdate()
    staff = list(User.objects.filter(is_active=True).order_by("username"))
    current = {t.user_id: t for t in TrainingRecord.objects.filter(valid_until__gte=today).order_by("valid_until")}
    return render(request, "assurance/governance.html", {
        "can_edit": can_edit, "tform": tform, "aform": aform, "approvals": RiskAssessmentApproval.objects.all()[:10],
        "staff": [(u, current.get(u.pk)) for u in staff], "records": TrainingRecord.objects.select_related("user")[:50],
        "approval_age": (today - RiskAssessmentApproval.objects.first().approved_on).days
                        if RiskAssessmentApproval.objects.exists() else None})


# ---------------------------------------------------------------- board report

def _last_quarter(today) -> str:
    q = (today.month - 1) // 3          # 0..3, the current quarter index
    return f"{today.year - 1}-Q4" if q == 0 else f"{today.year}-Q{q}"


@role_required(Role.COMPLIANCE_OFFICER, Role.SENIOR_MANAGEMENT, Role.AUDITOR)
def board(request):
    label = request.GET.get("q") or _last_quarter(timezone.localdate())
    try:
        start, end = services.quarter(label)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        label = _last_quarter(timezone.localdate())
        start, end = services.quarter(label)
    summary, open_findings, overdue_findings = services.board_report(start, end)
    if request.GET.get("format") == "txt":
        text = summary.to_text() + (f"\nOpen audit findings: " + (", ".join(f"{k} {v}" for k, v in open_findings.items()) or "none")
                                    + f"; past agreed date: {overdue_findings}\n")
        record(request.user.username, "board_report.downloaded", label, {})
        resp = HttpResponse(text, content_type="text/plain; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="AML-board-report-{label}.txt"'
        return resp
    today = timezone.localdate()
    quarters = []
    y, q = today.year, (today.month - 1) // 3 + 1
    for _ in range(8):
        quarters.append(f"{y}-Q{q}")
        y, q = (y - 1, 4) if q == 1 else (y, q - 1)
    pct = round(100 * summary.staff_trained / summary.staff_total) if summary.staff_total else 0
    return render(request, "assurance/board.html", {
        "s": summary, "label": label, "quarters": quarters, "open_findings": open_findings,
        "overdue_findings": overdue_findings, "trained_pct": pct, "period_end_passed": end < today})
