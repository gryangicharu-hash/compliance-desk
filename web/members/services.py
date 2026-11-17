"""Member business logic. Views stay thin; every change goes through here so it is
always risk-assessed and written to the audit trail."""
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from accounts.security import cipher, db_role
from audittrail.services import record
from compliance_core import models as core
from compliance_core.risk import RiskInputs, RiskSettings, assess

from accounts.models import Role

from .models import Member, MemberChange, MemberPerson, MemberType, Pep, RiskAssessment, Status, StatusRelease

MEMBER_NO_LOCK = 7340002


def to_core(m: Member) -> core.Member:
    persons = list(m.persons.all()) if m.pk else []
    as_person = lambda p: core.Person(p.name, p.role, None, p.date_of_birth, p.nationality or None, p.ownership_pct)
    return core.Member(
        member_no=m.member_no, member_type=core.MemberType(m.member_type), name=m.name, id_number="",
        nationality=m.nationality, residence=m.residence, date_of_birth=m.date_of_birth,
        occupation=m.occupation, employer=m.employer, checkoff=m.checkoff, channel=core.Channel(m.channel),
        products=frozenset(m.products), pep=core.PepStatus(m.pep), pep_approved=m.pep_approved_by_id is not None,
        declared_monthly_income=Decimal(m.declared_monthly_income), source_of_funds=m.source_of_funds,
        kyc_complete=m.kyc_complete, adverse_media=m.adverse_media, id_expiry=m.id_expiry, joined=m.joined_on,
        last_review=m.last_review_on, status=core.MemberStatus(m.status),
        officials=[as_person(p) for p in persons if p.kind == MemberPerson.Kind.OFFICIAL],
        beneficial_owners=[as_person(p) for p in persons if p.kind == MemberPerson.Kind.BENEFICIAL_OWNER])


def needs_approval(member: Member) -> bool:
    ra = member.latest_assessment
    return (member.pep != Pep.NONE and member.pep_approved_by_id is None) or (ra is not None and ra.rating in ("high", "prohibited"))


def _inputs(member: Member) -> RiskInputs:
    from cases.models import Case
    from screening.services import screening_status
    from corebanking.services import credits_last_30_days, flags_for
    with db_role("system"):          # the rating must reflect open cases whoever triggered it
        open_case = Case.objects.filter(member=member, status=Case.Status.OPEN).exists()
    return RiskInputs(screening=screening_status(member), open_case=open_case, expected_cash=member.expected_cash,
                      guarantor_flags=flags_for(member), credits_last_30_days=credits_last_30_days(member))


def reassess(member: Member, actor: str) -> RiskAssessment:
    result = assess(to_core(member), _inputs(member),
                    RiskSettings(version=settings.RISK_SETTINGS_VERSION), as_of=timezone.localdate())
    ra = RiskAssessment.objects.create(
        member=member, score=result.score, points=result.points, base_rating=result.base_rating.value,
        rating=result.rating.value, reasons=list(result.reasons), overrides=list(result.overrides),
        due_diligence=result.due_diligence.value, next_review_on=result.next_review,
        settings_version=result.settings_version, assessed_by=actor)
    record(actor, "risk.assessed", member.member_no,
           {"score": ra.score, "rating": ra.rating, "due_diligence": ra.due_diligence, "settings": ra.settings_version})
    return ra


@dataclass
class PersonInput:
    kind: str
    name: str
    role: str = ""
    ownership_pct: Decimal | None = None


def _next_member_no() -> str:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", [MEMBER_NO_LOCK])
    # Members imported from core banking keep their own numbers; only M###### are ours.
    last = (Member.objects.filter(member_no__regex=r"^M[0-9]{6}$").order_by("-member_no")
            .values_list("member_no", flat=True).first())
    n = int(last[1:]) + 1 if last else 1
    return f"M{n:06d}"


def find_by_id_number(id_number: str) -> Member | None:
    return Member.objects.filter(id_number_bidx=cipher().blind_index(id_number)).first()


