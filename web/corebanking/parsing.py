"""Reading core banking CSV files into checked rows.

Each kind of file has a standard layout (LAYOUTS). A SACCO whose system names
columns differently saves an import profile mapping its names to ours. Reading
never touches the database: it returns clean rows and a list of row errors, and
the import is all or nothing, so a file with any error changes nothing.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from django.utils import timezone

# column: (required, description shown on the import page)
LAYOUTS: dict[str, dict[str, tuple[bool, str]]] = {
    "members": {
        "member_no": (True, "Member number in the core banking system"),
        "name": (True, "Full name, or registered name for a group or company"),
        "id_number": (True, "National ID, passport or registration number"),
        "member_type": (False, "individual, group or corporate (default individual)"),
        "date_of_birth": (False, "Date"),
        "nationality": (False, "Default Kenya"),
        "residence": (False, "Default Kenya"),
        "occupation": (False, ""),
        "employer": (False, ""),
        "checkoff": (False, "yes or no"),
        "monthly_income": (False, "KES"),
        "source_of_funds": (False, ""),
        "channel": (False, "branch, agent, mobile_app or online (default branch)"),
        "products": (False, "Separated by semicolons, e.g. bosa_deposits;fosa_account"),
        "joined_on": (False, "Date"),
        "last_review_on": (False, "Date of the last KYC review (default: joined date)"),
        "id_expiry": (False, "Date"),
        "kyc_complete": (False, "yes or no. If left out, the KYC file is treated as incomplete until checked"),
        "exited_on": (False, "Date the member left, if they have"),
    },
    "loans": {
        "loan_no": (True, "Loan number; one row per guarantor, repeat the loan details"),
        "borrower_no": (True, "Borrower's member number"),
        "loan_amount": (True, "KES"),
        "disbursed_on": (False, "Date"),
        "loan_status": (False, "active or closed (default active)"),
        "guarantor_no": (False, "Guarantor's member number; leave empty for a loan without guarantors"),
        "guaranteed_amount": (False, "KES"),
    },
    "transactions": {
        "txn_id": (True, "Unique transaction reference"),
        "member_no": (True, ""),
        "date_time": (True, "Date and time, e.g. 2026-11-16 14:05 or 16/11/2026 14:05"),
        "amount": (True, "KES, positive"),
        "direction": (True, "in or out (credit/debit, cr/dr also accepted)"),
        "channel": (True, "cash, mobile_money, bank_transfer, checkoff or internal"),
        "counterparty": (False, "Paying or receiving phone number or account"),
        "purpose": (False, "e.g. deposit, loan_repayment, withdrawal, loan_disbursement"),
    },
}

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y")      # day before month, as used in Kenya
TIME_FORMATS = ("%H:%M:%S", "%H:%M")
YES, NO = {"yes", "y", "true", "1"}, {"no", "n", "false", "0", ""}
DIRECTIONS = {"in": "in", "credit": "in", "cr": "in", "c": "in", "out": "out", "debit": "out", "dr": "out", "d": "out"}
CHANNELS = {"cash": "cash", "mobile_money": "mobile_money", "mobile money": "mobile_money", "mpesa": "mobile_money",
            "m-pesa": "mobile_money", "bank_transfer": "bank_transfer", "bank transfer": "bank_transfer",
            "eft": "bank_transfer", "rtgs": "bank_transfer", "checkoff": "checkoff", "check-off": "checkoff",
            "internal": "internal", "transfer": "internal"}
MEMBER_TYPES = {"individual", "group", "corporate"}
MEMBER_CHANNELS = {"branch", "agent", "mobile_app", "online"}
MAX_ERRORS = 200


class FileError(ValueError):
    """The file as a whole can't be read (wrong encoding, missing columns)."""


@dataclass
class Parsed:
    rows: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def error(self, line: int, text: str) -> None:
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(f"Row {line}: {text}")


def parse_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"'{value}' is not a date (use 2026-11-16 or 16/11/2026)")


