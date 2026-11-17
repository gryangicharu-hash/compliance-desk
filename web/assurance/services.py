"""The audit workspace: building the data snapshot, running the engine's control tests,
sampling, findings and follow-up, and the board report."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils import timezone

from accounts.models import User
from audittrail.models import AuditEvent
from audittrail.services import _entry, record
from compliance_core import __version__ as ENGINE_VERSION
from compliance_core import assurance as A
from compliance_core.models import DueDiligence, Rating
from compliance_core.reports import board_summary
from compliance_core.risk import RiskResult

from .models import (Engagement, Finding, FindingResponse, ProgrammeStep, RiskAssessmentApproval, Sample,
                     TestResult, TestRun, TrainingRecord)
from .programme import PROGRAMME

ENGAGEMENT_LOCK = 7340004
ALERT_STATUS = {"to_review": "new", "case_opened": "escalated", "no_action": "dismissed"}


# ---------------------------------------------------------------- snapshot

def _risk(ra) -> RiskResult:
    return RiskResult(ra.score, dict(ra.points), Rating(ra.base_rating), Rating(ra.rating), tuple(ra.reasons),
                      tuple(ra.overrides), DueDiligence(ra.due_diligence), ra.next_review_on, ra.settings_version)


def snapshot(as_of: datetime | None = None) -> A.AuditData:
    """Everything the 17 tests need, read under the caller's database role (the
    auditor's role can read cases and alerts)."""
    from cases.models import Case
    from corebanking.models import Transaction
    from members.models import Member, RiskAssessment
    from members.services import to_core
    from monitoring.models import Alert, CashReport
    from monitoring.services import _core_txn, monitoring_settings
    from screening.models import Hit, ListVersion
    as_of = as_of or timezone.now()
    members = list(Member.objects.prefetch_related("persons").select_related("onboarded_by"))
    latest = {}
    for ra in RiskAssessment.objects.order_by("member_id", "-assessed_at", "-id").distinct("member_id"):
        latest[ra.member_id] = ra
    no_of = {m.pk: m.member_no for m in members}
    threshold = monitoring_settings().large_cash_kes
    large = Transaction.objects.filter(channel="cash", amount__gte=threshold).select_related("member")
    reports = []
    for r in CashReport.objects.prefetch_related("alerts"):
        txn_ids = frozenset(t for a in r.alerts.all() for t in a.txn_ids)
        reports.append(A.CashReportRecord(r.period_start, r.period_end, r.submitted_at, txn_ids))
    list_loads = ListVersion.objects.filter(status__in=["current", "superseded"]).order_by("-loaded_at")
    approval = RiskAssessmentApproval.objects.first()
    today = as_of.date()
    return A.AuditData(
        as_of=as_of,
        members=[to_core(m) for m in members],
        risk={no_of[pk]: _risk(ra) for pk, ra in latest.items()},
        onboarded_by={m.member_no: m.onboarded_by.username for m in members if m.onboarded_by},
        hits=[A.HitRecord(h.member.member_no, h.entry_ref, h.kind, h.status, h.raised_at, h.decided_at,
                          h.decided_by.username if h.decided_by else None, h.decision_note)
              for h in Hit.objects.select_related("member", "decided_by")],
        cases=[A.CaseRecord(c.ref, c.member.member_no, c.suspicion_formed_at, c.status, c.decided_at)
               for c in Case.objects.select_related("member")],
        alerts=[A.AlertRecord(str(a.pk), a.rule, a.member.member_no, a.raised_at, ALERT_STATUS[a.status], a.decided_at)
                for a in Alert.objects.filter(reportable_only=False).select_related("member")],
        txns=[_core_txn(t, t.member.member_no) for t in large],
        cash_reports=reports,
        large_cash_kes=threshold,
        rating_changes=[],          # ratings are calculated by rule; there are no manual overrides to approve
        last_list_update=list_loads.values_list("loaded_at", flat=True).first(),
        last_screened={m.member_no: m.last_screened_at for m in members if m.last_screened_at},
        audit_chain=[_entry(e) for e in AuditEvent.objects.order_by("seq")],
        staff_ids=list(User.objects.filter(is_active=True).order_by("username").values_list("username", flat=True)),
        training=[A.TrainingRecord(t.user.username, t.completed_on, t.valid_until)
                  for t in TrainingRecord.objects.select_related("user")],
        risk_assessment_approved_on=approval.approved_on if approval else None,
        current_settings_version=settings.RISK_SETTINGS_VERSION,
    )