@transaction.atomic
def onboard(*, data: dict, persons: list[PersonInput], user) -> Member:
    id_number = data.pop("id_number").strip()
    if find_by_id_number(id_number):
        raise ValidationError({"id_number": "A member with this ID or registration number already exists."})
    member = Member(**data)
    member.member_no = _next_member_no()
    member.id_number_enc = cipher().encrypt(id_number)
    member.id_number_bidx = cipher().blind_index(id_number)
    member.onboarded_by = user
    member.last_review_on = member.joined_on
    member.status = Status.PENDING_APPROVAL
    member.save()
    for p in persons:
        MemberPerson.objects.create(member=member, kind=p.kind, name=p.name, role=p.role, ownership_pct=p.ownership_pct)
    record(user.username, "member.onboarded", member.member_no,
           {"type": member.member_type, "channel": member.channel, "persons": len(persons)})
    from screening.services import screen
    hits = screen(member, user.username)
    ra = reassess(member, user.username)
    if any(h.kind == "sanctions" for h in hits):
        member.status = Status.SCREENING_HOLD
        member.save(update_fields=["status", "updated_at"])
        record(user.username, "member.screening_hold", member.member_no, {"hits": len(hits)})
    elif member.pep == Pep.NONE and ra.rating in ("low", "medium"):
        member.status = Status.ACTIVE
        member.save(update_fields=["status", "updated_at"])
        record(user.username, "member.activated", member.member_no, {"rating": ra.rating})
    else:
        record(user.username, "member.sent_for_approval", member.member_no,
               {"rating": ra.rating, "pep": member.pep})
    return member


@transaction.atomic
def approve(member: Member, user) -> Member:
    """Senior management approval for a PEP or high-risk relationship (reg. 22)."""
    member = Member.objects.select_for_update().get(pk=member.pk)
    if member.status != Status.PENDING_APPROVAL:
        raise ValidationError("Only members awaiting approval can be approved.")
    if member.pep != Pep.NONE and not member.source_of_wealth.strip():
        raise ValidationError("Record the PEP's source of wealth before approving.")
    if member.onboarded_by_id == user.pk:
        raise ValidationError("The person who onboarded a member cannot also approve them.")
    if member.hits.filter(status="to_review").exists():
        raise ValidationError("Resolve this member's screening matches before approving.")
    if member.pep != Pep.NONE:
        member.pep_approved_by, member.pep_approved_at = user, timezone.now()
    member.status = Status.ACTIVE
    member.save()
    record(user.username, "member.approved", member.member_no, {"pep": member.pep})
    return member


def parse_people(text: str, kind: str) -> list[PersonInput]:
    """One person per line: 'Name, role' for officials; 'Name, 40' for owners (percent)."""
    out = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        name, _, rest = line.partition(",")
        rest = rest.strip()
        if kind == MemberPerson.Kind.BENEFICIAL_OWNER:
            try:
                pct = Decimal(rest.rstrip("%")) if rest else None
            except ArithmeticError:
                raise ValidationError(f"Ownership for '{name.strip()}' must be a number, e.g. 40")
            if pct is not None and not 0 <= pct <= 100:
                raise ValidationError(f"Ownership for '{name.strip()}' must be between 0 and 100")
            out.append(PersonInput(kind, name.strip(), "Beneficial owner", pct))
        else:
            out.append(PersonInput(kind, name.strip(), rest or "Official"))
    return out


def needs_people(member_type: str) -> bool:
    return member_type != MemberType.INDIVIDUAL


@transaction.atomic
def record_review(member: Member, *, user, note: str) -> RiskAssessment:
    """A periodic KYC review (regs 29-30): the file was checked and brought up to date.
    Re-rates the member and schedules the next review from today."""
    member = Member.objects.select_for_update().get(pk=member.pk)
    if member.status == Status.EXITED:
        raise ValidationError("Exited members are not reviewed.")
    if not note.strip():
        raise ValidationError("Record what was checked in the review.")
    member.last_review_on = timezone.localdate()
    member.save(update_fields=["last_review_on", "updated_at"])
    record(user.username, "member.reviewed", member.member_no, {"note": note.strip()})
    return reassess(member, user.username)


