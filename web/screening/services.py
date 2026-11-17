"""Loading lists, screening members, and recording decisions."""
import hashlib
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audittrail.services import record
from compliance_core import lists as L
from compliance_core.models import ScreeningStatus
from compliance_core.screening import new_hits, screen_member
from members.models import Member, Pep, Status

from .models import Hit, ListEntry, ListKind, ListVersion

# Standard CSV layout for lists that are not in UN XML format (documented on the upload page).
CSV_MAPPING = {"ref": "ref", "name": "name", "type": "type", "aliases": "aliases", "dob": "dob", "nationality": "nationality"}


def _to_core(e: ListEntry, list_name: str) -> L.ListEntry:
    return L.ListEntry(list_name=list_name, ref=e.ref, entry_type=e.entry_type, primary_name=e.primary_name,
                       aliases=tuple(L.Alias(n, q) for n, q in e.aliases), dates_of_birth=tuple(e.dates_of_birth),
                       nationalities=tuple(e.nationalities), listed_on=e.listed_on or None)


def current_entries() -> dict[str, list[L.ListEntry]]:
    """Current entries grouped by list kind."""
    out: dict[str, list[L.ListEntry]] = {ListKind.SANCTIONS: [], ListKind.PEP: []}
    for v in ListVersion.objects.filter(status=ListVersion.Status.CURRENT).prefetch_related("entries"):
        out[v.kind].extend(_to_core(e, v.list_name) for e in v.entries.all())
    return out


def screening_status(member: Member) -> ScreeningStatus:
    statuses = set(member.hits.values_list("kind", "status"))
    if (ListKind.SANCTIONS, Hit.Status.CONFIRMED) in statuses:
        return ScreeningStatus.CONFIRMED_MATCH
    if any(s == Hit.Status.TO_REVIEW for _, s in statuses):
        return ScreeningStatus.POTENTIAL_MATCH
    return ScreeningStatus.CLEARED if statuses else ScreeningStatus.CLEAR


def settle_status(member: Member, actor: str) -> None:
    """Hold, block or release a member according to their screening results."""
    from members.services import needs_approval
    old = member.status
    open_sanctions = member.hits.filter(kind=ListKind.SANCTIONS, status=Hit.Status.TO_REVIEW).exists()
    confirmed = member.hits.filter(kind=ListKind.SANCTIONS, status=Hit.Status.CONFIRMED).exists()
    if confirmed:
        new = Status.BLOCKED
    elif open_sanctions and old in (Status.ACTIVE, Status.PENDING_APPROVAL):
        new = Status.SCREENING_HOLD
    elif old == Status.SCREENING_HOLD and not open_sanctions:
        new = Status.PENDING_APPROVAL if needs_approval(member) else Status.ACTIVE
    else:
        new = old
    if new != old and old != Status.EXITED:
        member.status = new
        member.save(update_fields=["status", "updated_at"])
        record(actor, "member.status_changed", member.member_no, {"from": old, "to": new, "reason": "screening"})


def screen(member: Member, actor: str, entries: dict | None = None) -> list[Hit]:
    entries = current_entries() if entries is None else entries
    from members.services import to_core
    core_member = to_core(member)
    decided = set(member.hits.values_list("member__member_no", "subject_name", "list_name", "entry_ref"))
    created = []
    for kind, es in entries.items():
        for h in new_hits(screen_member(core_member, es), decided):
            obj, made = Hit.objects.get_or_create(
                member=member, subject_name=h.subject.name, list_name=h.entry.list_name, entry_ref=h.entry.ref,
                defaults=dict(kind=kind, subject_role=h.subject.role, entry_name=h.entry.primary_name,
                              matched_name=h.matched_list_name, score=Decimal(str(h.score)), method=h.method,
                              dob_check=h.dob_check, nationality_check=h.nationality_check, confidence=h.confidence))
            if made:
                created.append(obj)
                record(actor, "screening.hit_raised", member.member_no,
                       {"list": h.entry.list_name, "ref": h.entry.ref, "score": h.score, "confidence": h.confidence})
    return created


def rescreen_all(actor: str) -> int:
    from members.services import reassess
    entries = current_entries()
    raised = 0
    for member in Member.objects.exclude(status=Status.EXITED).prefetch_related("persons"):
        new = screen(member, actor, entries)
        if new:
            raised += len(new)
            reassess(member, actor)
            settle_status(member, actor)
    record(actor, "screening.rescreened_all", "all members", {"new_hits": raised})
    return raised


def _parse(data: bytes, fmt: str, list_name: str) -> list[L.ListEntry]:
    if fmt == "un_xml":
        return [L.ListEntry(**{**e.__dict__, "list_name": list_name}) for e in L.parse_un_consolidated(data)]
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise L.ListFormatError("The CSV file must be saved as UTF-8") from exc
    mapping = {k: v for k, v in CSV_MAPPING.items()}
    header = text.splitlines()[0].split(",") if text.strip() else []
    mapping = {k: v for k, v in mapping.items() if k in ("ref", "name") or v in [h.strip() for h in header]}
    return L.parse_csv_list(text, list_name=list_name, mapping=mapping)


