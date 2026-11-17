from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.security import cipher, mask, role_required
from audittrail.models import AuditEvent

from . import services
from .forms import OnboardForm
from .models import Member, MemberPerson, RiskAssessment, Status

ALL_STAFF = (Role.FRONT_OFFICE, Role.COMPLIANCE_OFFICER, Role.SENIOR_MANAGEMENT, Role.AUDITOR, Role.ADMINISTRATOR)
CAN_ONBOARD = (Role.FRONT_OFFICE, Role.COMPLIANCE_OFFICER)
CASE_ROLES = (Role.COMPLIANCE_OFFICER, Role.SENIOR_MANAGEMENT, Role.AUDITOR)


def _with_rating(qs):
    latest = RiskAssessment.objects.filter(member=OuterRef("pk")).order_by("-assessed_at", "-id")
    return qs.annotate(rating=Subquery(latest.values("rating")[:1]), score=Subquery(latest.values("score")[:1]),
                       next_review=Subquery(latest.values("next_review_on")[:1]))


@role_required(*ALL_STAFF)
def home(request):
    members = _with_rating(Member.objects.exclude(status=Status.EXITED))
    by_rating = {r: 0 for r in ("low", "medium", "high", "prohibited")}
    for row in members.values("rating").annotate(n=Count("id")):
        if row["rating"]:
            by_rating[row["rating"]] = row["n"]
    pending = Member.objects.filter(status=Status.PENDING_APPROVAL).order_by("created_at")[:10]
    return render(request, "members/home.html", {
        "total": members.count(), "by_rating": by_rating, "pending": pending,
        "can_onboard": request.user.has_role(*CAN_ONBOARD)})


@role_required(*ALL_STAFF)
def member_list(request):
    qs = _with_rating(Member.objects.all())
    q = request.GET.get("q", "").strip()
    if q:
        by_id = services.find_by_id_number(q)
        qs = qs.filter(Q(name__icontains=q) | Q(member_no__iexact=q) | Q(pk=by_id.pk if by_id else None))
    rating, status = request.GET.get("rating", ""), request.GET.get("status", "")
    if rating:
        qs = qs.filter(rating=rating)
    if status:
        qs = qs.filter(status=status)
    page = Paginator(qs.order_by("member_no"), 50).get_page(request.GET.get("page"))
    return render(request, "members/list.html", {
        "page": page, "q": q, "rating": rating, "status": status, "statuses": Status.choices,
        "can_onboard": request.user.has_role(*CAN_ONBOARD)})


@role_required(*ALL_STAFF)
def member_detail(request, member_no):
    member = get_object_or_404(Member, member_no=member_no)
    ra = member.latest_assessment
    factors = []
    if ra:
        from compliance_core.risk import RiskSettings
        weights = RiskSettings(version="").weights
        factors = [(k.title(), weights.get(k, 0), v) for k, v in ra.points.items()]
    history = AuditEvent.objects.filter(subject=member.member_no).order_by("-seq")[:20]
    sees_cases = request.user.has_role(*CASE_ROLES)
    overrides = list(ra.overrides) if ra else []
    if not sees_cases:      # tipping-off: never reveal a case to front office, even indirectly
        overrides = [o if "case" not in o.lower() else "Additional compliance factors (restricted)" for o in overrides]
    from cases.models import Case
    from screening.models import Hit
    return render(request, "members/detail.html", {
        "overrides": overrides, "sees_cases": sees_cases,
        "hits": Hit.objects.filter(member=member).select_related("member", "decided_by") if sees_cases else [],
        "cases": Case.objects.filter(member=member) if sees_cases else [],
        "can_decide": request.user.has_role(Role.COMPLIANCE_OFFICER),
        "m": member, "ra": ra, "factors": factors, "id_masked": mask(cipher().decrypt(member.id_number_enc)),
        "officials": member.persons.filter(kind=MemberPerson.Kind.OFFICIAL),
        "owners": member.persons.filter(kind=MemberPerson.Kind.BENEFICIAL_OWNER),
        "history": history, "assessments": member.assessments.all()[:10],
        "can_approve": request.user.has_role(Role.SENIOR_MANAGEMENT) and member.status == Status.PENDING_APPROVAL})


@role_required(*CAN_ONBOARD)
def onboard(request):
    form = OnboardForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = dict(form.cleaned_data)
        officials, owners = data.pop("officials", ""), data.pop("beneficial_owners", "")
        try:
            people = (services.parse_people(officials, MemberPerson.Kind.OFFICIAL)
                      + services.parse_people(owners, MemberPerson.Kind.BENEFICIAL_OWNER))
            member = services.onboard(data=data, persons=people, user=request.user)
        except ValidationError as exc:
            if hasattr(exc, "error_dict"):
                for field, errs in exc.message_dict.items():
                    form.add_error(field if field in form.fields else None, errs)
            else:
                form.add_error(None, exc.messages)
        else:
            ra = member.latest_assessment
            if member.status == Status.ACTIVE:
                messages.success(request, f"{member.name} saved as {member.member_no}: {ra.rating} risk, account active.")
            else:
                messages.warning(request, f"{member.name} saved as {member.member_no}: {ra.rating} risk. "
                                          "Senior management must approve before the account is used.")
            return redirect("members:detail", member_no=member.member_no)
    return render(request, "members/onboard.html", {"form": form})


@require_POST
@role_required(Role.SENIOR_MANAGEMENT)
def approve(request, member_no):
    member = get_object_or_404(Member, member_no=member_no)
    try:
        services.approve(member, request.user)
        messages.success(request, f"{member.name} approved.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("members:detail", member_no=member_no)
