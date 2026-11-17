"""Running the monitoring rules, deciding alerts, and the weekly large cash report."""
from __future__ import annotations

import hashlib
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.security import cipher
from audittrail.services import record
from compliance_core import models as core
from compliance_core import monitoring as M
from compliance_core.deadlines import cash_report_due
from compliance_core.reports import cash_report_csv
from corebanking.models import Transaction
from members.models import Member, Status

from .models import Alert, CashReport, Rule

LOOKBACK_DAYS = 400            # history loaded per member: enough to see a 180-day dormant gap
ACTIVE_DAYS = 31               # members with any transaction this recent are checked
WINDOW_RULES = {"INCOME_MISMATCH", "MANY_PAYERS"}      # rules over a rolling 30-day window
QUIET_DAYS = 30                # a window rule is not raised again for the same member within this time


def monitoring_settings() -> M.MonitoringSettings:
    return M.MonitoringSettings(usd_rate=Decimal(settings.USD_KES_RATE))


def _key(rule: str, member_no: str, txn_ids) -> str:
    return hashlib.sha256("|".join([rule, member_no, *sorted(txn_ids)]).encode()).hexdigest()


def _core_txn(t: Transaction, member_no: str) -> core.Transaction:
    return core.Transaction(t.txn_id, member_no, t.at, Decimal(t.amount), core.Direction(t.direction),
                            core.TxnChannel(t.channel), t.counterparty or None, t.purpose)


def _is_repeat(a: M.Alert, earlier: list[Alert], now) -> bool:
    """Rolling rules would otherwise raise a fresh alert every night for the same pattern."""
    for e in earlier:
        if e.rule != a.rule:
            continue
        if a.rule in WINDOW_RULES and e.raised_at >= now - timedelta(days=QUIET_DAYS):
            return True
        if a.rule not in WINDOW_RULES and set(e.txn_ids) & set(a.txn_ids):
            return True
    return False


def run_monitoring(actor: str, *, as_of=None, member_ids=None) -> dict:
    """Trusted code: runs under the 'system' database role, so alerts are written even
    when an administrator's import triggered the run (administrators can't read them)."""
    from accounts.security import db_role
    with db_role("system"):
        return _run(actor, as_of=as_of, member_ids=member_ids)


def _run(actor: str, *, as_of=None, member_ids=None) -> dict:
    from members.services import reassess, to_core
    now = as_of or timezone.now()
    s = monitoring_settings()
    if member_ids is None:
        member_ids = (Transaction.objects.filter(at__gte=now - timedelta(days=ACTIVE_DAYS), at__lte=now)
                      .values_list("member_id", flat=True).distinct())
    members = Member.objects.filter(pk__in=list(member_ids)).exclude(status=Status.EXITED).prefetch_related("persons")
    raised = {"members_checked": 0, "alerts": 0, "for_cash_report": 0}
    for member in members:
        raised["members_checked"] += 1
        txns = [_core_txn(t, member.member_no) for t in Transaction.objects.filter(
            member=member, at__gte=now - timedelta(days=LOOKBACK_DAYS), at__lte=now)]
        found = M.run_rules(to_core(member), txns, s, as_of=now)
        earlier = list(Alert.objects.filter(member=member))
        keys = {e.key for e in earlier}
        suspicious = False
        for a in found:
            k = _key(a.rule, a.member_no, a.txn_ids)
            if k in keys or _is_repeat(a, earlier, now):
                continue
            alert = Alert.objects.create(
                rule=a.rule, member=member, txn_ids=list(a.txn_ids), key=k, description=a.description,
                reportable_only=a.reportable_only,
                status=Alert.Status.FOR_CASH_REPORT if a.reportable_only else Alert.Status.TO_REVIEW)
            earlier.append(alert)
            keys.add(k)
            record(actor, "monitoring.alert_raised", member.member_no, {"rule": a.rule, "alert": alert.pk})
            raised["for_cash_report" if a.reportable_only else "alerts"] += 1
            suspicious |= not a.reportable_only
        if suspicious:
            reassess(member, actor)
    record(actor, "monitoring.run", "transaction monitoring", raised)
    return raised


