"""AML audit tools for internal auditors, external auditors and inspection preparation.

The auditor gets:
  * automated control tests over the whole population, each tied to a regulation;
  * reproducible sampling for manual file testing;
  * a findings log with management responses and follow-up;
  * an evidence fingerprint: every run records a SHA-256 hash of the data it tested,
    so the result can be tied to exactly that data later.

All functions are read-only over an AuditData snapshot. The web layer builds the
snapshot from the database under the auditor's read-only role.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from . import audit as audit_log
from .deadlines import cash_report_due, str_due
from .models import Member, MemberStatus, PepStatus, Rating, Transaction, TxnChannel
from .risk import RiskResult


# ---------------------------------------------------------------- snapshot records
@dataclass(frozen=True)
class HitRecord:
    member_no: str
    list_ref: str
    kind: str                       # "sanctions" or "pep"
    status: str                     # "to_review", "false_positive", "confirmed"
    raised_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_note: str = ""


@dataclass(frozen=True)
class CaseRecord:
    ref: str
    member_no: str
    suspicion_formed_at: datetime
    status: str                     # "open", "str_filed", "closed_not_suspicious"
    decided_at: datetime | None = None


@dataclass(frozen=True)
class CashReportRecord:
    period_start: date
    period_end: date
    filed_at: datetime | None
    txn_ids: frozenset[str]


@dataclass(frozen=True)
class AlertRecord:
    alert_id: str
    rule: str
    member_no: str
    raised_at: datetime
    status: str                     # "new", "escalated", "dismissed"
    reviewed_at: datetime | None = None


@dataclass(frozen=True)
class RatingChange:
    member_no: str
    at: datetime
    old: Rating
    new: Rating
    changed_by: str
    second_approver: str | None = None


@dataclass(frozen=True)
class TrainingRecord:
    staff_id: str
    completed_on: date
    valid_until: date


@dataclass
class AuditData:
    as_of: datetime
    members: list[Member]
    risk: dict[str, RiskResult]                    # latest assessment per member
    onboarded_by: dict[str, str] = field(default_factory=dict)
    hits: list[HitRecord] = field(default_factory=list)
    cases: list[CaseRecord] = field(default_factory=list)
    alerts: list[AlertRecord] = field(default_factory=list)
    txns: list[Transaction] = field(default_factory=list)
    cash_reports: list[CashReportRecord] = field(default_factory=list)
    large_cash_kes: Decimal = Decimal("1935000")
    rating_changes: list[RatingChange] = field(default_factory=list)
    last_list_update: datetime | None = None
    last_screened: dict[str, datetime] = field(default_factory=dict)
    audit_chain: list[audit_log.AuditEntry] = field(default_factory=list)
    staff_ids: list[str] = field(default_factory=list)
    training: list[TrainingRecord] = field(default_factory=list)
    risk_assessment_approved_on: date | None = None
    current_settings_version: str | None = None


@dataclass(frozen=True)
class Params:
    hit_review_days: int = 5          # potential matches decided within this many days
    alert_review_days: int = 10
    risk_assessment_max_age_days: int = 730     # reg. 6: at least every two years
    training_valid_required: float = 1.0        # share of staff with current training


# ---------------------------------------------------------------- results
@dataclass(frozen=True)
class ExceptionItem:
    ref: str            # member number, case ref, transaction id...
    detail: str


@dataclass(frozen=True)
class ControlResult:
    code: str
    title: str
    regulation: str
    severity: str        # severity if it fails: "high", "medium", "low"
    population: int
    exceptions: tuple[ExceptionItem, ...]

    @property
    def passed(self) -> bool:
        return not self.exceptions

    @property
    def exception_rate(self) -> float:
        return len(self.exceptions) / self.population if self.population else 0.0


@dataclass(frozen=True)
class AuditRun:
    run_at: datetime
    data_fingerprint: str
    results: tuple[ControlResult, ...]

    @property
    def failed(self) -> list[ControlResult]:
        return [r for r in self.results if not r.passed]


TESTS: list[tuple[str, str, str, str]] = []      # (code, title, regulation, severity)


def _test(code: str, title: str, regulation: str, severity: str):
    def wrap(fn):
        TESTS.append((code, title, regulation, severity))
        fn.meta = (code, title, regulation, severity)
        _REGISTRY.append(fn)
        return fn
    return wrap


_REGISTRY: list = []


def _active(d: AuditData) -> list[Member]:
    return [m for m in d.members if m.status is not MemberStatus.EXITED]


# ---------------------------------------------------------------- the tests
@_test("AT01", "Active members with incomplete KYC files", "Regs 12–14", "high")
def t_kyc(d, p):
    pop = _active(d)
    return len(pop), [ExceptionItem(m.member_no, "KYC file marked incomplete") for m in pop if not m.kyc_complete]


@_test("AT02", "Active members with an expired ID", "Regs 12–13, 29–30", "medium")
def t_id_expired(d, p):
    pop = _active(d)
    today = d.as_of.date()
    return len(pop), [ExceptionItem(m.member_no, f"ID expired {m.id_expiry}") for m in pop
                      if m.id_expiry and m.id_expiry < today]


@_test("AT03", "Members with no source of funds recorded", "Regs 12, 22", "medium")
def t_sof(d, p):
    pop = _active(d)
    return len(pop), [ExceptionItem(m.member_no, "Source of funds blank") for m in pop if not m.source_of_funds.strip()]


@_test("AT04", "Active PEPs without senior management approval", "Reg. 22", "high")
def t_pep(d, p):
    peps = [m for m in _active(d) if m.pep is not PepStatus.NONE]
    return len(peps), [ExceptionItem(m.member_no, "Active PEP, no approval recorded") for m in peps
                       if m.status is MemberStatus.ACTIVE and not m.pep_approved]


@_test("AT05", "Overdue reviews of high-risk members", "Regs 29–30", "high")
def t_reviews(d, p):
    today = d.as_of.date()
    high = [m for m in _active(d) if m.member_no in d.risk and d.risk[m.member_no].rating in (Rating.HIGH, Rating.PROHIBITED)]
    out = []
    for m in high:
        nxt = d.risk[m.member_no].next_review
        if nxt is None or nxt < today:
            out.append(ExceptionItem(m.member_no, f"Review due {nxt or 'never scheduled'}"))
    return len(high), out


@_test("AT06", "Members without a current risk assessment", "Reg. 6", "high")
def t_risk_missing(d, p):
    pop = _active(d)
    out = []
    for m in pop:
        r = d.risk.get(m.member_no)
        if r is None:
            out.append(ExceptionItem(m.member_no, "No risk assessment"))
        elif d.current_settings_version and r.settings_version != d.current_settings_version:
            out.append(ExceptionItem(m.member_no, f"Assessed under superseded settings {r.settings_version}"))
    return len(pop), out


@_test("AT07", "Screening decisions without a recorded reason or by the onboarding officer", "Reg. 22; PTA (TFS)", "high")
def t_hit_decisions(d, p):
    decided = [h for h in d.hits if h.status != "to_review"]
    out = []
    for h in decided:
        if not h.decided_by or not h.decision_note.strip():
            out.append(ExceptionItem(h.member_no, f"{h.list_ref}: no decider or reason recorded"))
        elif d.onboarded_by.get(h.member_no) == h.decided_by:
            out.append(ExceptionItem(h.member_no, f"{h.list_ref}: cleared by the officer who onboarded the member"))
    return len(decided), out


@_test("AT08", "Potential matches left undecided too long", "PTA (TFS without delay)", "high")
def t_hit_age(d, p):
    limit = timedelta(days=p.hit_review_days)
    out = []
    for h in d.hits:
        end = h.decided_at or d.as_of
        if end - h.raised_at > limit:
            out.append(ExceptionItem(h.member_no, f"{h.list_ref}: {(end - h.raised_at).days} days to decide"))
    return len(d.hits), out


@_test("AT09", "Confirmed sanctions matches not blocked", "PTA (TFS)", "high")
def t_blocked(d, p):
    by = {m.member_no: m for m in d.members}
    confirmed = [h for h in d.hits if h.kind == "sanctions" and h.status == "confirmed"]
    return len(confirmed), [ExceptionItem(h.member_no, f"Status is {by[h.member_no].status.value}") for h in confirmed
                            if h.member_no in by and by[h.member_no].status is not MemberStatus.BLOCKED]


@_test("AT10", "Members not re-screened since the last list update", "PTA (TFS)", "high")
def t_rescreen(d, p):
    if d.last_list_update is None:
        return 0, []
    pop = _active(d)
    return len(pop), [ExceptionItem(m.member_no, f"Last screened {d.last_screened.get(m.member_no, 'never')}")
                      for m in pop if d.last_screened.get(m.member_no) is None
                      or d.last_screened[m.member_no] < d.last_list_update]


@_test("AT11", "Suspicious transaction reports late or overdue", "Reg. 33", "high")
def t_str(d, p):
    out = []
    for c in d.cases:
        due = str_due(c.suspicion_formed_at)
        if c.status == "open" and d.as_of > due:
            out.append(ExceptionItem(c.ref, f"Open past the {due:%d %b %Y %H:%M} deadline"))
        elif c.status == "str_filed" and c.decided_at and c.decided_at > due:
            hours = (c.decided_at - due).total_seconds() / 3600
            out.append(ExceptionItem(c.ref, f"Filed {hours:.0f} hours late"))
    return len(d.cases), out


@_test("AT12", "Large cash transactions missing from a filed weekly report", "Reg. 35", "high")
def t_cash(d, p):
    large = [t for t in d.txns if t.channel is TxnChannel.CASH and t.amount >= d.large_cash_kes]
    out = []
    for t in large:
        day = t.timestamp.date()
        due = cash_report_due(day)
        if due >= d.as_of.date():
            continue                     # not due yet
        covering = [r for r in d.cash_reports if r.period_start <= day <= r.period_end and t.txn_id in r.txn_ids]
        if not covering or all(r.filed_at is None for r in covering):
            out.append(ExceptionItem(t.txn_id, f"Due {due}; not in a filed report"))
        elif min(r.filed_at for r in covering if r.filed_at).date() > due:
            out.append(ExceptionItem(t.txn_id, f"Reported late (due {due})"))
    return len(large), out


@_test("AT13", "Monitoring alerts not reviewed in time", "Regs 29–30", "medium")
def t_alerts(d, p):
    limit = timedelta(days=p.alert_review_days)
    out = []
    for a in d.alerts:
        end = a.reviewed_at or d.as_of
        if (a.status == "new" or a.reviewed_at) and end - a.raised_at > limit:
            out.append(ExceptionItem(a.alert_id, f"{a.rule} on {a.member_no}: {(end - a.raised_at).days} days"))
    return len(d.alerts), out


@_test("AT14", "Risk ratings lowered without a second approval", "Reg. 6 (controls)", "medium")
def t_downgrades(d, p):
    downs = [c for c in d.rating_changes if c.new.rank < c.old.rank]
    return len(downs), [ExceptionItem(c.member_no, f"{c.old.value} to {c.new.value} by {c.changed_by}") for c in downs
                        if not c.second_approver or c.second_approver == c.changed_by]


@_test("AT15", "Audit trail integrity", "Reg. 37", "high")
def t_chain(d, p):
    bad = audit_log.verify(d.audit_chain)
    return len(d.audit_chain), ([ExceptionItem(f"entry {bad}", "Hash chain broken: record altered, removed or reordered")]
                                if bad is not None else [])


@_test("AT16", "Staff without current AML training", "Internal controls", "medium")
def t_training(d, p):
    today = d.as_of.date()
    current = {t.staff_id for t in d.training if t.valid_until >= today}
    return len(d.staff_ids), [ExceptionItem(s, "No current AML training record") for s in d.staff_ids if s not in current]


@_test("AT17", "Institutional risk assessment approved within two years", "Reg. 6", "high")
def t_institutional(d, p):
    a = d.risk_assessment_approved_on
    if a is None:
        return 1, [ExceptionItem("risk assessment", "No board approval recorded")]
    age = (d.as_of.date() - a).days
    return 1, ([ExceptionItem("risk assessment", f"Last approved {a} ({age} days ago)")]
               if age > p.risk_assessment_max_age_days else [])


# ---------------------------------------------------------------- running
def fingerprint(d: AuditData) -> str:
    """SHA-256 of the tested data, so a run can be tied to the exact snapshot."""
    def default(o):
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if isinstance(o, (Decimal,)):
            return str(o)
        if isinstance(o, (set, frozenset)):
            return sorted(o)
        if hasattr(o, "value"):
            return o.value
        raise TypeError(type(o))
    payload = json.dumps(asdict(d), sort_keys=True, default=default, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def run_all(d: AuditData, p: Params = Params(), *, only: set[str] | None = None) -> AuditRun:
    results = []
    for fn in _REGISTRY:
        code, title, reg, sev = fn.meta
        if only and code not in only:
            continue
        pop, exc = fn(d, p)
        results.append(ControlResult(code, title, reg, sev, pop, tuple(exc)))
    return AuditRun(d.as_of, fingerprint(d), tuple(results))


# ---------------------------------------------------------------- sampling
@dataclass(frozen=True)
class Sample:
    method: str
    seed: int
    population_size: int
    selected: tuple[str, ...]

    def describe(self) -> str:
        return (f"{self.method}: {len(self.selected)} of {self.population_size} selected, seed {self.seed}. "
                f"Re-run with the same population and seed to reproduce.")


def random_sample(population: list[str], n: int, seed: int) -> Sample:
    pop = sorted(set(population))
    chosen = random.Random(seed).sample(pop, min(n, len(pop)))
    return Sample("Simple random", seed, len(pop), tuple(sorted(chosen)))


def risk_based_sample(risk: dict[str, RiskResult], *, seed: int, all_high: bool = True,
                      medium_n: int = 10, low_n: int = 10) -> Sample:
    """Every high and prohibited member, plus random medium and low ones."""
    by = {"high": [], "medium": [], "low": []}
    for no, r in risk.items():
        by["high" if r.rating in (Rating.HIGH, Rating.PROHIBITED) else r.rating.value].append(no)
    rng = random.Random(seed)
    pick = lambda xs, n: rng.sample(sorted(xs), min(n, len(xs)))
    chosen = (sorted(by["high"]) if all_high else []) + pick(by["medium"], medium_n) + pick(by["low"], low_n)
    return Sample(f"Risk-based (all high, {medium_n} medium, {low_n} low)", seed, len(risk), tuple(sorted(chosen)))


# ---------------------------------------------------------------- findings
@dataclass
class Finding:
    ref: str
    title: str
    regulation: str
    severity: str                       # "high", "medium", "low"
    condition: str                      # what was found
    recommendation: str
    test_code: str | None = None
    management_response: str = ""
    owner: str = ""
    due: date | None = None
    status: str = "open"                # "open", "in_progress", "closed", "risk_accepted"
    closed_on: date | None = None
    closure_evidence: str = ""

    def close(self, on: date, evidence: str) -> None:
        if not evidence.strip():
            raise ValueError("Closing a finding needs evidence of the fix")
        self.status, self.closed_on, self.closure_evidence = "closed", on, evidence

    def overdue(self, today: date) -> bool:
        return self.status in ("open", "in_progress") and self.due is not None and today > self.due


def findings_from_run(run: AuditRun, *, prefix: str = "F") -> list[Finding]:
    out = []
    for i, r in enumerate(run.failed, 1):
        sample = "; ".join(f"{e.ref} ({e.detail})" for e in r.exceptions[:3])
        more = f" and {len(r.exceptions) - 3} more" if len(r.exceptions) > 3 else ""
        out.append(Finding(
            ref=f"{prefix}{i:02d}", title=r.title, regulation=r.regulation, severity=r.severity, test_code=r.code,
            condition=f"{len(r.exceptions)} exception(s) in a population of {r.population}: {sample}{more}.",
            recommendation="Correct the exceptions listed and fix the control that allowed them."))
    return out


def follow_up(previous: list[Finding], current_run: AuditRun, today: date) -> list[tuple[Finding, str]]:
    """For each earlier finding: still failing, fixed per retest, or overdue."""
    failing = {r.code for r in current_run.failed}
    out = []
    for f in previous:
        if f.test_code and f.test_code in failing:
            state = "Still failing on retest"
        elif f.test_code:
            state = "Passes on retest"
        else:
            state = "Manual follow-up needed"
        if f.overdue(today):
            state += "; past agreed date"
        out.append((f, state))
    return out


def audit_report_text(run: AuditRun, findings: list[Finding], *, sacco: str, period: str) -> str:
    lines = [f"AML/CFT audit: {sacco}", f"Period: {period}", f"Tests run {run.run_at:%d %b %Y %H:%M}; data fingerprint {run.data_fingerprint[:16]}…", ""]
    passed = sum(r.passed for r in run.results)
    lines.append(f"Automated tests: {passed} of {len(run.results)} passed.")
    for r in run.results:
        mark = "PASS" if r.passed else "FAIL"
        lines.append(f"  {r.code} {mark}  {r.title} [{r.regulation}]: {len(r.exceptions)} of {r.population}")
    lines.append("")
    lines.append("Findings:")
    for f in sorted(findings, key=lambda f: ["high", "medium", "low"].index(f.severity)):
        lines.append(f"  {f.ref} ({f.severity}) {f.title}. {f.condition}")
        if f.management_response:
            lines.append(f"     Management: {f.management_response} Owner: {f.owner or 'unassigned'}; due {f.due or 'not set'}.")
    return "\n".join(lines)
