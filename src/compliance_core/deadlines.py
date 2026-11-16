"""Legal deadlines.

  STR: within 2 days of the suspicion arising (reg. 33). Counted in calendar
       days by default; set business_days=True if the SACCO's legal advice says
       working days. The conservative reading is calendar days.
  Large cash report: weekly, by Friday (reg. 35). ASSUMPTION to confirm with the
       FRC: a transaction is reported on the first Friday on or after its date.
  Records: kept at least 7 years after the transaction or the end of the
       relationship (reg. 37).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .risk import add_months

STR_DAYS = 2
RETENTION_YEARS = 7


def str_due(suspicion_formed_at: datetime, *, business_days: bool = False) -> datetime:
    if not business_days:
        return suspicion_formed_at + timedelta(days=STR_DAYS)
    d, left = suspicion_formed_at, STR_DAYS
    while left:
        d += timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    return d


@dataclass(frozen=True)
class ClockState:
    due: datetime
    hours_left: float
    status: str     # "on time", "due soon" (under 24h), "overdue"


def str_clock(suspicion_formed_at: datetime, now: datetime, *, business_days: bool = False) -> ClockState:
    due = str_due(suspicion_formed_at, business_days=business_days)
    hours = (due - now).total_seconds() / 3600
    status = "overdue" if hours < 0 else "due soon" if hours < 24 else "on time"
    return ClockState(due, round(hours, 1), status)


def cash_report_due(txn_date: date) -> date:
    return txn_date + timedelta(days=(4 - txn_date.weekday()) % 7)


def retention_until(relationship_ended: date | None, last_transaction: date | None) -> date | None:
    """Earliest date records may be destroyed; None while the relationship is open."""
    if relationship_ended is None:
        return None
    anchor = max(d for d in (relationship_ended, last_transaction) if d is not None)
    return add_months(anchor, 12 * RETENTION_YEARS)


def may_destroy(record_retention_until: date | None, today: date, *, legal_hold: bool = False) -> bool:
    return record_retention_until is not None and today > record_retention_until and not legal_hold


def id_status(expiry: date | None, today: date, warn_days: int = 60) -> str:
    if expiry is None:
        return "no expiry recorded"
    if expiry < today:
        return "expired"
    if (expiry - today).days <= warn_days:
        return "expiring soon"
    return "valid"
