from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.security import role_required
from audittrail.services import record
from members.models import Member

from . import parsing, services
from .models import Guarantee, ImportBatch, ImportKind, ImportProfile, Loan, NetworkFlag

CAN_VIEW_IMPORTS = (Role.COMPLIANCE_OFFICER, Role.ADMINISTRATOR, Role.AUDITOR)
CAN_IMPORT = (Role.COMPLIANCE_OFFICER, Role.ADMINISTRATOR)
CASE_ROLES = (Role.COMPLIANCE_OFFICER, Role.SENIOR_MANAGEMENT, Role.AUDITOR)


class ImportForm(forms.Form):
    kind = forms.ChoiceField(choices=ImportKind.choices, label="File type")
    profile = forms.ModelChoiceField(queryset=ImportProfile.objects.all(), required=False,
                                     empty_label="Standard layout", label="Column names")
    file = forms.FileField(label="CSV file")

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > settings.IMPORT_MAX_BYTES:
            raise ValidationError("The file is larger than the size limit; split it by date range.")
        return f


class ProfileForm(forms.Form):
    name = forms.CharField(max_length=80, help_text="For example the name of your core banking system and file")
    kind = forms.ChoiceField(choices=ImportKind.choices, label="File type")
    mapping = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}), label="Column names",
                              help_text="One per line: Their column = our column, e.g. 'Member No = member_no'")

    def clean(self):
        data = super().clean()
        if "mapping" in data and "kind" in data:
            try:
                data["mapping"] = parsing.parse_mapping(data["mapping"], data["kind"])
            except ValueError as exc:
                self.add_error("mapping", str(exc))
        if ImportProfile.objects.filter(name=data.get("name", "")).exists():
            self.add_error("name", "A profile with this name already exists.")
        return data


@role_required(*CAN_VIEW_IMPORTS)
def imports(request):
    form = ImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        if not request.user.has_role(*CAN_IMPORT):
            messages.error(request, "Only the compliance officer or an administrator can import files.")
        elif form.is_valid():
            f = form.cleaned_data["file"]
            try:
                batch = services.import_file(data=f.read(), filename=f.name, kind=form.cleaned_data["kind"],
                                             user=request.user, profile=form.cleaned_data["profile"])
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            else:
                if batch.status == ImportBatch.Status.IMPORTED:
                    messages.success(request, f"{f.name} imported: {batch.created} new, {batch.updated} updated, "
                                              f"{batch.unchanged} unchanged.")
                else:
                    messages.error(request, f"{f.name} was not imported: {len(batch.errors)} problem(s) found. "
                                            "Nothing was changed. Fix the file and import it again.")
                return redirect("corebanking:batch", pk=batch.pk)
    layouts = [(ImportKind(k).label, [(c, req, d) for c, (req, d) in cols.items()]) for k, cols in parsing.LAYOUTS.items()]
    return render(request, "corebanking/imports.html", {
        "form": form, "profile_form": ProfileForm(), "batches": ImportBatch.objects.select_related("imported_by")[:50],
        "profiles": ImportProfile.objects.all(), "layouts": layouts, "can_import": request.user.has_role(*CAN_IMPORT)})


@require_POST
@role_required(*CAN_IMPORT)
def new_profile(request):
    form = ProfileForm(request.POST)
    if form.is_valid():
        p = ImportProfile.objects.create(name=form.cleaned_data["name"], kind=form.cleaned_data["kind"],
                                         mapping=form.cleaned_data["mapping"], created_by=request.user)
        record(request.user.username, "import.profile_saved", p.name, {"kind": p.kind, "mapping": p.mapping})
        messages.success(request, f"Profile '{p.name}' saved.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("corebanking:imports")


@role_required(*CAN_VIEW_IMPORTS)
def batch(request, pk):
    b = get_object_or_404(ImportBatch.objects.select_related("imported_by"), pk=pk)
    return render(request, "corebanking/batch.html", {"b": b})


@role_required(*CASE_ROLES)
def guarantors(request):
    flags = NetworkFlag.objects.select_related("member")
    by_member: dict[int, dict] = {}
    for f in flags:
        row = by_member.setdefault(f.member_id, {"m": f.member, "flags": [], "high": False})
        row["flags"].append(f)
        row["high"] |= f.severity == "high"
    rows = sorted(by_member.values(), key=lambda r: (not r["high"], r["m"].member_no))
    cycles = sorted({f.text for f in flags if f.code == "CIRCULAR"})
    top = (Member.objects.annotate(n=Count("guarantees_given", filter=Q(guarantees_given__loan__status="active")))
           .filter(n__gt=0).order_by("-n", "member_no")[:10])
    return render(request, "corebanking/guarantors.html", {
        "rows": rows, "cycles": cycles, "top": top, "computed_at": flags[0].computed_at if flags else None,
        "loans": Loan.objects.filter(status="active").count(),
        "guarantees": Guarantee.objects.filter(loan__status="active").count(),
        "can_recalculate": request.user.has_role(Role.COMPLIANCE_OFFICER)})


@require_POST
@role_required(Role.COMPLIANCE_OFFICER)
def recalculate(request):
    s = services.refresh_network(request.user.username)
    messages.success(request, f"Recalculated: {s['members_flagged']} member(s) flagged, "
                              f"{s['members_changed']} re-assessed.")
    return redirect("corebanking:guarantors")
