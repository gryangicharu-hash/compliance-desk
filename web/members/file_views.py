"""Keeping a member's file up to date: edits, documents, exit, and lifting a freeze."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.security import role_required

from . import documents, services
from .forms import DecisionNoteForm, DocumentForm, EditForm, ExitForm, ReleaseRequestForm
from .models import Member, MemberDocument, MemberPerson, StatusRelease

CAN_EDIT = (Role.FRONT_OFFICE, Role.COMPLIANCE_OFFICER)
CAN_SEE_FILE = (Role.FRONT_OFFICE, Role.COMPLIANCE_OFFICER, Role.SENIOR_MANAGEMENT, Role.AUDITOR)
CAN_EXIT = (Role.FRONT_OFFICE, Role.COMPLIANCE_OFFICER)


def _initial(m: Member) -> dict:
    people = lambda kind, fmt: "\n".join(fmt(p) for p in m.persons.filter(kind=kind))
    return {**{f: getattr(m, f) for f in services.EDITABLE},
            "officials": people(MemberPerson.Kind.OFFICIAL, lambda p: f"{p.name}, {p.role}"),
            "beneficial_owners": people(MemberPerson.Kind.BENEFICIAL_OWNER,
                                        lambda p: f"{p.name}, {p.ownership_pct.normalize() if p.ownership_pct is not None else ''}".rstrip(", "))}


def _form_errors(form, exc):
    if hasattr(exc, "error_dict"):
        for field, errs in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errs)
    else:
        form.add_error(None, exc.messages)


@role_required(*CAN_EDIT)
def edit(request, member_no):
    m = get_object_or_404(Member, member_no=member_no)
    form = EditForm(request.POST or None, initial=_initial(m))
    if request.method == "POST" and form.is_valid():
        data = dict(form.cleaned_data)
        reason = data.pop("reason")
        officials, owners = data.pop("officials", ""), data.pop("beneficial_owners", "")
        try:
            people = (services.parse_people(officials, MemberPerson.Kind.OFFICIAL)
                      + services.parse_people(owners, MemberPerson.Kind.BENEFICIAL_OWNER))
            changed = services.update_member(m, data=data, persons=people, user=request.user, reason=reason)
        except ValidationError as exc:
            _form_errors(form, exc)
        else:
            m.refresh_from_db()
            if not changed:
                messages.info(request, "Nothing changed.")
            else:
                messages.success(request, f"Saved ({', '.join(c.replace('_', ' ') for c in changed)}). "
                                          f"Re-rated {m.latest_assessment.rating}; status {m.get_status_display().lower()}.")
            return redirect("members:detail", member_no=member_no)
    return render(request, "members/edit.html", {"form": form, "m": m})


@require_POST
@role_required(*CAN_EDIT)
def upload_document(request, member_no):
    m = get_object_or_404(Member, member_no=member_no)
    form = DocumentForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Choose the document type and a file.")
    else:
        f = form.cleaned_data["file"]
        try:
            documents.store(m, data=f.read(), filename=f.name, doc_type=form.cleaned_data["doc_type"], user=request.user,
                            description=form.cleaned_data["description"], expires_on=form.cleaned_data["expires_on"])
            messages.success(request, f"{f.name} stored (encrypted).")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    return redirect("members:detail", member_no=member_no)


@role_required(*CAN_SEE_FILE)
def view_document(request, member_no, pk):
    doc = get_object_or_404(MemberDocument.objects.select_related("member"), pk=pk, member__member_no=member_no)
    try:
        data = documents.read(doc, user=request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("members:detail", member_no=member_no)
    resp = HttpResponse(data, content_type=doc.content_type)
    resp["Content-Disposition"] = f'inline; filename="{doc.member.member_no}-{doc.doc_type}-{doc.pk}"'
    resp["Cache-Control"] = "no-store"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@require_POST
@role_required(*CAN_EDIT)
def supersede_document(request, member_no, pk):
    doc = get_object_or_404(MemberDocument, pk=pk, member__member_no=member_no)
    try:
        documents.supersede(doc, user=request.user, reason=request.POST.get("reason", ""))
        messages.success(request, "Marked as replaced. It stays on file for the retention period.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("members:detail", member_no=member_no)


@role_required(*CAN_EXIT)
def exit_member(request, member_no):
    m = get_object_or_404(Member, member_no=member_no)
    form = ExitForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.exit_member(m, user=request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc.messages)
        else:
            messages.success(request, f"{m.name} recorded as exited. Records are kept for 7 years from the exit date.")
            return redirect("members:detail", member_no=member_no)
    return render(request, "members/exit.html", {"form": form, "m": m})


@require_POST
@role_required(Role.COMPLIANCE_OFFICER)
def request_release(request, member_no):
    m = get_object_or_404(Member, member_no=member_no)
    form = ReleaseRequestForm(request.POST)
    try:
        if not form.is_valid():
            raise ValidationError("Give the reason and the evidence.")
        services.request_release(m, user=request.user, **form.cleaned_data)
        messages.success(request, "Request recorded. A second person (another compliance officer or senior management) must decide.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("members:detail", member_no=member_no)


@require_POST
@role_required(Role.COMPLIANCE_OFFICER, Role.SENIOR_MANAGEMENT)
def decide_release(request, member_no, pk, decision):
    r = get_object_or_404(StatusRelease, pk=pk, member__member_no=member_no)
    form = DecisionNoteForm(request.POST)
    try:
        if decision not in ("approve", "reject"):
            raise ValidationError("Unknown decision.")
        if not form.is_valid():
            raise ValidationError("Record the reason for the decision.")
        services.decide_release(r, user=request.user, approve=decision == "approve", note=form.cleaned_data["note"])
        messages.success(request, "Freeze lifted." if decision == "approve" else "Request rejected; the freeze stays.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("members:detail", member_no=member_no)
