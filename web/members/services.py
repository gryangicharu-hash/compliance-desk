"""Member business logic. Views stay thin; every change goes through here so it is
always risk-assessed and written to the audit trail."""
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from accounts.security import cipher
from audittrail.services import record
from compliance_core import models as core
from compliance_core.risk import RiskInputs, RiskSettings, assess

from .models import Member, MemberPerson, MemberType, Pep, RiskAssessment, Status

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


def reassess(member: Member, actor: str) -> RiskAssessment:
    result = assess(to_core(member), RiskInputs(expected_cash=member.expected_cash),
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
    last = Member.objects.order_by("-id").values_list("member_no", flat=True).first()
    n = int(last[1:]) + 1 if last and last[1:].isdigit() else 1
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
    ra = reassess(member, user.username)
    if member.pep == Pep.NONE and ra.rating in ("low", "medium"):
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
