import hashlib
from datetime import date

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.security import role_required
from audittrail.services import record
from corebanking.models import Transaction

from . import services
from .models import Alert, CashReport

CAN_VIEW = (Role.COMPLIANCE_OFFICER, Role.SENIOR_MANAGEMENT, Role.AUDITOR)


class DecideForm(forms.Form):
    action = forms.ChoiceField(choices=[("no_action", "No further action"), ("open_case", "Open a suspicious activity case")])
    note = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="What you checked and why")


class SubmitForm(forms.Form):
    frc_reference = forms.CharField(max_length=80, label="Submission reference from the FRC")


@role_required(*CAN_VIEW)
def alerts(request):
    return render(request, "monitoring/alerts.html", {
        "open": Alert.objects.filter(status=Alert.Status.TO_REVIEW).select_related("member"),
        "decided": Alert.objects.filter(status__in=[Alert.Status.NO_ACTION, Alert.Status.CASE_OPENED])
                   .select_related("member", "decided_by", "case").order_by("-decided_at")[:50],
        "settings": services.monitoring_settings()})


@role_required(*CAN_VIEW)
def alert_detail(request, pk):
    a = get_object_or_404(Alert.objects.select_related("member", "decided_by", "case"), pk=pk)
    return render(request, "monitoring/alert.html", {
        "a": a, "txns": Transaction.objects.filter(txn_id__in=a.txn_ids).order_by("at"),
        "form": DecideForm(), "can_decide": request.user.has_role(Role.COMPLIANCE_OFFICER) and a.status == "to_review"})


@require_POST
@role_required(Role.COMPLIANCE_OFFICER)
def decide(request, pk):
    a = get_object_or_404(Alert, pk=pk)
    form = DecideForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a decision and record what you checked.")
        return redirect("monitoring:alert", pk=pk)
    try:
        a = services.decide(a, user=request.user, **form.cleaned_data)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("monitoring:alert", pk=pk)
    if a.case:
        messages.warning(request, f"{a.case.ref} opened. The 2-day STR clock has started.")
        return redirect("cases:detail", ref=a.case.ref)
    messages.success(request, "Alert closed: no further action.")
    return redirect("monitoring:alerts")


@role_required(*CAN_VIEW)
def cash_reports(request):
    return render(request, "monitoring/cash_reports.html", {
        "weeks": services.pending_weeks(), "submitted": CashReport.objects.select_related("submitted_by")[:30],
        "form": SubmitForm(), "can_submit": request.user.has_role(Role.COMPLIANCE_OFFICER),
        "threshold": services.monitoring_settings().large_cash_kes, "settings": services.monitoring_settings()})


def _period(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Http404 from None


@role_required(Role.COMPLIANCE_OFFICER)
def cash_report_csv(request, period_end):
    end = _period(period_end)
    text, alerts = services.cash_report(end)
    if not alerts:
        raise Http404
    record(request.user.username, "cash_report.downloaded", f"week ending {end:%Y-%m-%d}",
           {"rows": len(alerts), "sha256": hashlib.sha256(text.encode()).hexdigest()})
    resp = HttpResponse(text, content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="large-cash-week-ending-{end:%Y-%m-%d}.csv"'
    return resp


@require_POST
@role_required(Role.COMPLIANCE_OFFICER)
def submit(request, period_end):
    end = _period(period_end)
    form = SubmitForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter the FRC's reference for the submission.")
        return redirect("monitoring:cash_reports")
    try:
        r = services.submit_cash_report(end, user=request.user, **form.cleaned_data)
        messages.success(request, f"Week ending {end:%d %b %Y} recorded as reported ({r.rows} transaction(s)).")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("monitoring:cash_reports")
