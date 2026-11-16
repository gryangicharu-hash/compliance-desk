from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from compliance_core import assurance as A
from compliance_core import audit
from compliance_core.models import Direction, MemberStatus, PepStatus, Rating, Transaction, TxnChannel
from compliance_core.risk import RiskInputs, RiskSettings, assess

EAT = timezone(timedelta(hours=3))
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=EAT)
S = RiskSettings(version="v1")


@pytest.fixture
def clean(make_member):
    """A snapshot in which every control works: all 17 tests should pass."""
    members = [make_member(no=f"M{i:04d}", last_review=date(2026, 9, 1)) for i in range(1, 6)]
    risk = {m.member_no: assess(m, RiskInputs(), S, as_of=NOW.date()) for m in members}
    chain = []
    for i in range(3):
        audit.append(chain, at=NOW - timedelta(hours=3 - i), actor="co", action="x", subject=str(i))
    big = Transaction("T1", "M0001", NOW - timedelta(days=10), Decimal("2000000"), Direction.IN, TxnChannel.CASH)
    report = A.CashReportRecord(date(2026, 11, 2), date(2026, 11, 8), datetime(2026, 11, 6, 9, tzinfo=EAT), frozenset({"T1"}))
    return A.AuditData(
        as_of=NOW, members=members, risk=risk, onboarded_by={m.member_no: "teller1" for m in members},
        hits=[A.HitRecord("M0002", "XXi.101", "sanctions", "false_positive", NOW - timedelta(days=3),
                          NOW - timedelta(days=2), "co", "DOB and nationality differ")],
        cases=[A.CaseRecord("C-1", "M0003", NOW - timedelta(days=5), "str_filed", NOW - timedelta(days=4))],
        alerts=[A.AlertRecord("A1", "STRUCTURING", "M0003", NOW - timedelta(days=6), "escalated", NOW - timedelta(days=5))],
        txns=[big], cash_reports=[report],
        rating_changes=[A.RatingChange("M0004", NOW - timedelta(days=30), Rating.HIGH, Rating.MEDIUM, "co", "ceo")],
        last_list_update=NOW - timedelta(hours=6), last_screened={m.member_no: NOW - timedelta(hours=5) for m in members},
        audit_chain=chain, staff_ids=["teller1", "co"],
        training=[A.TrainingRecord("teller1", date(2026, 3, 1), date(2027, 3, 1)), A.TrainingRecord("co", date(2026, 1, 10), date(2027, 1, 10))],
        risk_assessment_approved_on=date(2026, 1, 30), current_settings_version="v1")


def results(data, **kw):
    return {r.code: r for r in A.run_all(data, **kw).results}


def test_all_seventeen_tests_pass_on_clean_data(clean):
    run = A.run_all(clean)
    assert len(run.results) == 17 and run.failed == []
    assert len(run.data_fingerprint) == 64


def test_fingerprint_changes_with_data(clean):
    a = A.fingerprint(clean)
    clean.members[0].kyc_complete = False
    assert A.fingerprint(clean) != a


def test_member_file_exceptions(clean):
    m1, m2, m3 = clean.members[:3]
    m1.kyc_complete = False
    m2.id_expiry = date(2026, 10, 1)
    m3.source_of_funds = ""
    m3.pep, m3.pep_approved = PepStatus.DOMESTIC, False
    r = results(clean)
    assert [e.ref for e in r["AT01"].exceptions] == ["M0001"]
    assert [e.ref for e in r["AT02"].exceptions] == ["M0002"]
    assert [e.ref for e in r["AT03"].exceptions] == ["M0003"]
    assert [e.ref for e in r["AT04"].exceptions] == ["M0003"] and r["AT04"].population == 1


def test_exited_members_excluded(clean):
    clean.members[0].kyc_complete = False
    clean.members[0].status = MemberStatus.EXITED
    assert results(clean)["AT01"].passed


def test_overdue_high_risk_review_and_stale_assessment(clean, make_member):
    m = clean.members[0]
    m.pep, m.pep_approved, m.last_review = PepStatus.DOMESTIC, True, date(2025, 1, 1)
    clean.risk[m.member_no] = assess(m, RiskInputs(), S, as_of=NOW.date())
    clean.risk["M0005"] = replace(clean.risk["M0005"], settings_version="v0")
    r = results(clean)
    assert [e.ref for e in r["AT05"].exceptions] == ["M0001"]
    assert [e.ref for e in r["AT06"].exceptions] == ["M0005"]