def parse_datetime(value: str) -> datetime:
    value = value.strip().replace("T", " ")
    if value.endswith("Z") or "+" in value[10:]:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    day, _, clock = value.partition(" ")
    d = parse_date(day)
    if d is None:
        raise ValueError("date and time are required")
    t = time(0, 0)
    if clock.strip():
        for fmt in TIME_FORMATS:
            try:
                t = datetime.strptime(clock.strip(), fmt).time()
                break
            except ValueError:
                pass
        else:
            raise ValueError(f"'{clock.strip()}' is not a time (use 14:05)")
    return timezone.make_aware(datetime.combine(d, t))


def parse_amount(value: str, *, required: bool = True) -> Decimal | None:
    v = value.strip().replace(",", "").replace("KES", "").replace("Ksh", "").strip()
    if not v:
        if required:
            raise ValueError("amount is required")
        return None
    try:
        amount = Decimal(v)
    except InvalidOperation:
        raise ValueError(f"'{value.strip()}' is not an amount") from None
    if amount < 0:
        raise ValueError("amounts must not be negative; use the direction column for debits")
    return amount.quantize(Decimal("0.01"))


def parse_bool(value: str) -> bool:
    v = value.strip().lower()
    if v in YES:
        return True
    if v in NO:
        return False
    raise ValueError(f"'{value.strip()}' should be yes or no")


