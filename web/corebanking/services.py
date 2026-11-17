"""Importing core banking files, and the guarantor network.

Imports are all or nothing: the file is read and every row checked first; if any
row has a problem the whole file is rejected (and recorded, with the row errors),
so the database never holds half a file. Re-importing the same records is safe:
unchanged rows are skipped, so a SACCO can send overlapping extracts.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import OuterRef, Subquery, Sum
from django.utils import timezone

from accounts.security import cipher
from audittrail.services import record
from compliance_core import guarantors as G
from compliance_core import models as core
from members.models import Member, RiskAssessment, Status

from . import parsing
from .models import Guarantee, ImportBatch, ImportKind, ImportProfile, Loan, NetworkFlag, Transaction

MEMBER_FIELDS = ("name", "member_type", "date_of_birth", "nationality", "residence", "occupation", "employer",
                 "checkoff", "declared_monthly_income", "source_of_funds", "channel", "products", "joined_on",
                 "last_review_on", "id_expiry", "kyc_complete")
COLUMN_FOR = {"declared_monthly_income": "monthly_income"}
SCREENED_FIELDS = {"name", "date_of_birth", "nationality"}


def _reject(*, kind, filename, sha, profile, user, errors, rows=0) -> ImportBatch:
    batch = ImportBatch.objects.create(kind=kind, source=filename, sha256=sha, profile=profile,
                                       status=ImportBatch.Status.REJECTED, rows_total=rows, errors=errors,
                                       imported_by=user)
    record(user.username, "import.rejected", f"{kind} file", {"file": filename, "errors": len(errors), "first": errors[:3]})
    return batch


def import_file(*, data: bytes, filename: str, kind: str, user, profile: ImportProfile | None = None) -> ImportBatch:
    """Returns the batch; check batch.status. A rejected batch is kept with its errors."""
    if kind not in ImportKind.values:
        raise ValidationError("Unknown file type.")
    if profile is not None and profile.kind != kind:
        raise ValidationError(f"The profile '{profile.name}' is for {profile.get_kind_display().lower()} files.")
    sha = hashlib.sha256(data).hexdigest()
    pname = profile.name if profile else ""
    earlier = ImportBatch.objects.filter(sha256=sha, kind=kind, status=ImportBatch.Status.IMPORTED).first()
    if earlier:
        return _reject(kind=kind, filename=filename, sha=sha, profile=pname, user=user,
                       errors=[f"This exact file was already imported on {timezone.localtime(earlier.imported_at):%d %b %Y %H:%M}."])
    try:
        parsed = parsing.PARSERS[kind](data, profile.mapping if profile else None)
    except parsing.FileError as exc:
        return _reject(kind=kind, filename=filename, sha=sha, profile=pname, user=user, errors=[str(exc)])
    errors = list(parsed.errors) + _check_against_database(kind, parsed.rows)
    if errors:
        return _reject(kind=kind, filename=filename, sha=sha, profile=pname, user=user,
                       errors=errors[:parsing.MAX_ERRORS], rows=len(parsed.rows))
    with transaction.atomic():
        batch = ImportBatch.objects.create(kind=kind, source=filename, sha256=sha, profile=pname,
                                           status=ImportBatch.Status.IMPORTED, rows_total=len(parsed.rows),
                                           imported_by=user)
        counts, affected = APPLY[kind](parsed.rows, batch, user)
        for k, v in counts.items():
            setattr(batch, k, v)
        batch.save()
        record(user.username, "import.completed", f"{kind} file",
               {"file": filename, "sha256": sha, "rows": len(parsed.rows), **counts})
    _after_import(kind, affected, user.username)
    return batch


def _check_against_database(kind: str, rows: list[dict]) -> list[str]:
    errors: list[str] = []
    if kind == ImportKind.MEMBERS:
        existing = {m.member_no: m for m in Member.objects.filter(member_no__in=[r["member_no"] for r in rows])}
        bidx_of = {r["member_no"]: cipher().blind_index(r["id_number"]) for r in rows}
        owners = dict(Member.objects.filter(id_number_bidx__in=bidx_of.values()).values_list("id_number_bidx", "member_no"))
        for r in rows:
            bidx = bidx_of[r["member_no"]]
            owner = owners.get(bidx)
            if owner and owner != r["member_no"]:
                errors.append(f"Row {r['_line']}: this ID number already belongs to member {owner}")
            m = existing.get(r["member_no"])
            if m and m.id_number_bidx != bidx:
                errors.append(f"Row {r['_line']}: member {r['member_no']} has a different ID number on record. "
                              "ID numbers can't be changed by import; correct it in the member file.")
            if m and m.status == Status.EXITED and not r["exited_on"]:
                errors.append(f"Row {r['_line']}: member {r['member_no']} has exited; re-admission is done in the app")
    elif kind == ImportKind.LOANS:
        nos = {r["borrower_no"] for r in rows} | {g for r in rows for g in r["guarantors"]}
        known = set(Member.objects.filter(member_no__in=nos).values_list("member_no", flat=True))
        existing = dict(Loan.objects.filter(loan_no__in=[r["loan_no"] for r in rows])
                        .values_list("loan_no", "borrower__member_no"))
        for r in rows:
            for no in [r["borrower_no"], *r["guarantors"]]:
                if no not in known:
                    errors.append(f"Row {r['_line']}: member {no} is not on record; import members first")
            if r["loan_no"] in existing and existing[r["loan_no"]] != r["borrower_no"]:
                errors.append(f"Row {r['_line']}: loan {r['loan_no']} is recorded for member {existing[r['loan_no']]}")
    elif kind == ImportKind.TRANSACTIONS:
        known = set(Member.objects.filter(member_no__in={r["member_no"] for r in rows}).values_list("member_no", flat=True))
        existing = {t.txn_id: t for t in Transaction.objects.filter(txn_id__in=[r["txn_id"] for r in rows])
                    .select_related("member")}
        for r in rows:
            if r["member_no"] not in known:
                errors.append(f"Row {r['_line']}: member {r['member_no']} is not on record; import members first")
            t = existing.get(r["txn_id"])
            if t and (t.member.member_no, t.at, t.amount, t.direction, t.channel) != (
                    r["member_no"], r["at"], r["amount"], r["direction"], r["channel"]):
                errors.append(f"Row {r['_line']}: transaction {r['txn_id']} was imported before with different details")
    return errors


def _apply_members(rows, batch, user):
    from members.services import reassess
    from screening.services import current_entries, screen
    entries = current_entries()
    existing = {m.member_no: m for m in Member.objects.filter(member_no__in=[r["member_no"] for r in rows])}
    created = updated = unchanged = 0
    held = []
    for r in rows:
        m = existing.get(r["member_no"])
        present = r["_present"]
        if m is None:
            m = Member(member_no=r["member_no"], id_number_enc=cipher().encrypt(r["id_number"]),
                       id_number_bidx=cipher().blind_index(r["id_number"]), onboarded_by=None,
                       **{f: r[f] for f in MEMBER_FIELDS})
            m.status, m.exited_on = (Status.EXITED, r["exited_on"]) if r["exited_on"] else (Status.ACTIVE, None)
            m.save()
            created += 1
            record(user.username, "member.imported", m.member_no, {"batch": batch.pk})
            changed = set(MEMBER_FIELDS)
        else:
            changed = {f for f in MEMBER_FIELDS if COLUMN_FOR.get(f, f) in present and getattr(m, f) != r[f]}
            for f in changed:
                setattr(m, f, r[f])
            if r["exited_on"] and m.status != Status.EXITED:
                m.status, m.exited_on = Status.EXITED, r["exited_on"]
                changed.add("status")
            if not changed:
                unchanged += 1
                continue
            m.save()
            updated += 1
            record(user.username, "member.updated_by_import", m.member_no, {"batch": batch.pk, "fields": sorted(changed)})
        if m.status == Status.EXITED:
            continue
        hits = screen(m, user.username, entries) if changed & (SCREENED_FIELDS | {"member_type"}) else []
        reassess(m, user.username)
        if any(h.kind == "sanctions" for h in hits) and m.status in (Status.ACTIVE, Status.PENDING_APPROVAL):
            m.status = Status.SCREENING_HOLD
            m.save(update_fields=["status", "updated_at"])
            record(user.username, "member.screening_hold", m.member_no, {"hits": len(hits)})
            held.append(m.member_no)
    return {"created": created, "updated": updated, "unchanged": unchanged}, held


def _apply_loans(rows, batch, user):
    members = {m.member_no: m for m in Member.objects.filter(
        member_no__in={r["borrower_no"] for r in rows} | {g for r in rows for g in r["guarantors"]})}
    existing = {l.loan_no: l for l in Loan.objects.filter(loan_no__in=[r["loan_no"] for r in rows])}
    created = updated = unchanged = 0
    for r in rows:
        loan = existing.get(r["loan_no"])
        wanted = {members[no].pk: amt for no, amt in r["guarantors"].items()}
        if loan is None:
            loan = Loan.objects.create(loan_no=r["loan_no"], borrower=members[r["borrower_no"]], amount=r["amount"],
                                       disbursed_on=r["disbursed_on"], status=r["status"], batch=batch)
            Guarantee.objects.bulk_create([Guarantee(loan=loan, guarantor_id=g, amount=a) for g, a in wanted.items()])
            created += 1
            continue
        have = {g.guarantor_id: g.amount for g in loan.guarantees.all()}
        same = (loan.amount, loan.disbursed_on, loan.status) == (r["amount"], r["disbursed_on"], r["status"]) and have == wanted
        if same:
            unchanged += 1
            continue
        loan.amount, loan.disbursed_on, loan.status, loan.batch = r["amount"], r["disbursed_on"], r["status"], batch
        loan.save()
        # The file is the latest picture of the loan: guarantors not in it have been released.
        loan.guarantees.exclude(guarantor_id__in=wanted).delete()
        for g, a in wanted.items():
            Guarantee.objects.update_or_create(loan=loan, guarantor_id=g, defaults={"amount": a})
        updated += 1
    return {"created": created, "updated": updated, "unchanged": unchanged}, []


def _apply_transactions(rows, batch, user):
    members = dict(Member.objects.filter(member_no__in={r["member_no"] for r in rows}).values_list("member_no", "pk"))
    have = set(Transaction.objects.filter(txn_id__in=[r["txn_id"] for r in rows]).values_list("txn_id", flat=True))
    new = [Transaction(txn_id=r["txn_id"], member_id=members[r["member_no"]], at=r["at"], amount=r["amount"],
                       direction=r["direction"], channel=r["channel"], counterparty=r["counterparty"],
                       purpose=r["purpose"], batch=batch) for r in rows if r["txn_id"] not in have]
    Transaction.objects.bulk_create(new, batch_size=2000)
    affected = sorted({t.member_id for t in new})
    return {"created": len(new), "updated": 0, "unchanged": len(rows) - len(new)}, affected


APPLY = {ImportKind.MEMBERS: _apply_members, ImportKind.LOANS: _apply_loans, ImportKind.TRANSACTIONS: _apply_transactions}


def _after_import(kind: str, affected, actor: str) -> None:
    if kind == ImportKind.LOANS:
        refresh_network(actor)
    elif kind == ImportKind.TRANSACTIONS and affected:
        from monitoring.services import run_monitoring
        run_monitoring(actor, member_ids=affected)


# ---------------------------------------------------------------- guarantor network

def credits_last_30_days(member: Member) -> Decimal:
    since = timezone.now() - timedelta(days=30)
    total = (Transaction.objects.filter(member=member, direction="in", at__gte=since)
             .exclude(purpose="loan_disbursement").aggregate(t=Sum("amount"))["t"])
    return total or Decimal("0")


def flags_for(member: Member) -> tuple[G.Flag, ...]:
    return tuple(G.Flag(f.code, f.severity, f.text) for f in member.network_flags.all())


def _high_on_own_account(p: dict) -> bool:
    """High risk for reasons other than the guarantor network itself. Without this a
    member rated high only because of a circular guarantee would keep being flagged
    as a 'high-risk guarantor' after the circle was broken."""
    if p["r"] == "prohibited" or p["base"] in ("high", "prohibited"):
        return True
    ov = json.loads(p["ov"]) if isinstance(p["ov"], str) else (p["ov"] or [])
    own = [o for o in ov if not o.startswith(("Guarantor network", "Incomplete KYC"))]
    return p["r"] == "high" and bool(own)


def refresh_network(actor: str, *, max_loans_per_guarantor: int = 4) -> dict:
    """Recalculate guarantor flags for everyone (guarantees on active loans), store
    them, and re-assess the members whose flags changed."""
    from members.services import reassess
    rows = list(Guarantee.objects.filter(loan__status=Loan.Status.ACTIVE)
                .values_list("loan__loan_no", "loan__borrower__member_no", "guarantor__member_no", "amount"))
    guarantees = [core.Guarantee(loan, b, g, amt) for loan, b, g, amt in rows]
    nos = {g.borrower for g in guarantees} | {g.guarantor for g in guarantees}
    latest = RiskAssessment.objects.filter(member=OuterRef("pk")).order_by("-assessed_at", "-id")
    people = list(Member.objects.filter(member_no__in=nos)
                  .annotate(r=Subquery(latest.values("rating")[:1]), base=Subquery(latest.values("base_rating")[:1]),
                            ov=Subquery(latest.values("overrides")[:1]))
                  .values("pk", "member_no", "name", "member_type", "status", "pep", "r", "base", "ov"))
    from screening.models import Hit
    under_review = set(Hit.objects.filter(status=Hit.Status.TO_REVIEW, member__member_no__in=nos)
                       .values_list("member__member_no", flat=True))
    cores = {p["member_no"]: core.Member(p["member_no"], core.MemberType(p["member_type"]), p["name"], "",
                                         status=core.MemberStatus(p["status"]), pep=core.PepStatus(p["pep"]))
             for p in people}
    profiles = G.analyse(guarantees, cores, max_loans_per_guarantor=max_loans_per_guarantor,
                         high_risk_members={p["member_no"] for p in people if _high_on_own_account(p)},
                         screening_review_members=under_review)
    pk_of = {p["member_no"]: p["pk"] for p in people}
    new = {pk_of[no]: {(f.code, f.severity, f.text) for f in prof.flags} for no, prof in profiles.items() if prof.flags}
    old: dict[int, set] = {}
    for f in NetworkFlag.objects.all():
        old.setdefault(f.member_id, set()).add((f.code, f.severity, f.text))
    changed = [pk for pk in set(new) | set(old) if new.get(pk, set()) != old.get(pk, set())]
    now = timezone.now()
    with transaction.atomic():
        NetworkFlag.objects.all().delete()
        NetworkFlag.objects.bulk_create([NetworkFlag(member_id=pk, code=c, severity=s, text=t, computed_at=now)
                                         for pk, flags in new.items() for c, s, t in sorted(flags)])
        for m in Member.objects.filter(pk__in=changed).exclude(status=Status.EXITED):
            reassess(m, actor)
    cycles = sorted({f[2] for flags in new.values() for f in flags if f[0] == "CIRCULAR"})
    summary = {"guarantees": len(guarantees), "members_flagged": len(new), "members_changed": len(changed),
               "circular": len(cycles)}
    record(actor, "guarantors.recalculated", "guarantor network", summary)
    return summary
