"""Transaction monitoring rules.

Each rule looks at one member's transactions and returns alerts. Alerts are
leads for the compliance officer, not conclusions: a suspicious transaction
report is only filed after review (reg. 33).

Rules (all thresholds are settings):
  LARGE_CASH            cash at or above USD 15,000 equivalent: goes on the weekly
                        cash report (reg. 35). Reportable, not suspicious by itself.
  STRUCTURING           several cash deposits just under the large-cash threshold
                        within a few days, together at or above it.
  INCOME_MISMATCH       credits in 30 days above a multiple of declared income.
  RAPID_IN_OUT          a large credit mostly withdrawn or sent out within 48 hours.
  DORMANT_REACTIVATION  a large credit after a long period with no activity.
  MANY_PAYERS           deposits or repayments from many different mobile money
                        numbers or accounts in 30 days.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .models import Direction, Member, Transaction, TxnChannel


@dataclass(frozen=True)
class MonitoringSettings:
    usd_rate: Decimal = Decimal("129.00")          # KES per USD, set by the administrator
    large_cash_usd: Decimal = Decimal("15000")
    structuring_floor_pct: Decimal = Decimal("0.70")
    structuring_min_count: int = 3
    structuring_window_days: int = 7
    income_multiple: Decimal = Decimal("3")
    rapid_window_hours: int = 48
    rapid_out_share: Decimal = Decimal("0.80")
    rapid_min_credit_kes: Decimal = Decimal("500000")
    dormant_days: int = 180
    dormant_min_credit_kes: Decimal = Decimal("200000")
    many_payers_count: int = 5
    many_payers_window_days: int = 30

    @property
    def large_cash_kes(self) -> Decimal:
        return (self.large_cash_usd * self.usd_rate).quantize(Decimal("1"))


@dataclass(frozen=True)
class Alert:
    rule: str
    member_no: str
    txn_ids: tuple[str, ...]
    description: str
    reportable_only: bool = False      # True for LARGE_CASH: report, no suspicion needed

    @property
    def key(self) -> tuple[str, str, tuple[str, ...]]:
        return (self.rule, self.member_no, self.txn_ids)


def _kes(x: Decimal) -> str:
    return f"KES {x:,.0f}"


def run_rules(member: Member, txns: list[Transaction], s: MonitoringSettings, *,
              as_of: datetime) -> list[Alert]:
    mine = sorted((t for t in txns if t.member_no == member.member_no), key=lambda t: t.timestamp)
    alerts: list[Alert] = []
    limit = s.large_cash_kes

    # LARGE_CASH
    for t in mine:
        if t.channel is TxnChannel.CASH and t.amount >= limit:
            alerts.append(Alert("LARGE_CASH", member.member_no, (t.txn_id,),
                                f"Cash {t.direction.value} of {_kes(t.amount)} (threshold {_kes(limit)})", True))

    # STRUCTURING
    floor = limit * s.structuring_floor_pct
    near = [t for t in mine if t.channel is TxnChannel.CASH and t.direction is Direction.IN and floor <= t.amount < limit]
    used: set[str] = set()
    for i, first in enumerate(near):
        if first.txn_id in used:
            continue
        window = [t for t in near[i:] if t.timestamp - first.timestamp <= timedelta(days=s.structuring_window_days)]
        total = sum((t.amount for t in window), Decimal("0"))
        if len(window) >= s.structuring_min_count and total >= limit:
            used.update(t.txn_id for t in window)
            alerts.append(Alert("STRUCTURING", member.member_no, tuple(t.txn_id for t in window),
                                f"{len(window)} cash deposits between {_kes(floor)} and {_kes(limit)} within "
                                f"{s.structuring_window_days} days, totalling {_kes(total)}"))

    # INCOME_MISMATCH (credits other than salary check-off in the last 30 days)
    if member.declared_monthly_income > 0:
        start = as_of - timedelta(days=30)
        credits = [t for t in mine if t.direction is Direction.IN and t.timestamp >= start and t.purpose != "loan_disbursement"]
        total = sum((t.amount for t in credits), Decimal("0"))
        if total > member.declared_monthly_income * s.income_multiple:
            alerts.append(Alert("INCOME_MISMATCH", member.member_no, tuple(t.txn_id for t in credits),
                                f"Credits of {_kes(total)} in 30 days against declared income of "
                                f"{_kes(member.declared_monthly_income)} a month"))

    # RAPID_IN_OUT
    for t in mine:
        if t.direction is Direction.IN and t.amount >= s.rapid_min_credit_kes and t.purpose != "loan_disbursement":
            end = t.timestamp + timedelta(hours=s.rapid_window_hours)
            outs = [o for o in mine if o.direction is Direction.OUT and t.timestamp < o.timestamp <= end]
            out_total = sum((o.amount for o in outs), Decimal("0"))
            if out_total >= t.amount * s.rapid_out_share:
                alerts.append(Alert("RAPID_IN_OUT", member.member_no, (t.txn_id,) + tuple(o.txn_id for o in outs),
                                    f"{_kes(t.amount)} received and {_kes(out_total)} paid out within "
                                    f"{s.rapid_window_hours} hours"))

    # DORMANT_REACTIVATION
    for prev, cur in zip(mine, mine[1:]):
        gap = (cur.timestamp - prev.timestamp).days
        if gap >= s.dormant_days and cur.direction is Direction.IN and cur.amount >= s.dormant_min_credit_kes:
            alerts.append(Alert("DORMANT_REACTIVATION", member.member_no, (cur.txn_id,),
                                f"{_kes(cur.amount)} received after {gap} days without activity"))

    # MANY_PAYERS
    start = as_of - timedelta(days=s.many_payers_window_days)
    payers = [t for t in mine if t.direction is Direction.IN and t.timestamp >= start
              and t.channel in (TxnChannel.MOBILE_MONEY, TxnChannel.BANK_TRANSFER) and t.counterparty]
    distinct = sorted({t.counterparty for t in payers})
    if len(distinct) >= s.many_payers_count:
        alerts.append(Alert("MANY_PAYERS", member.member_no, tuple(t.txn_id for t in payers),
                            f"Payments from {len(distinct)} different payers in {s.many_payers_window_days} days"))
    return alerts


def new_alerts(found: list[Alert], existing_keys: set) -> list[Alert]:
    return [a for a in found if a.key not in existing_keys]