def read_csv(data: bytes, kind: str, mapping: dict[str, str] | None = None) -> tuple[list[tuple[int, dict]], list[str]]:
    """Rows as (line number, {our column: value}) and the columns found."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1252")        # files saved from Excel on Windows
        except UnicodeDecodeError as exc:
            raise FileError("The file must be a CSV saved as UTF-8") from exc
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise FileError("The file is empty") from None
    rename = {k.strip().lower(): v for k, v in (mapping or {}).items()}
    cols = [rename.get(h.strip().lower(), h.strip().lower()) for h in header]
    layout = LAYOUTS[kind]
    missing = [c for c, (req, _) in layout.items() if req and c not in cols]
    if missing:
        raise FileError(f"Missing column(s): {', '.join(missing)}. Columns found: {', '.join(cols) or 'none'}")
    rows = []
    for line, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        rows.append((line, {c: (values[i].strip() if i < len(values) else "") for i, c in enumerate(cols) if c in layout}))
    if not rows:
        raise FileError("The file has a header row but no data")
    return rows, cols


def members(data: bytes, mapping=None) -> Parsed:
    out = Parsed()
    raw, cols = read_csv(data, "members", mapping)
    seen_no, seen_id = {}, {}
    for line, r in raw:
        try:
            for c in ("member_no", "name", "id_number"):
                if not r.get(c):
                    raise ValueError(f"{c} is required")
            mtype = (r.get("member_type") or "individual").lower()
            if mtype not in MEMBER_TYPES:
                raise ValueError(f"member_type '{mtype}' should be individual, group or corporate")
            channel = (r.get("channel") or "branch").lower()
            if channel not in MEMBER_CHANNELS:
                raise ValueError(f"channel '{channel}' should be branch, agent, mobile_app or online")
            joined = parse_date(r.get("joined_on", "")) or timezone.localdate()
            row = {
                "member_no": r["member_no"], "name": " ".join(r["name"].split()), "id_number": r["id_number"],
                "member_type": mtype, "date_of_birth": parse_date(r.get("date_of_birth", "")),
                "nationality": r.get("nationality") or "Kenya", "residence": r.get("residence") or "Kenya",
                "occupation": r.get("occupation", ""), "employer": r.get("employer", ""),
                "checkoff": parse_bool(r.get("checkoff", "")),
                "declared_monthly_income": parse_amount(r.get("monthly_income", ""), required=False) or Decimal("0"),
                "source_of_funds": r.get("source_of_funds", ""), "channel": channel,
                "products": [p.strip() for p in r.get("products", "").split(";") if p.strip()] or ["bosa_deposits"],
                "joined_on": joined,
                "last_review_on": parse_date(r.get("last_review_on", "")) or joined,
                "id_expiry": parse_date(r.get("id_expiry", "")),
                "kyc_complete": parse_bool(r.get("kyc_complete", "")),
                "exited_on": parse_date(r.get("exited_on", "")),
            }
            # Only overwrite optional fields the file actually has, so a partial file
            # (say, just names and IDs) doesn't wipe what was recorded at onboarding.
            row["_present"] = {c for c in cols if c in LAYOUTS["members"]}
        except ValueError as exc:
            out.error(line, str(exc))
            continue
        for key, seen in (("member_no", seen_no), ("id_number", seen_id)):
            if row[key] in seen:
                out.error(line, f"{key} {row[key] if key == 'member_no' else '(hidden)'} also appears on row {seen[row[key]]}")
            seen[row[key]] = line
        row["_line"] = line
        out.rows.append(row)
    return out


def loans(data: bytes, mapping=None) -> Parsed:
    out = Parsed()
    raw, _ = read_csv(data, "loans", mapping)
    by_loan: dict[str, dict] = {}
    for line, r in raw:
        try:
            for c in ("loan_no", "borrower_no", "loan_amount"):
                if not r.get(c):
                    raise ValueError(f"{c} is required")
            amount = parse_amount(r["loan_amount"])
            if amount == 0:
                raise ValueError("loan_amount must be more than zero")
            status = (r.get("loan_status") or "active").lower()
            if status not in ("active", "closed"):
                raise ValueError(f"loan_status '{status}' should be active or closed")
            disbursed = parse_date(r.get("disbursed_on", ""))
            g_no = r.get("guarantor_no", "")
            g_amt = parse_amount(r.get("guaranteed_amount", ""), required=False) or Decimal("0")
        except ValueError as exc:
            out.error(line, str(exc))
            continue
        loan = by_loan.get(r["loan_no"])
        if loan is None:
            loan = by_loan[r["loan_no"]] = {"loan_no": r["loan_no"], "borrower_no": r["borrower_no"], "amount": amount,
                                            "disbursed_on": disbursed, "status": status, "guarantors": {}, "_line": line}
            out.rows.append(loan)
        elif (loan["borrower_no"], loan["amount"], loan["status"]) != (r["borrower_no"], amount, status):
            out.error(line, f"loan {r['loan_no']} has different borrower, amount or status from row {loan['_line']}")
            continue
        if g_no:
            if g_no in loan["guarantors"]:
                out.error(line, f"guarantor {g_no} is listed twice for loan {r['loan_no']}")
            loan["guarantors"][g_no] = g_amt
    return out


def transactions(data: bytes, mapping=None) -> Parsed:
    out = Parsed()
    raw, _ = read_csv(data, "transactions", mapping)
    seen = {}
    for line, r in raw:
        try:
            for c in ("txn_id", "member_no", "date_time", "amount", "direction", "channel"):
                if not r.get(c):
                    raise ValueError(f"{c} is required")
            direction = DIRECTIONS.get(r["direction"].lower())
            if not direction:
                raise ValueError(f"direction '{r['direction']}' should be in or out")
            channel = CHANNELS.get(r["channel"].lower())
            if not channel:
                raise ValueError(f"channel '{r['channel']}' should be cash, mobile_money, bank_transfer, checkoff or internal")
            amount = parse_amount(r["amount"])
            if amount == 0:
                raise ValueError("amount must be more than zero")
            row = {"txn_id": r["txn_id"], "member_no": r["member_no"], "at": parse_datetime(r["date_time"]),
                   "amount": amount, "direction": direction, "channel": channel,
                   "counterparty": r.get("counterparty", ""), "purpose": (r.get("purpose") or "").lower(), "_line": line}
        except ValueError as exc:
            out.error(line, str(exc))
            continue
        if row["txn_id"] in seen:
            out.error(line, f"txn_id {row['txn_id']} also appears on row {seen[row['txn_id']]}")
            continue
        seen[row["txn_id"]] = line
        out.rows.append(row)
    return out


PARSERS = {"members": members, "loans": loans, "transactions": transactions}


def parse_mapping(text: str, kind: str) -> dict[str, str]:
    """'Their column = our column', one per line."""
    mapping = {}
    for n, line in enumerate((text or "").splitlines(), start=1):
        if not line.strip():
            continue
        theirs, sep, ours = line.partition("=")
        ours = ours.strip().lower()
        if not sep or not theirs.strip() or not ours:
            raise ValueError(f"Line {n}: write it as 'Their column = our column'")
        if ours not in LAYOUTS[kind]:
            raise ValueError(f"Line {n}: '{ours}' is not one of our columns for this file type")
        mapping[theirs.strip()] = ours
    if not mapping:
        raise ValueError("Enter at least one column mapping")
    return mapping