def load_list(*, data: bytes, filename: str, list_name: str, kind: str, fmt: str, user) -> ListVersion:
    """Parse first; a file that can't be read is recorded as rejected (outside any
    transaction, so the record survives) and the list in use stays unchanged."""
    sha = hashlib.sha256(data).hexdigest()
    try:
        parsed = _parse(data, fmt, list_name)
        if not parsed:
            raise L.ListFormatError("The file contains no entries")
    except L.ListFormatError as exc:
        ListVersion.objects.create(list_name=list_name, kind=kind, source=filename, sha256=sha,
                                   status=ListVersion.Status.REJECTED, error=str(exc), loaded_by=user)
        record(user.username, "screening.list_rejected", list_name, {"file": filename, "error": str(exc)})
        raise ValidationError(f"The list was not loaded: {exc}. The list already in use is unchanged.")
    return _install(parsed, data_sha=sha, filename=filename, list_name=list_name, kind=kind, user=user)


@transaction.atomic
def _install(parsed, *, data_sha, filename, list_name, kind, user) -> ListVersion:
    sha = data_sha
    previous = ListVersion.objects.select_for_update().filter(list_name=list_name, status=ListVersion.Status.CURRENT).first()
    old_entries = [_to_core(e, list_name) for e in previous.entries.all()] if previous else []
    if previous:
        previous.status = ListVersion.Status.SUPERSEDED
        previous.save(update_fields=["status"])
    version = ListVersion.objects.create(list_name=list_name, kind=kind, source=filename, sha256=sha,
                                         entry_count=len(parsed), status=ListVersion.Status.CURRENT, loaded_by=user)
    ListEntry.objects.bulk_create([ListEntry(
        version=version, ref=e.ref, entry_type=e.entry_type, primary_name=e.primary_name,
        aliases=[[a.name, a.quality] for a in e.aliases], dates_of_birth=list(e.dates_of_birth),
        nationalities=list(e.nationalities), listed_on=e.listed_on or "") for e in parsed])
    diff = L.diff_lists(old_entries, parsed)
    changed = [e.ref for e in diff.changed]
    if changed:   # changed entries get a fresh decision
        Hit.objects.filter(list_name=list_name, entry_ref__in=changed).update(
            status=Hit.Status.TO_REVIEW, decided_by=None, decided_at=None, decision_note="")
    record(user.username, "screening.list_loaded", list_name, {
        "file": filename, "sha256": sha, "entries": len(parsed), "added": len(diff.added),
        "removed": len(diff.removed), "changed": len(changed)})
    rescreen_all(user.username)
    return version


@transaction.atomic
def decide(hit: Hit, *, user, decision: str, note: str) -> Hit:
    from cases.services import open_case
    from members.services import reassess
    hit = Hit.objects.select_for_update().select_related("member").get(pk=hit.pk)
    member = hit.member
    if hit.status != Hit.Status.TO_REVIEW:
        raise ValidationError("This match has already been decided.")
    if not note.strip():
        raise ValidationError("Record the reason for the decision.")
    if member.onboarded_by_id == user.pk:
        raise ValidationError("The officer who onboarded this member cannot decide their screening matches.")
    if decision not in (Hit.Status.FALSE_POSITIVE, Hit.Status.CONFIRMED):
        raise ValidationError("Unknown decision.")
    hit.status, hit.decided_by, hit.decided_at, hit.decision_note = decision, user, timezone.now(), note.strip()
    hit.save()
    record(user.username, f"screening.hit_{decision}", member.member_no,
           {"list": hit.list_name, "ref": hit.entry_ref, "note": hit.decision_note})
    if decision == Hit.Status.CONFIRMED and hit.kind == ListKind.SANCTIONS:
        settle_status(member, user.username)
        open_case(member, user=user, case_type="sanctions_match",
                  reason=f"Confirmed match with {hit.entry_name} ({hit.list_name}, {hit.entry_ref}). "
                         "Funds frozen; report to the FRC without delay.")
    elif decision == Hit.Status.CONFIRMED and hit.kind == ListKind.PEP:
        if member.pep == Pep.NONE:
            member.pep = Pep.DOMESTIC
        member.pep_approved_by, member.pep_approved_at = None, None
        if member.status == Status.ACTIVE:
            member.status = Status.PENDING_APPROVAL
        member.save()
        record(user.username, "member.pep_confirmed", member.member_no, {"list_entry": hit.entry_ref})
        reassess(member, user.username)          # rating first: settle_status reads it
        settle_status(member, user.username)
    else:
        reassess(member, user.username)
        settle_status(member, user.username)
    return hit