# ---------------------------------------------------------------- editing a member's details

EDITABLE = ("member_type", "name", "id_expiry", "date_of_birth", "nationality", "residence", "occupation", "employer",
            "checkoff", "declared_monthly_income", "source_of_funds", "source_of_wealth", "channel", "products", "pep",
            "expected_cash", "kyc_complete")
SCREENED = {"name", "date_of_birth", "nationality", "member_type", "id_number", "persons"}


def _plain(v):
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (list, tuple, set)):
        return list(v)
    return str(v) if v is not None and not isinstance(v, (bool, int, str)) else v


def _persons_key(people) -> list:
    return sorted([p.kind, p.name.strip(), (p.role or "").strip(),
                   None if p.ownership_pct is None else str(Decimal(p.ownership_pct).quantize(Decimal("0.01")))]
                  for p in people)


@transaction.atomic
def update_member(member: Member, *, data: dict, persons: list[PersonInput], user, reason: str) -> list[str]:
    """Apply an edit. Returns the fields that changed (empty if nothing did)."""
    import json
    from screening.services import screen, settle_status
    member = Member.objects.select_for_update().get(pk=member.pk)
    if member.status == Status.EXITED:
        raise ValidationError("An exited member's file can't be edited.")
    if not reason.strip():
        raise ValidationError({"reason": "Say why the details are being changed (e.g. periodic review, member request)."})
    previous: dict = {}
    for f in EDITABLE:
        if f in data and getattr(member, f) != data[f]:
            previous[f] = _plain(getattr(member, f))
            setattr(member, f, data[f])
    new_id = (data.get("id_number") or "").strip()
    if new_id and cipher().blind_index(new_id) != member.id_number_bidx:
        other = find_by_id_number(new_id)
        if other and other.pk != member.pk:
            raise ValidationError({"id_number": "Another member already has this ID or registration number."})
        previous["id_number"] = cipher().decrypt(member.id_number_enc)
        member.id_number_enc, member.id_number_bidx = cipher().encrypt(new_id), cipher().blind_index(new_id)
    old_people = list(member.persons.all())
    if _persons_key(old_people) != _persons_key(persons):
        previous["persons"] = [[p.kind, p.name, p.role, _plain(p.ownership_pct)] for p in old_people]
        member.persons.all().delete()
        for p in persons:
            MemberPerson.objects.create(member=member, kind=p.kind, name=p.name, role=p.role, ownership_pct=p.ownership_pct)
    if not previous:
        return []
    if member.member_type != MemberType.INDIVIDUAL and not member.persons.filter(kind=MemberPerson.Kind.OFFICIAL).exists():
        raise ValidationError({"officials": "Groups and companies need at least one official or signatory."})
    became_pep = "pep" in previous and previous["pep"] == Pep.NONE and member.pep != Pep.NONE
    if became_pep or "pep" in previous:
        member.pep_approved_by, member.pep_approved_at = None, None
    if became_pep and member.status == Status.ACTIVE:
        member.status = Status.PENDING_APPROVAL          # continuing a PEP relationship needs approval (reg. 22)
    member.save()
    changed = sorted(previous)
    MemberChange.objects.create(member=member, changed_by=user, fields=changed, reason=reason.strip(),
                                previous_enc=cipher().encrypt(json.dumps(previous, default=str)))
    record(user.username, "member.updated", member.member_no, {"fields": changed, "reason": reason.strip()})
    if SCREENED & set(changed):
        screen(member, user.username)
    reassess(member, user.username)
    settle_status(member, user.username)
    return changed


