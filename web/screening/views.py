from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.security import role_required

from . import services
from .models import Hit, ListKind, ListVersion

CAN_VIEW = (Role.COMPLIANCE_OFFICER, Role.SENIOR_MANAGEMENT, Role.AUDITOR)


class UploadForm(forms.Form):
    list_name = forms.CharField(max_length=120, initial="UN Security Council consolidated list",
                                help_text="Use the same name every time you load a new version of this list.")
    kind = forms.ChoiceField(choices=ListKind.choices, label="List type")
    fmt = forms.ChoiceField(choices=[("un_xml", "UN consolidated list (XML)"), ("csv", "CSV in the standard layout")],
                            label="File format")
    file = forms.FileField()

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > settings.LIST_UPLOAD_MAX_BYTES:
            raise ValidationError("The file is larger than the 50 MB limit.")
        return f


class DecisionForm(forms.Form):
    decision = forms.ChoiceField(choices=[(Hit.Status.FALSE_POSITIVE, "False positive"), (Hit.Status.CONFIRMED, "Confirmed match")])
    note = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), label="Reason")


@role_required(*CAN_VIEW)
def queue(request):
    hits = Hit.objects.filter(status=Hit.Status.TO_REVIEW).select_related("member").order_by("raised_at")
    decided = Hit.objects.exclude(status=Hit.Status.TO_REVIEW).select_related("member", "decided_by")[:50]
    return render(request, "screening/queue.html", {
        "hits": hits, "decided": decided, "form": DecisionForm(),
        "can_decide": request.user.has_role(Role.COMPLIANCE_OFFICER)})


def _back(request) -> str:
    back = request.POST.get("back", "")
    return back if back.startswith("/") and not back.startswith("//") else "screening:queue"


@require_POST
@role_required(Role.COMPLIANCE_OFFICER)
def decide(request, pk):
    hit = get_object_or_404(Hit, pk=pk)
    form = DecisionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a decision and record the reason.")
        return redirect(_back(request))
    try:
        services.decide(hit, user=request.user, **form.cleaned_data)
        messages.success(request, f"Decision recorded for {hit.member.name}.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect(_back(request))


@role_required(*CAN_VIEW)
def lists(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        if not request.user.has_role(Role.COMPLIANCE_OFFICER):
            messages.error(request, "Only the compliance officer can load lists.")
        elif form.is_valid():
            f = form.cleaned_data["file"]
            try:
                v = services.load_list(data=f.read(), filename=f.name, list_name=form.cleaned_data["list_name"],
                                       kind=form.cleaned_data["kind"], fmt=form.cleaned_data["fmt"], user=request.user)
                n = Hit.objects.filter(status=Hit.Status.TO_REVIEW).count()
                messages.success(request, f"{v.list_name} loaded: {v.entry_count} entries. All members re-screened; "
                                          f"{n} match(es) waiting for a decision.")
                return redirect("screening:lists")
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
    versions = ListVersion.objects.select_related("loaded_by")[:50]
    return render(request, "screening/lists.html", {
        "form": form, "versions": versions, "can_load": request.user.has_role(Role.COMPLIANCE_OFFICER),
        "csv_columns": ", ".join(services.CSV_MAPPING.values())})


@require_POST
@role_required(Role.COMPLIANCE_OFFICER)
def rescreen_member(request, member_no):
    from members.models import Member
    from members.services import reassess
    member = get_object_or_404(Member, member_no=member_no)
    new = services.screen(member, request.user.username)
    services.settle_status(member, request.user.username)
    reassess(member, request.user.username)
    messages.info(request, f"Re-screened: {len(new)} new match(es).")
    return redirect("members:detail", member_no=member_no)
