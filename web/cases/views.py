from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.security import role_required
from audittrail.models import AuditEvent
from audittrail.services import record
from members.models import Member

from . import services
from .models import Case

CAN_VIEW = (Role.COMPLIANCE_OFFICER, Role.SENIOR_MANAGEMENT, Role.AUDITOR)
CAN_DECIDE = (Role.COMPLIANCE_OFFICER,)


class OpenCaseForm(forms.Form):
    case_type = forms.ChoiceField(choices=[c for c in Case.Type.choices if c[0] != Case.Type.SANCTIONS_MATCH])
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), label="Why is this suspicious?")


class FileForm(forms.Form):
    frc_reference = forms.CharField(max_length=80, label="goAML reference from the FRC")
    note = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Summary of what was reported")


class CloseForm(forms.Form):
    note = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Why it is not suspicious")


@role_required(*CAN_VIEW)
def case_list(request):
    cases = list(Case.objects.select_related("member"))
    rows = [(c, services.clock(c) if c.status == Case.Status.OPEN else None) for c in cases]
    return render(request, "cases/list.html", {"rows": rows, "can_decide": request.user.has_role(*CAN_DECIDE)})


@role_required(*CAN_VIEW)
def case_detail(request, ref):
    case = get_object_or_404(Case.objects.select_related("member", "opened_by", "decided_by"), ref=ref)
    return render(request, "cases/detail.html", {
        "c": case, "clock": services.clock(case) if case.status == Case.Status.OPEN else None,
        "can_decide": request.user.has_role(*CAN_DECIDE) and case.status == Case.Status.OPEN,
        "file_form": FileForm(), "close_form": CloseForm(),
        "history": AuditEvent.objects.filter(subject=case.ref).order_by("-seq")})


@role_required(*CAN_DECIDE)
def open_case(request, member_no):
    member = get_object_or_404(Member, member_no=member_no)
    form = OpenCaseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        case = services.open_case(member, user=request.user, case_type=form.cleaned_data["case_type"],
                                  reason=form.cleaned_data["reason"])
        messages.warning(request, f"{case.ref} opened. Decide whether to file an STR by "
                                  f"{case.str_due_at.astimezone():%d %b %Y %H:%M}.")
        return redirect("cases:detail", ref=case.ref)
    return render(request, "cases/open.html", {"form": form, "m": member})


@require_POST
@role_required(*CAN_DECIDE)
def decide(request, ref, action):
    if action not in ("file", "close"):
        raise Http404
    case = get_object_or_404(Case, ref=ref)
    form = (FileForm if action == "file" else CloseForm)(request.POST)
    if not form.is_valid():
        messages.error(request, "Fill in every field to record the decision.")
        return redirect("cases:detail", ref=ref)
    try:
        if action == "file":
            services.file_str(case, user=request.user, **form.cleaned_data)
            messages.success(request, f"{ref}: STR recorded as filed.")
        else:
            services.close_not_suspicious(case, user=request.user, **form.cleaned_data)
            messages.success(request, f"{ref} closed as not suspicious.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("cases:detail", ref=ref)


@role_required(*CAN_DECIDE)
def str_download(request, ref):
    case = get_object_or_404(Case, ref=ref)
    xml = services.str_xml(case, reporting_entity_id=settings.INSTITUTION_NAME)
    record(request.user.username, "case.str_draft_downloaded", case.ref, {})
    resp = HttpResponse(xml, content_type="application/xml; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{case.ref}-STR-draft.xml"'
    return resp
