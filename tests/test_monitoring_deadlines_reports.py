from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from compliance_core import audit
from compliance_core.crypto import FieldCipher
from compliance_core.deadlines import cash_report_due, id_status, may_destroy, retention_until, str_clock, str_due
from compliance_core.models import Direction, Rating, Transaction, TxnChannel
from compliance_core.monitoring import MonitoringSettings, new_alerts, run_rules
from compliance_core.reports import board_summary, cash_report_csv, str_report_xml

EAT = timezone(timedelta(hours=3))
NOW = datetime(2026, 11, 16, 10, 0, tzinfo=EAT)
M = MonitoringSettings()          # threshold 15,000 x 129 = KES 1,935,000


def tx(i, days_ago, amt, direction="in", channel="cash", counterparty=None, purpose="deposit", member="M0001"):
    return Transaction(f"T{i}", member, NOW - timedelta(days=days_ago), Decimal(amt), Direction(direction),
                       TxnChannel(channel), counterparty, purpose)


def rules(member, txns):
    return {a.rule: a for a in run_rules(member, txns, M, as_of=NOW)}


# ---------- monitoring ----------
def test_threshold_in_kes():
    assert M.large_cash_kes == Decimal("1935000")


def test_large_cash_is_reportable_not_suspicious(make_member):
    a = rules(make_member(declared_monthly_income=Decimal("2000000")), [tx(1, 1, 1935000)])
    assert set(a) == {"LARGE_CASH"} and a["LARGE_CASH"].reportable_only


def test_just_below_threshold_not_large_cash(make_member):
    assert "LARGE_CASH" not in rules(make_member(declared_monthly_income=Decimal("2000000")), [tx(1, 1, 1934999)])


def test_structuring(make_member):
    txns = [tx(1, 6, 1500000), tx(2, 4, 1800000), tx(3, 2, 1900000), tx(4, 40, 1600000)]
    a = rules(make_member(declared_monthly_income=Decimal("5000000")), txns)
    assert a["STRUCTURING"].txn_ids == ("T1", "T2", "T3")
    # two deposits only: no alert
    assert "STRUCTURING" not in rules(make_member(declared_monthly_income=Decimal("5000000")), txns[:2])


def test_income_mismatch_ignores_loan_disbursements(make_member):
    m = make_member()     # KES 60,000 a month
    assert "INCOME_MISMATCH" in rules(m, [tx(1, 3, 100000, channel="mobile_money"), tx(2, 5, 90000, channel="mobile_money")])
    assert "INCOME_MISMATCH" not in rules(m, [tx(1, 3, 500000, channel="internal", purpose="loan_disbursement")])


def test_rapid_in_out(make_member):
    m = make_member(declared_monthly_income=Decimal("5000000"))
    txns = [tx(1, 5, 800000, channel="bank_transfer"), Transaction("T2", "M0001", NOW - timedelta(days=4), Decimal(700000),
            Direction.OUT, TxnChannel.MOBILE_MONEY)]
    assert rules(m, txns)["RAPID_IN_OUT"].txn_ids == ("T1", "T2")


def test_dormant_reactivation(make_member):
    m = make_member(declared_monthly_income=Decimal("5000000"))
    assert "DORMANT_REACTIVATION" in rules(m, [tx(1, 300, 5000, channel="mobile_money"), tx(2, 2, 250000, channel="bank_transfer")])


def test_many_payers(make_member):
    m = make_member(declared_monthly_income=Decimal("5000000"))
    txns = [tx(i, i, 2000, channel="mobile_money", counterparty=f"07000000{i}", purpose="loan_repayment") for i in range(1, 6)]
    assert "MANY_PAYERS" in rules(m, txns)
    assert "MANY_PAYERS" not in rules(m, txns[:4])


def test_other_members_transactions_ignored_and_dedup(make_member):
    txns = [tx(1, 1, 3000000, member="M0099")]
    assert rules(make_member(), txns) == {}
    found = run_rules(make_member(declared_monthly_income=Decimal("9000000")), [tx(1, 1, 2000000)], M, as_of=NOW)
    assert new_alerts(found, {a.key for a in found}) == []


# ---------- deadlines ----------
def test_str_clock():
    formed = NOW - timedelta(hours=30)
    c = str_clock(formed, NOW)
    assert c.due == formed + timedelta(days=2) and c.status == "due soon" and c.hours_left == 18.0
    assert str_clock(NOW - timedelta(hours=50), NOW).status == "overdue"
    friday = datetime(2026, 11, 20, 9, 0, tzinfo=EAT)
    assert str_due(friday, business_days=True).date() == date(2026, 11, 24)