# ---------------------------------------------------------------- engagements

def _next_ref(year: int) -> str:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", [ENGAGEMENT_LOCK])
    n = Engagement.objects.filter(ref__startswith=f"AE-{year}-").count() + 1
    return f"AE-{year}-{n:02d}"


@transaction.atomic
def open_engagement(*, user, kind, title, period_start, period_end, scope="") -> Engagement:
    if period_end < period_start:
        raise ValidationError("The period must end after it starts.")
    e = Engagement.objects.create(ref=_next_ref(period_end.year), kind=kind, title=title, period_start=period_start,
                                  period_end=period_end, scope=scope, lead_auditor=user)
    ProgrammeStep.objects.bulk_create([ProgrammeStep(engagement=e, step_no=n, area=area, regulation=reg,
                                                     procedure=proc, test_code=code)
                                       for n, area, reg, proc, code in PROGRAMME])
    record(user.username, "audit.engagement_opened", e.ref, {"kind": kind, "period": f"{period_start}..{period_end}"})
    return e


@transaction.atomic
def run_tests(engagement: Engagement, *, user) -> TestRun:
    if engagement.status == Engagement.Status.CLOSED:
        raise ValidationError("This engagement is closed.")
    data = snapshot()
    result = A.run_all(data)
    run = TestRun.objects.create(engagement=engagement, run_at=result.run_at, data_fingerprint=result.data_fingerprint,
                                 engine_version=ENGINE_VERSION, run_by=user)
    TestResult.objects.bulk_create([TestResult(
        run=run, code=r.code, title=r.title, regulation=r.regulation, severity=r.severity, population=r.population,
        exceptions=[[e.ref, e.detail] for e in r.exceptions], passed=r.passed) for r in result.results])
    if engagement.status == Engagement.Status.PLANNING:
        engagement.status = Engagement.Status.FIELDWORK
        engagement.save(update_fields=["status"])
    record(user.username, "audit.tests_run", engagement.ref, {
        "fingerprint": run.data_fingerprint, "failed": [r.code for r in result.failed], "engine": ENGINE_VERSION})
    return run


def to_engine_run(run: TestRun) -> A.AuditRun:
    return A.AuditRun(run.run_at, run.data_fingerprint, tuple(
        A.ControlResult(r.code, r.title, r.regulation, r.severity, r.population,
                        tuple(A.ExceptionItem(ref, detail) for ref, detail in r.exceptions))
        for r in run.results.all()))


# ---------------------------------------------------------------- samples

def populations(as_of=None) -> dict[str, tuple[str, list[str]]]:
    """Populations the auditor can sample from: key -> (label, references)."""
    from cases.models import Case
    from members.models import Member, Status
    from monitoring.models import Alert
    from screening.models import Hit
    active = Member.objects.exclude(status=Status.EXITED)
    return {
        "members": ("All active members", list(active.values_list("member_no", flat=True))),
        "groups": ("Active groups and companies", list(active.exclude(member_type="individual").values_list("member_no", flat=True))),
        "peps": ("Active PEPs", list(active.exclude(pep="none").values_list("member_no", flat=True))),
        "dismissed_alerts": ("Alerts closed with no further action",
                             [str(p) for p in Alert.objects.filter(status="no_action").values_list("pk", flat=True)]),
        "closed_cases": ("Cases closed as not suspicious",
                         list(Case.objects.filter(status="closed_not_suspicious").values_list("ref", flat=True))),
        "false_positives": ("Screening matches cleared as false positives",
                            [str(p) for p in Hit.objects.filter(status="false_positive").values_list("pk", flat=True)]),
    }