def test_screening_controls(clean):
    clean.hits.append(A.HitRecord("M0001", "XXi.102", "sanctions", "false_positive", NOW - timedelta(days=1), NOW, "teller1", "looks different"))
    clean.hits.append(A.HitRecord("M0003", "XXe.201", "sanctions", "false_positive", NOW - timedelta(days=1), NOW, "co", ""))
    clean.hits.append(A.HitRecord("M0004", "XXi.103", "sanctions", "to_review", NOW - timedelta(days=9)))
    clean.hits.append(A.HitRecord("M0005", "XXi.104", "sanctions", "confirmed", NOW - timedelta(days=1), NOW, "co", "Exact DOB match"))
    clean.last_screened["M0002"] = NOW - timedelta(days=2)
    r = results(clean)
    assert {e.ref for e in r["AT07"].exceptions} == {"M0001", "M0003"}       # same officer; no reason
    assert [e.ref for e in r["AT08"].exceptions] == ["M0004"]
    assert [e.ref for e in r["AT09"].exceptions] == ["M0005"]                # confirmed but still active
    assert [e.ref for e in r["AT10"].exceptions] == ["M0002"]


def test_str_and_cash_reporting(clean):
    clean.cases.append(A.CaseRecord("C-2", "M0001", NOW - timedelta(days=3), "open"))
    clean.cases.append(A.CaseRecord("C-3", "M0002", NOW - timedelta(days=10), "str_filed", NOW - timedelta(days=7)))
    clean.txns.append(Transaction("T2", "M0002", NOW - timedelta(days=9), Decimal("3000000"), Direction.IN, TxnChannel.CASH))
    clean.txns.append(Transaction("T3", "M0002", NOW - timedelta(days=1), Decimal("3000000"), Direction.IN, TxnChannel.CASH))  # not due yet
    r = results(clean)
    assert {e.ref for e in r["AT11"].exceptions} == {"C-2", "C-3"}
    assert [e.ref for e in r["AT12"].exceptions] == ["T2"] and r["AT12"].population == 3


def test_alerts_downgrades_chain_training_and_institutional(clean):
    clean.alerts.append(A.AlertRecord("A2", "INCOME_MISMATCH", "M0005", NOW - timedelta(days=15), "new"))
    clean.rating_changes.append(A.RatingChange("M0001", NOW, Rating.HIGH, Rating.LOW, "co", "co"))
    tampered = list(clean.audit_chain)
    tampered[1] = audit.AuditEntry(**{**audit.to_dict(tampered[1]), "subject": "edited"})
    clean.audit_chain = tampered
    clean.staff_ids.append("new_clerk")
    clean.risk_assessment_approved_on = date(2024, 6, 1)
    r = results(clean)
    assert [e.ref for e in r["AT13"].exceptions] == ["A2"]
    assert [e.ref for e in r["AT14"].exceptions] == ["M0001"]                # self-approved downgrade
    assert [e.ref for e in r["AT15"].exceptions] == ["entry 2"]
    assert [e.ref for e in r["AT16"].exceptions] == ["new_clerk"]
    assert not r["AT17"].passed


def test_run_subset(clean):
    assert [r.code for r in A.run_all(clean, only={"AT04", "AT15"}).results] == ["AT04", "AT15"]


def test_sampling_is_reproducible(clean):
    pop = [f"M{i:04d}" for i in range(1, 201)]
    a, b = A.random_sample(pop, 25, seed=2026), A.random_sample(pop, 25, seed=2026)
    assert a == b and len(a.selected) == 25 and "seed 2026" in a.describe()
    assert A.random_sample(pop, 25, seed=7) != a
    m = clean.members[0]
    m.pep, m.pep_approved = PepStatus.DOMESTIC, True
    clean.risk[m.member_no] = assess(m, RiskInputs(), S, as_of=NOW.date())
    s = A.risk_based_sample(clean.risk, seed=1, medium_n=0, low_n=2)
    assert "M0001" in s.selected and len(s.selected) == 3


def test_findings_follow_up_and_report(clean):
    clean.members[0].kyc_complete = False
    run1 = A.run_all(clean)
    [f] = A.findings_from_run(run1)
    assert f.test_code == "AT01" and f.severity == "high" and "1 exception(s) in a population of 5" in f.condition
    f.management_response, f.owner, f.due = "Files being completed", "Head of Operations", date(2026, 12, 15)
    with pytest.raises(ValueError):
        f.close(date(2026, 12, 1), "")
    assert A.follow_up([f], run1, date(2026, 12, 20))[0][1] == "Still failing on retest; past agreed date"
    clean.members[0].kyc_complete = True
    run2 = A.run_all(clean)
    assert A.follow_up([f], run2, date(2026, 12, 1))[0][1] == "Passes on retest"
    text = A.audit_report_text(run1, [f], sacco="Mfano SACCO", period="FY 2026")
    assert "16 of 17 passed" in text and "AT01 FAIL" in text and "Head of Operations" in text