@transaction.atomic
def decide(alert: Alert, *, user, action: str, note: str) -> Alert:
    from cases.services import open_case
    alert = Alert.objects.select_for_update().select_related("member").get(pk=alert.pk)
    if alert.status != Alert.Status.TO_REVIEW:
        raise ValidationError("This alert has already been dealt with.")
    if not note.strip():
        raise ValidationError("Record what you checked and why you decided this.")
    alert.decided_by, alert.decided_at, alert.note = user, timezone.now(), note.strip()
    if action == "no_action":
        alert.status = Alert.Status.NO_ACTION
    elif action == "open_case":
        alert.case = open_case(alert.member, user=user, case_type="unusual_activity",
                               reason=f"Monitoring alert: {alert.get_rule_display()}. {alert.description}. {note.strip()}")
        alert.status = Alert.Status.CASE_OPENED
    else:
        raise ValidationError("Unknown decision.")
    alert.save()
    record(user.username, f"monitoring.alert_{action}", alert.member.member_no,
           {"alert": alert.pk, "rule": alert.rule, "case": alert.case.ref if alert.case else None})
    return alert


# ---------------------------------------------------------------- weekly large cash report

def week_ending(d: date) -> date:
    """The Friday a transaction on day d is reported by (Saturday to Friday weeks)."""
    return cash_report_due(d)


def pending_weeks() -> list[dict]:
    """Weeks with large cash transactions not yet reported, oldest first."""
    weeks: dict[date, list[Alert]] = {}
    alerts = Alert.objects.filter(status=Alert.Status.FOR_CASH_REPORT).select_related("member")
    txn_dates = dict(Transaction.objects.filter(txn_id__in=[t for a in alerts for t in a.txn_ids])
                     .values_list("txn_id", "at"))
    for a in alerts:
        d = timezone.localtime(txn_dates[a.txn_ids[0]]).date()
        weeks.setdefault(week_ending(d), []).append(a)
    today = timezone.localdate()
    return [{"period_end": end, "period_start": end - timedelta(days=6), "alerts": al,
             "overdue": end < today, "due_today": end == today} for end, al in sorted(weeks.items())]


def cash_report(period_end: date) -> tuple[str, list[Alert]]:
    alerts = [a for w in pending_weeks() if w["period_end"] == period_end for a in w["alerts"]]
    txns = Transaction.objects.filter(txn_id__in=[t for a in alerts for t in a.txn_ids]).select_related("member")
    members = {}
    for t in txns:
        if t.member.member_no not in members:
            m = to_core_with_id(t.member)
            members[m.member_no] = m
    csv_text = cash_report_csv([_core_txn(t, t.member.member_no) for t in txns], members,
                               period_start=period_end - timedelta(days=6), period_end=period_end,
                               threshold_kes=monitoring_settings().large_cash_kes)
    return csv_text, alerts


def to_core_with_id(member: Member) -> core.Member:
    from members.services import to_core
    m = to_core(member)
    m.id_number = cipher().decrypt(member.id_number_enc)
    return m


@transaction.atomic
def submit_cash_report(period_end: date, *, user, frc_reference: str) -> CashReport:
    if not frc_reference.strip():
        raise ValidationError("Enter the reference the FRC gave the submission.")
    if CashReport.objects.filter(period_end=period_end).exists():
        raise ValidationError("This week's report is already recorded.")
    csv_text, alerts = cash_report(period_end)
    if not alerts:
        raise ValidationError("There are no large cash transactions waiting for this week.")
    report = CashReport.objects.create(period_start=period_end - timedelta(days=6), period_end=period_end,
                                       rows=len(csv_text.strip().splitlines()) - 1, frc_reference=frc_reference.strip(),
                                       submitted_by=user, file_sha256=hashlib.sha256(csv_text.encode()).hexdigest())
    Alert.objects.filter(pk__in=[a.pk for a in alerts]).update(status=Alert.Status.REPORTED, cash_report=report)
    record(user.username, "cash_report.submitted", f"week ending {period_end:%Y-%m-%d}",
           {"rows": report.rows, "frc_reference": report.frc_reference, "sha256": report.file_sha256,
            "late": timezone.localdate() > period_end})
    return report


RULE_LABELS = dict(Rule.choices)