@transaction.atomic
def draw_sample(engagement: Engagement, *, user, population: str, size: int, seed: int, risk_based: bool = False) -> Sample:
    pops = populations()
    if population not in pops:
        raise ValidationError("Unknown population.")
    label, refs = pops[population]
    if not refs:
        raise ValidationError(f"There is nothing to sample in '{label}'.")
    if risk_based:
        if population != "members":
            raise ValidationError("A risk-based sample is drawn from all active members.")
        risk = {k: v for k, v in snapshot().risk.items() if k in set(refs)}
        per = max(1, size // 2)
        s = A.risk_based_sample(risk, seed=seed, medium_n=per, low_n=per)
    else:
        s = A.random_sample(refs, size, seed)
    sample = Sample.objects.create(engagement=engagement, population_name=label, method=s.method, seed=s.seed,
                                   population_size=s.population_size, selected=list(s.selected), created_by=user)
    record(user.username, "audit.sample_drawn", engagement.ref,
           {"population": label, "method": s.method, "seed": seed, "selected": len(s.selected)})
    return sample


# ---------------------------------------------------------------- findings

def _next_finding_ref(engagement: Engagement) -> str:
    n = engagement.findings.count() + 1
    return f"F{n:02d}"


@transaction.atomic
def draft_findings(engagement: Engagement, run: TestRun, *, user) -> list[Finding]:
    """One finding per failed test not already raised in this engagement."""
    have = set(engagement.findings.exclude(test_code="").values_list("test_code", flat=True))
    made = []
    for f in A.findings_from_run(to_engine_run(run)):
        if f.test_code in have:
            continue
        made.append(Finding.objects.create(
            engagement=engagement, ref=_next_finding_ref(engagement), title=f.title, regulation=f.regulation,
            severity=f.severity, condition=f.condition, recommendation=f.recommendation, test_code=f.test_code or ""))
    record(user.username, "audit.findings_drafted", engagement.ref, {"run": run.pk, "findings": [f.ref for f in made]})
    return made


def add_finding(engagement: Engagement, *, user, **fields) -> Finding:
    for k in ("title", "condition", "recommendation"):
        if not str(fields.get(k, "")).strip():
            raise ValidationError("Title, condition and recommendation are all required.")
    with transaction.atomic():
        f = Finding.objects.create(engagement=engagement, ref=_next_finding_ref(engagement), **fields)
    record(user.username, "audit.finding_raised", engagement.ref, {"finding": f.ref, "severity": f.severity})
    return f


def set_finding_status(finding: Finding, *, user, status: str, evidence: str = "") -> Finding:
    if status not in Finding.Status.values:
        raise ValidationError("Unknown status.")
    if status == Finding.Status.CLOSED:
        if not evidence.strip():
            raise ValidationError("Closing a finding needs evidence that it was fixed.")
        finding.closed_on, finding.closure_evidence = timezone.localdate(), evidence.strip()
    else:
        finding.closed_on, finding.closure_evidence = None, ""
    finding.status = status
    finding.save()
    record(user.username, "audit.finding_status", finding.engagement.ref, {"finding": finding.ref, "status": status})
    return finding


def respond(finding: Finding, *, user, response: str, owner: str, due_on: date) -> FindingResponse:
    if not response.strip() or not owner.strip():
        raise ValidationError("Give the response and the person responsible.")
    r, _ = FindingResponse.objects.update_or_create(finding=finding, defaults=dict(
        response=response.strip(), owner=owner.strip(), due_on=due_on, responded_by=user))
    record(user.username, "audit.management_response", finding.engagement.ref,
           {"finding": finding.ref, "owner": r.owner, "due": due_on})
    return r


def _engine_finding(f: Finding) -> A.Finding:
    resp = getattr(f, "response", None) if hasattr(f, "response") else None
    return A.Finding(f.ref, f.title, f.regulation, f.severity, f.condition, f.recommendation, f.test_code or None,
                     resp.response if resp else "", resp.owner if resp else "", resp.due_on if resp else None,
                     f.status, f.closed_on, f.closure_evidence)


def previous_open_findings(engagement: Engagement) -> list[Finding]:
    return list(Finding.objects.filter(engagement__created_at__lt=engagement.created_at)
                .exclude(status__in=[Finding.Status.CLOSED, Finding.Status.RISK_ACCEPTED])
                .select_related("engagement", "response"))


def follow_up(engagement: Engagement) -> list[tuple[Finding, str]]:
    run = engagement.runs.first()
    previous = previous_open_findings(engagement)
    if not run or not previous:
        return [(f, "Run this engagement's tests to retest") for f in previous] if not run else []
    states = A.follow_up([_engine_finding(f) for f in previous], to_engine_run(run), timezone.localdate())
    return [(f, state) for f, (_, state) in zip(previous, states)]


def report_text(engagement: Engagement) -> str:
    run = engagement.runs.first()
    if run is None:
        raise ValidationError("Run the automated tests before producing the report.")
    findings = [_engine_finding(f) for f in engagement.findings.select_related("response")]
    text = A.audit_report_text(to_engine_run(run), findings, sacco=settings.INSTITUTION_NAME,
                               period=f"{engagement.period_start:%d %b %Y} to {engagement.period_end:%d %b %Y}")
    fu = follow_up(engagement)
    if fu:
        text += "\n\nFollow-up of earlier findings:\n" + "\n".join(
            f"  {f.engagement.ref} {f.ref} {f.title}: {state}" for f, state in fu)
    return f"{engagement.ref} {engagement.get_kind_display()}: {engagement.title}\n\n{text}\n"


# ---------------------------------------------------------------- board report

def quarter(label: str) -> tuple[date, date]:
    """'2026-Q3' -> (2026-07-01, 2026-09-30)."""
    try:
        year, q = label.split("-Q")
        year, q = int(year), int(q)
        if not 1 <= q <= 4:
            raise ValueError
    except ValueError:
        raise ValidationError("Use a quarter like 2026-Q3.") from None
    start = date(year, 3 * q - 2, 1)
    end = date(year + (q == 4), 1 if q == 4 else 3 * q + 1, 1) - timedelta(days=1)
    return start, end


def board_report(start: date, end: date):
    """Figures for the board's AML/CFT report for a period, as at the end of it."""
    from cases.models import Case
    from cases.services import str_due
    from members.models import Member, RiskAssessment, Status
    from monitoring.models import Alert, CashReport
    from screening.models import Hit
    tz = timezone.get_current_timezone()
    t0 = datetime.combine(start, time.min, tz)
    t1 = datetime.combine(end + timedelta(days=1), time.min, tz)
    at_end = RiskAssessment.objects.filter(member=OuterRef("pk"), assessed_at__lt=t1).order_by("-assessed_at", "-id")
    members = (Member.objects.filter(created_at__lt=t1).filter(Q(exited_on__isnull=True) | Q(exited_on__gt=end))
               .annotate(r=Subquery(at_end.values("rating")[:1]), nr=Subquery(at_end.values("next_review_on")[:1])))
    rows = list(members.values("member_no", "r", "nr", "pep"))
    ratings = {r["member_no"]: Rating(r["r"]) for r in rows if r["r"]}
    filed = Case.objects.filter(status="str_filed", decided_at__gte=t0, decided_at__lt=t1)
    staff = list(User.objects.filter(is_active=True).values_list("pk", flat=True))
    trained = set(TrainingRecord.objects.filter(user_id__in=staff, completed_on__lte=end, valid_until__gte=end)
                  .values_list("user_id", flat=True))
    summary = board_summary(
        period=f"{start:%d %b %Y} to {end:%d %b %Y}", ratings=ratings,
        peps=sum(r["pep"] != "none" for r in rows),
        reviews_completed=AuditEvent.objects.filter(action="member.reviewed", at__gte=t0, at__lt=t1).count(),
        reviews_overdue=sum(1 for r in rows if r["nr"] and r["nr"] < end),
        hits_raised=Hit.objects.filter(raised_at__gte=t0, raised_at__lt=t1).count(),
        hits_confirmed=Hit.objects.filter(status="confirmed", decided_at__gte=t0, decided_at__lt=t1).count(),
        alert_rules=list(Alert.objects.filter(raised_at__gte=t0, raised_at__lt=t1, reportable_only=False)
                         .values_list("rule", flat=True)),
        strs_filed=filed.count(),
        strs_late=sum(1 for c in filed if c.decided_at > str_due(c.suspicion_formed_at)),
        cash_reports=CashReport.objects.filter(period_end__gte=start, period_end__lte=end).count(),
        staff_trained=len(trained), staff_total=len(staff))
    open_findings = (Finding.objects.exclude(status__in=[Finding.Status.CLOSED, Finding.Status.RISK_ACCEPTED])
                     .values("severity").annotate(n=Count("id")))
    overdue_findings = Finding.objects.filter(status__in=["open", "in_progress"], response__due_on__lt=end).count()
    return summary, {f["severity"]: f["n"] for f in open_findings}, overdue_findings
