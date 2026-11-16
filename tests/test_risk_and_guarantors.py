from datetime import date
from decimal import Decimal

import pytest

from compliance_core.guarantors import Flag, analyse, find_cycles
from compliance_core.models import (Channel, DueDiligence, Guarantee, MemberStatus, MemberType, PepStatus,
                                    Rating, ScreeningStatus)
from compliance_core.risk import RiskInputs, RiskSettings, add_months, assess

S = RiskSettings(version="2026-11-board-v1")
TODAY = date(2026, 11, 16)


def g(loan, b, gu, amt=100000):
    return Guarantee(loan, b, gu, Decimal(amt))


# ---------- settings ----------
def test_default_settings_are_valid():
    assert S.validate() == []


def test_invalid_settings_rejected(make_member):
    bad = RiskSettings(version="x", weights={"member": 50, "geography": 15, "product": 10, "channel": 15, "transactions": 30})
    assert "add up to 120" in bad.validate()[0]
    with pytest.raises(ValueError):
        assess(make_member(), RiskInputs(), bad, as_of=TODAY)
    inverted = RiskSettings(version="y", review_months={"low": 12, "medium": 24, "high": 36})
    assert any("at least as often" in p for p in inverted.validate())


# ---------- scoring ----------
def test_ordinary_checkoff_teacher_is_low_with_simplified_dd(make_member):
    r = assess(make_member(), RiskInputs(), S, as_of=TODAY)
    # member 10, geo 0, product 10+20=30, channel 10, txn 10 -> (300+0+300+150+300)/100 = 10.5 -> 10
    assert r.points == {"member": 10, "geography": 0, "product": 30, "channel": 10, "transactions": 10}
    assert r.score == 10 and r.rating is Rating.LOW and r.due_diligence is DueDiligence.SIMPLIFIED
    assert r.next_review == date(2028, 6, 1) and r.settings_version == "2026-11-board-v1"


def test_cash_heavy_forex_dealer_far_above_income_is_high(make_member):
    m = make_member(occupation="Forex dealer", channel=Channel.AGENT, products=frozenset({"bosa_deposits", "fosa_account", "mobile_loans"}))
    r = assess(m, RiskInputs(expected_cash="high", credits_last_30_days=Decimal("4430000")), S, as_of=TODAY)
    assert r.points["transactions"] == 100 and r.base_rating is Rating.HIGH
    assert r.due_diligence is DueDiligence.ENHANCED
    assert r.next_review == date(2026, 6, 1)      # 12 months after last review: already overdue


def test_overrides_only_raise(make_member):
    pep = make_member(pep=PepStatus.DOMESTIC)
    r = assess(pep, RiskInputs(), S, as_of=TODAY)
    assert r.rating is Rating.HIGH and any("reg. 22" in o for o in r.overrides)
    r = assess(make_member(), RiskInputs(screening=ScreeningStatus.CONFIRMED_MATCH), S, as_of=TODAY)
    assert r.rating is Rating.PROHIBITED and r.due_diligence is DueDiligence.PROHIBITED and r.next_review == TODAY
    r = assess(make_member(), RiskInputs(screening=ScreeningStatus.POTENTIAL_MATCH), S, as_of=TODAY)
    assert r.rating is Rating.HIGH
    r = assess(make_member(kyc_complete=False), RiskInputs(), S, as_of=TODAY)
    assert r.rating is Rating.MEDIUM and r.due_diligence is DueDiligence.STANDARD


def test_guarantor_flags_raise_rating(make_member):
    high = (Flag("CIRCULAR", "high", "Circular guarantee: M1 → M2 → M1"),)
    med = (Flag("CONCENTRATION", "medium", "Guarantees 6 loans (limit 4)"),)
    assert assess(make_member(), RiskInputs(guarantor_flags=high), S, as_of=TODAY).rating is Rating.HIGH
    r = assess(make_member(), RiskInputs(guarantor_flags=med), S, as_of=TODAY)
    assert r.rating is Rating.MEDIUM and r.due_diligence is DueDiligence.STANDARD


def test_group_without_beneficial_owners_scores_higher(make_member):
    grp = make_member(member_type=MemberType.GROUP, name="Upendo Women Group")
    r = assess(grp, RiskInputs(), S, as_of=TODAY)
    assert r.points["member"] == 50 and "No beneficial owners recorded" in r.reasons


def test_foreign_and_high_risk_jurisdictions(make_member):
    assert assess(make_member(residence="United Kingdom"), RiskInputs(), S, as_of=TODAY).points["geography"] == 35
    assert assess(make_member(nationality="Iran"), RiskInputs(), S, as_of=TODAY).points["geography"] == 100


def test_add_months_handles_month_ends():
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2024, 2, 29), 12) == date(2025, 2, 28)
    assert add_months(date(2025, 11, 30), 3) == date(2026, 2, 28)


# ---------- guarantors ----------
def test_cycles_found_once_including_longer_loops():
    gs = [g("L1", "M2", "M1"), g("L2", "M1", "M2"), g("L3", "M4", "M3"), g("L4", "M5", "M4"), g("L5", "M3", "M5"),
          g("L6", "M9", "M8")]
    assert find_cycles(gs) == [("M1", "M2"), ("M3", "M4", "M5")]


def test_network_flags(make_member):
    members = {n: make_member(no=n) for n in ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8"]}
    members["M7"].status = MemberStatus.EXITED
    members["M8"].status = MemberStatus.BLOCKED
    gs = [g(f"L{i}", f"M{i}", "M6") for i in range(1, 6)]         # M6 guarantees 5 loans
    gs += [g("L10", "M1", "M7"), g("L11", "M2", "M8"), g("L12", "M3", "M3")]
    prof = analyse(gs, members, max_loans_per_guarantor=4)
    assert [f.code for f in prof["M6"].flags] == ["CONCENTRATION"]
    assert prof["M6"].total_guaranteed == Decimal(500000) and prof["M6"].distinct_borrowers == 5
    assert "EXITED" in [f.code for f in prof["M7"].flags]
    assert "BLOCKED" in [f.code for f in prof["M8"].flags]
    assert "LINKED_TO_BLOCKED" in [f.code for f in prof["M2"].flags]       # borrower guaranteed by blocked member
    assert "SELF_GUARANTEE" in [f.code for f in prof["M3"].flags]
