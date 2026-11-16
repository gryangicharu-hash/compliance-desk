"""Domain objects shared by every engine module.

The engine is framework-free: the web layer loads these from the database,
calls the pure functions in the other modules, and stores the results.
Money is always Decimal in KES.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class MemberType(str, Enum):
    INDIVIDUAL = "individual"
    GROUP = "group"          # chama, self-help group, welfare group
    CORPORATE = "corporate"


class PepStatus(str, Enum):
    NONE = "none"
    DOMESTIC = "domestic"
    FOREIGN = "foreign"
    INTERNATIONAL_ORG = "international_organisation"
    FAMILY_OR_ASSOCIATE = "family_or_close_associate"


class Channel(str, Enum):
    BRANCH = "branch"
    AGENT = "agent"
    MOBILE_APP = "mobile_app"
    ONLINE = "online"

    @property
    def face_to_face(self) -> bool:
        return self in (Channel.BRANCH, Channel.AGENT)


class MemberStatus(str, Enum):
    ACTIVE = "active"
    PENDING_APPROVAL = "pending_approval"
    SCREENING_HOLD = "screening_hold"
    BLOCKED = "blocked"
    EXITED = "exited"


class Rating(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    PROHIBITED = "prohibited"

    @property
    def rank(self) -> int:
        return ["low", "medium", "high", "prohibited"].index(self.value)

    def at_least(self, other: "Rating") -> "Rating":
        return self if self.rank >= other.rank else other


class DueDiligence(str, Enum):
    SIMPLIFIED = "simplified"
    STANDARD = "standard"
    ENHANCED = "enhanced"
    PROHIBITED = "prohibited"


class ScreeningStatus(str, Enum):
    CLEAR = "clear"                    # no hits ever
    CLEARED = "cleared"                # hits, all decided as false positives
    POTENTIAL_MATCH = "potential_match"
    CONFIRMED_MATCH = "confirmed_match"


@dataclass(frozen=True)
class Person:
    """An official, signatory, director or beneficial owner of a group or company."""
    name: str
    role: str
    id_number: str | None = None
    date_of_birth: date | None = None
    nationality: str | None = None
    ownership_pct: Decimal | None = None


@dataclass
class Member:
    member_no: str
    member_type: MemberType
    name: str
    id_number: str
    nationality: str = "Kenya"
    residence: str = "Kenya"
    date_of_birth: date | None = None
    occupation: str = ""
    employer: str = ""
    checkoff: bool = False
    channel: Channel = Channel.BRANCH
    products: frozenset[str] = frozenset({"bosa_deposits"})
    pep: PepStatus = PepStatus.NONE
    pep_approved: bool = False
    declared_monthly_income: Decimal = Decimal("0")
    source_of_funds: str = ""
    kyc_complete: bool = True
    adverse_media: bool = False
    id_expiry: date | None = None
    joined: date | None = None
    last_review: date | None = None
    status: MemberStatus = MemberStatus.ACTIVE
    officials: list[Person] = field(default_factory=list)
    beneficial_owners: list[Person] = field(default_factory=list)


class Direction(str, Enum):
    IN = "in"
    OUT = "out"


class TxnChannel(str, Enum):
    CASH = "cash"
    MOBILE_MONEY = "mobile_money"
    BANK_TRANSFER = "bank_transfer"
    CHECKOFF = "checkoff"
    INTERNAL = "internal"


@dataclass(frozen=True)
class Transaction:
    txn_id: str
    member_no: str
    timestamp: datetime
    amount: Decimal              # always positive, KES
    direction: Direction
    channel: TxnChannel
    counterparty: str | None = None   # e.g. paying mobile number, bank account
    purpose: str = ""                 # "deposit", "loan_repayment", "withdrawal"


@dataclass(frozen=True)
class Guarantee:
    loan_id: str
    borrower: str     # member_no
    guarantor: str    # member_no
    amount: Decimal