@pytest.mark.parametrize("d,due", [(date(2026, 11, 16), date(2026, 11, 20)), (date(2026, 11, 20), date(2026, 11, 20)),
                                   (date(2026, 11, 21), date(2026, 11, 27))])
def test_cash_report_due(d, due):
    assert cash_report_due(d) == due


def test_retention():
    assert retention_until(None, date(2026, 1, 1)) is None
    until = retention_until(date(2026, 2, 28), date(2026, 3, 15))
    assert until == date(2033, 3, 15)
    assert not may_destroy(until, date(2033, 3, 15)) and may_destroy(until, date(2033, 3, 16))
    assert not may_destroy(until, date(2040, 1, 1), legal_hold=True)
    assert id_status(date(2026, 12, 1), date(2026, 11, 16)) == "expiring soon"
    assert id_status(date(2026, 11, 1), date(2026, 11, 16)) == "expired"


# ---------- reports ----------
def test_cash_report_and_str_xml(make_member):
    m = make_member(name="Kevin <Mwangi> & Sons")
    txns = [tx(1, 1, 2000000), tx(2, 1, 100000), tx(3, 20, 2500000)]
    csv_text = cash_report_csv(txns, {m.member_no: m}, period_start=date(2026, 11, 14), period_end=date(2026, 11, 20),
                               threshold_kes=M.large_cash_kes)
    assert csv_text.count("\n") == 2 and "T1" in csv_text and "T3" not in csv_text
    xml = str_report_xml(reporting_entity_id="SACCO-001", case_ref="C-7", member=m, reason="Cash far above income",
                         action_taken="Account monitored", txns=txns[:1], submitted_at=NOW)
    assert "&lt;Mwangi&gt; &amp; Sons" in xml and "<report_code>STR</report_code>" in xml
    import xml.etree.ElementTree as ET
    ET.fromstring(xml.split("-->", 1)[1])          # well-formed


def test_board_summary():
    s = board_summary(period="Q4 2026", ratings={"a": Rating.LOW, "b": Rating.LOW, "c": Rating.HIGH}, peps=1,
                      reviews_completed=40, reviews_overdue=3, hits_raised=5, hits_confirmed=1,
                      alert_rules=["STRUCTURING", "LARGE_CASH", "LARGE_CASH"], strs_filed=2, strs_late=0,
                      cash_reports=13, staff_trained=18, staff_total=20)
    assert s.members_by_rating == {"low": 2, "medium": 0, "high": 1, "prohibited": 0}
    assert "LARGE_CASH 2" in s.to_text() and "(90%)" in s.to_text()


# ---------- audit chain ----------
def test_audit_chain_detects_tampering():
    chain = []
    for i in range(4):
        audit.append(chain, at=NOW + timedelta(minutes=i), actor="u1", action="member.updated", subject=f"M{i}", detail={"i": i})
    assert audit.verify(chain) is None
    forged = list(chain)
    forged[1] = audit.AuditEntry(**{**audit.to_dict(chain[1]), "actor": "someone else"})
    assert audit.verify(forged) == 2
    assert audit.verify(chain[:1] + chain[2:]) == 3          # deleted entry
    with pytest.raises(ValueError):
        audit.append([], at=datetime(2026, 1, 1), actor="u", action="a", subject="s")


# ---------- encryption ----------
def test_field_cipher_round_trip_rotation_and_blind_index():
    k1, k2 = FieldCipher.new_key(), FieldCipher.new_key()
    idx = "i" * 32
    old = FieldCipher([k1], idx)
    token = old.encrypt("23456789")
    assert "23456789" not in token and old.decrypt(token) == "23456789"
    new = FieldCipher([k2, k1], idx)
    rotated = new.rotate(token)
    assert FieldCipher([k2], idx).decrypt(rotated) == "23456789"
    with pytest.raises(ValueError):
        FieldCipher([k2], idx).decrypt(token)
    assert old.blind_index("2345 6789") == old.blind_index("23456789")
    assert old.blind_index("23456789") != FieldCipher([k1], "j" * 32).blind_index("23456789")


def test_file_encryption_round_trip_and_rotation():
    idx = "x" * 40
    k1, k2 = FieldCipher.new_key(), FieldCipher.new_key()
    data = b"%PDF-1.7 scanned national ID" * 100
    token = FieldCipher([k1], idx).encrypt_bytes(data)
    assert data not in token
    assert FieldCipher([k2, k1], idx).decrypt_bytes(token) == data       # old key still reads after rotation
    with pytest.raises(ValueError):
        FieldCipher([k2], idx).decrypt_bytes(token)
