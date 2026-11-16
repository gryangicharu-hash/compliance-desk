"""Member risk assessment (POCAMLA Regulations, reg. 6 risk factors).

score = sum over factor groups of (points 0-100 x weight %) / 100
Ratings come from score bands, then override rules can only raise the rating:
  confirmed sanctions match       -> prohibited
  unresolved screening match      -> at least high
  open suspicious activity case   -> at least high
  PEP (reg. 22)                   -> at least high
  adverse media                   -> at least high
  high-severity guarantor flag    -> at least high
  medium guarantor flag           -> at least medium
  incomplete KYC                  -> at least medium
Simplified due diligence (reg. 18A) only for low risk with complete KYC, no PEP,
no screening match and no suspicion.

Every result records the settings version used, so a past rating can always be
explained on inspection.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .guarantors import Flag
from .models import Channel, DueDiligence, Member, MemberType, PepStatus, Rating, ScreeningStatus

FACTORS = ("member", "geography", "product", "channel", "transactions")


@dataclass(frozen=True)
class RiskSettings:
    version: str
    weights: dict[str, int] = field(default_factory=lambda: {
        "member": 30, "geography": 15, "product": 10, "channel": 15, "transactions": 30})
    medium_from: int = 30
    high_from: int = 50
    review_months: dict[str, int] = field(default_factory=lambda: {"low": 36, "medium": 24, "high": 12})
    high_risk_jurisdictions: frozenset[str] = frozenset({
        "Democratic People's Republic of Korea", "Iran", "Myanmar"})
    elevated_jurisdictions: frozenset[str] = frozenset()
    high_risk_occupations: frozenset[str] = frozenset({
        "real estate agent", "forex dealer", "car dealer", "precious metals and stones dealer",
        "mining or gold trade", "lawyer", "casino or betting", "money remittance"})
    product_points: dict[str, int] = field(default_factory=lambda: {
        "bosa_deposits": 10, "fosa_account": 20, "fixed_deposit": 25, "mobile_loans": 20,
        "development_loan": 5, "asset_finance": 10, "foreign_currency_account": 30})
    channel_points: dict[str, int] = field(default_factory=lambda: {
        "branch": 10, "agent": 30, "mobile_app": 60, "online": 60})
    cash_points: dict[str, int] = field(default_factory=lambda: {"low": 10, "medium": 45, "high": 85})
    income_multiple_alert: Decimal = Decimal("3")

    def validate(self) -> list[str]:
        problems = []
        if set(self.weights) != set(FACTORS):
            problems.append(f"Weights must cover exactly: {', '.join(FACTORS)}")
        if sum(self.weights.values()) != 100:
            problems.append(f"Weights add up to {sum(self.weights.values())}, not 100")
        if any(w < 0 for w in self.weights.values()):
            problems.append("Weights cannot be negative")
        if not 0 < self.medium_from < self.high_from <= 100:
            problems.append("Bands must satisfy 0 < medium_from < high_from <= 100")
        if any(self.review_months.get(k, 0) <= 0 for k in ("low", "medium", "high")):
            problems.append("Review cycles must be positive for low, medium and high")
        if not self.review_months["high"] <= self.review_months["medium"] <= self.review_months["low"]:
            problems.append("Higher-risk members must be reviewed at least as often as lower-risk ones")
        return problems


@dataclass(frozen=True)
class RiskInputs:
    """Facts that come from outside the member record."""
    screening: ScreeningStatus = ScreeningStatus.CLEAR
    open_case: bool = False
    guarantor_flags: tuple[Flag, ...] = ()
    expected_cash: str = "low"                 # declared at onboarding: low / medium / high
    credits_last_30_days: Decimal = Decimal("0")


@dataclass(frozen=True)
class RiskResult:
    score: int
    points: dict[str, int]
    base_rating: Rating
    rating: Rating
    reasons: tuple[str, ...]          # why points were given
    overrides: tuple[str, ...]        # rules that raised the rating
    due_diligence: DueDiligence
    next_review: date | None
    settings_version: str


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _points(member: Member, inputs: RiskInputs, s: RiskSettings) -> tuple[dict[str, int], list[str]]:
    reasons: list[str] = []
    mem = {MemberType.INDIVIDUAL: 10, MemberType.GROUP: 30, MemberType.CORPORATE: 40}[member.member_type]
    pep_pts = {PepStatus.NONE: 0, PepStatus.FAMILY_OR_ASSOCIATE: 60, PepStatus.DOMESTIC: 70,
               PepStatus.INTERNATIONAL_ORG: 70, PepStatus.FOREIGN: 100}[member.pep]
    if pep_pts:
        reasons.append(f"PEP ({member.pep.value.replace('_', ' ')})")
    mem += pep_pts
    if member.occupation.strip().lower() in s.high_risk_occupations:
        mem += 30
        reasons.append(f"Higher-risk occupation: {member.occupation}")
    if member.adverse_media:
        mem += 50
        reasons.append("Adverse media recorded")
    if member.member_type is not MemberType.INDIVIDUAL and not member.beneficial_owners:
        mem += 20
        reasons.append("No beneficial owners recorded")

    places = {member.nationality, member.residence} | {p.nationality for p in member.beneficial_owners if p.nationality}
    if places & s.high_risk_jurisdictions:
        geo = 100
        reasons.append("Link to a high-risk jurisdiction")
    elif places & s.elevated_jurisdictions:
        geo = 60
        reasons.append("Link to an elevated-risk jurisdiction")
    elif any(p and p != "Kenya" for p in places):
        geo = 35
        reasons.append("Foreign nationality or residence")
    else:
        geo = 0

    prod = sum(s.product_points.get(p, 10) for p in member.products)
    chan = s.channel_points.get(member.channel.value, 30)
    if not member.channel.face_to_face:
        reasons.append("Onboarded without meeting in person (reg. 7A)")

    txn = s.cash_points.get(inputs.expected_cash, 10)
    income = member.declared_monthly_income
    if income > 0 and inputs.credits_last_30_days > income * s.income_multiple_alert:
        txn += 30
        reasons.append(f"Credits in last 30 days exceed {s.income_multiple_alert}x declared income")
    pts = {"member": mem, "geography": geo, "product": prod, "channel": chan, "transactions": txn}
    return {k: min(100, v) for k, v in pts.items()}, reasons


def assess(member: Member, inputs: RiskInputs, settings: RiskSettings, *, as_of: date) -> RiskResult:
    problems = settings.validate()
    if problems:
        raise ValueError("Invalid risk settings: " + "; ".join(problems))
    pts, reasons = _points(member, inputs, settings)
    score = round(sum(pts[f] * settings.weights[f] for f in FACTORS) / 100)
    base = Rating.HIGH if score >= settings.high_from else Rating.MEDIUM if score >= settings.medium_from else Rating.LOW
    rating = base
    overrides: list[str] = []

    def raise_to(level: Rating, why: str) -> None:
        nonlocal rating
        overrides.append(why)
        rating = rating.at_least(level)

    if inputs.screening is ScreeningStatus.CONFIRMED_MATCH:
        raise_to(Rating.PROHIBITED, "Confirmed sanctions match: relationship prohibited, freeze and report")
    if inputs.screening is ScreeningStatus.POTENTIAL_MATCH:
        raise_to(Rating.HIGH, "Unresolved screening match: high risk until decided")
    if inputs.open_case:
        raise_to(Rating.HIGH, "Subject of an open suspicious activity case")
    if member.pep is not PepStatus.NONE:
        raise_to(Rating.HIGH, "PEP: enhanced due diligence and senior management approval (reg. 22)")
    if member.adverse_media:
        raise_to(Rating.HIGH, "Adverse media")
    high_flags = [f for f in inputs.guarantor_flags if f.severity == "high"]
    med_flags = [f for f in inputs.guarantor_flags if f.severity != "high"]
    if high_flags:
        raise_to(Rating.HIGH, "Guarantor network: " + "; ".join(sorted({f.text for f in high_flags})))
    elif med_flags:
        raise_to(Rating.MEDIUM, "Guarantor network: " + "; ".join(sorted({f.text for f in med_flags})))
    if not member.kyc_complete or not member.source_of_funds.strip():
        raise_to(Rating.MEDIUM, "Incomplete KYC file or source of funds missing")

    if rating is Rating.PROHIBITED:
        dd = DueDiligence.PROHIBITED
    elif rating is Rating.HIGH:
        dd = DueDiligence.ENHANCED
    elif (rating is Rating.LOW and member.kyc_complete and member.pep is PepStatus.NONE
          and inputs.screening in (ScreeningStatus.CLEAR, ScreeningStatus.CLEARED) and not inputs.open_case):
        dd = DueDiligence.SIMPLIFIED
    else:
        dd = DueDiligence.STANDARD

    nxt = None
    if member.last_review and rating is not Rating.PROHIBITED:
        nxt = add_months(member.last_review, settings.review_months[rating.value])
    elif member.last_review:
        nxt = as_of
    return RiskResult(score, pts, base, rating, tuple(reasons), tuple(overrides), dd, nxt, settings.version)