def change_history(member: Member) -> list[dict]:
    import json
    out = []
    for c in member.changes.select_related("changed_by"):
        try:
            prev = json.loads(cipher().decrypt(c.previous_enc))
        except ValueError:
            prev = {}
        out.append({"c": c, "previous": prev})
    return out


# ---------------------------------------------------------------- exits

@transaction.atomic
def exit_member(member: Member, *, user, exited_on, reason: str) -> Member:
    from cases.models import Case
    member = Member.objects.select_for_update().get(pk=member.pk)
    if not reason.strip():
        raise ValidationError("Record why the member is leaving.")
    if member.status == Status.EXITED:
        raise ValidationError("This member has already exited.")
    if exited_on > timezone.localdate():
        raise ValidationError("The exit date can't be in the future.")
    refer = "This member can't be exited here. Refer the request to the compliance officer."
    if member.status in (Status.BLOCKED, Status.SCREENING_HOLD):
        # Frozen or held funds must stay where they are until compliance resolves it.
        raise ValidationError(refer if not user.has_role(Role.COMPLIANCE_OFFICER) else
                              "Members on hold or with frozen funds can't be exited: resolve the screening first.")
    with db_role("system"):
        open_case = Case.objects.filter(member=member, status=Case.Status.OPEN).exists()
    if open_case and not user.has_role(Role.COMPLIANCE_OFFICER):
        raise ValidationError(refer)      # same wording as above: never hint at a case (tipping-off)
    old = member.status
    member.status, member.exited_on = Status.EXITED, exited_on
    member.save()
    record(user.username, "member.exited", member.member_no,
           {"from": old, "on": exited_on, "reason": reason.strip(), "open_case": open_case})
    return member


# ---------------------------------------------------------------- lifting a sanctions freeze (two people)

@transaction.atomic
def request_release(member: Member, *, user, reason: str, evidence: str) -> "StatusRelease":
    if member.status != Status.BLOCKED:
        raise ValidationError("Only a blocked member's freeze can be lifted.")
    if not reason.strip() or not evidence.strip():
        raise ValidationError("Give the reason and the evidence (e.g. UN delisting notice, FRC instruction).")
    if member.releases.filter(state=StatusRelease.State.PENDING).exists():
        raise ValidationError("A request is already waiting for a decision.")
    r = StatusRelease.objects.create(member=member, reason=reason.strip(), evidence=evidence.strip(), requested_by=user)
    record(user.username, "member.release_requested", member.member_no, {"request": r.pk})
    return r


@transaction.atomic
def decide_release(release: "StatusRelease", *, user, approve: bool, note: str) -> "StatusRelease":
    from screening.models import Hit
    release = StatusRelease.objects.select_for_update().select_related("member").get(pk=release.pk)
    if release.state != StatusRelease.State.PENDING:
        raise ValidationError("This request has already been decided.")
    if release.requested_by_id == user.pk:
        raise ValidationError("A second person must decide: you made this request.")
    if not note.strip():
        raise ValidationError("Record the reason for the decision.")
    release.state = StatusRelease.State.APPROVED if approve else StatusRelease.State.REJECTED
    release.decided_by, release.decided_at, release.decision_note = user, timezone.now(), note.strip()
    release.save()
    member = release.member
    record(user.username, f"member.release_{release.state}", member.member_no, {"request": release.pk})
    if approve:
        lifted = Hit.objects.filter(member=member, kind="sanctions", status=Hit.Status.CONFIRMED)
        refs = list(lifted.values_list("entry_ref", flat=True))
        lifted.update(status=Hit.Status.LIFTED)
        ra = reassess(member, user.username)
        member.refresh_from_db()
        member.status = Status.PENDING_APPROVAL if needs_approval(member) or ra.rating == "high" else Status.ACTIVE
        member.save(update_fields=["status", "updated_at"])
        record(user.username, "member.freeze_lifted", member.member_no, {"entries": refs, "status": member.status})
    return release
