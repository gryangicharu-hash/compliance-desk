from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from audittrail.services import record
from compliance_core.deadlines import str_clock, str_due
from compliance_core.reports import str_report_xml

from .models import Case

CASE_REF_LOCK = 7340003


def _next_ref() -> str:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", [CASE_REF_LOCK])
    last = Case.objects.order_by("-id").values_list("ref", flat=True).first()
    n = int(last.split("-")[1]) + 1 if last else 1
    return f"C-{n:06d}"


@transaction.atomic
def open_case(member, *, user, case_type: str, reason: str, suspicion_formed_at=None) -> Case:
    from members.services import reassess
    if not reason.strip():
        raise ValidationError("Describe why the activity is suspicious.")
    formed = suspicion_formed_at or timezone.now()
    case = Case.objects.create(ref=_next_ref(), member=member, case_type=case_type, reason=reason.strip(),
                               suspicion_formed_at=formed, str_due_at=str_due(formed), opened_by=user)
    record(user.username, "case.opened", case.ref,
           {"member": member.member_no, "type": case_type, "str_due": case.str_due_at.isoformat()})
    reassess(member, user.username)
    return case


def clock(case: Case):
    return str_clock(case.suspicion_formed_at, timezone.now())


@transaction.atomic
def file_str(case: Case, *, user, frc_reference: str, note: str) -> Case:
    from members.services import reassess
    case = Case.objects.select_for_update().get(pk=case.pk)
    if case.status != Case.Status.OPEN:
        raise ValidationError("This case is already decided.")
    if not frc_reference.strip():
        raise ValidationError("Enter the reference the FRC's goAML system gave the report.")
    if not note.strip():
        raise ValidationError("Summarise what was reported.")
    case.status, case.decided_by, case.decided_at = Case.Status.STR_FILED, user, timezone.now()
    case.frc_reference, case.decision_note = frc_reference.strip(), note.strip()
    case.save()
    late = case.decided_at > case.str_due_at
    record(user.username, "case.str_filed", case.ref,
           {"member": case.member.member_no, "frc_reference": case.frc_reference, "late": late})
    reassess(case.member, user.username)
    return case


@transaction.atomic
def close_not_suspicious(case: Case, *, user, note: str) -> Case:
    from members.services import reassess
    case = Case.objects.select_for_update().get(pk=case.pk)
    if case.status != Case.Status.OPEN:
        raise ValidationError("This case is already decided.")
    if case.case_type == Case.Type.SANCTIONS_MATCH:
        raise ValidationError("A confirmed sanctions match must be reported, not closed.")
    if not note.strip():
        raise ValidationError("Record why the activity is not suspicious.")
    case.status, case.decided_by, case.decided_at, case.decision_note = Case.Status.CLOSED, user, timezone.now(), note.strip()
    case.save()
    record(user.username, "case.closed_not_suspicious", case.ref, {"member": case.member.member_no, "note": case.decision_note})
    reassess(case.member, user.username)
    return case


def str_xml(case: Case, *, reporting_entity_id: str) -> str:
    from accounts.security import cipher
    from members.services import to_core
    member = to_core(case.member)
    member.id_number = cipher().decrypt(case.member.id_number_enc)
    action = case.decision_note or "Under review by the compliance officer."
    return str_report_xml(reporting_entity_id=reporting_entity_id, case_ref=case.ref, member=member,
                          reason=case.reason, action_taken=action, txns=[], submitted_at=timezone.localtime())
