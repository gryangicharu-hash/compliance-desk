"""Keeping core banking in step with compliance holds and freezes."""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import os
import urllib.request
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from audittrail.services import record
from members.models import Member, Status

from .models import ENFORCED, CoreReport, CoreRestricted, Level, RestrictionChange, RestrictionExport, RestrictionState

log = logging.getLogger(__name__)
LEVEL_FOR = {Status.ACTIVE: Level.NONE, Status.PENDING_APPROVAL: Level.NO_NEW_BUSINESS,
             Status.SCREENING_HOLD: Level.HOLD, Status.BLOCKED: Level.FREEZE, Status.EXITED: Level.CLOSED}
REPORT_STALE = timedelta(days=2)
DELIVERY_GRACE = timedelta(minutes=30)
COALESCE_WITHIN = timedelta(seconds=60)


def desired(member: Member) -> str:
    return LEVEL_FOR.get(member.status, Level.HOLD)       # unknown status: hold, to be safe


def sync_member(member: Member) -> bool:
    """Bring the member's restriction in line with their status. True if it changed."""
    level = desired(member)
    state = RestrictionState.objects.filter(member_id=member.pk).first()
    if state is None:
        RestrictionState.objects.create(member_id=member.pk, level=level, since=timezone.now())
        if level == Level.NONE:
            return False
        previous = Level.NONE
    elif state.level == level:
        return False
    else:
        previous = state.level
        state.level, state.since = level, timezone.now()
        state.save(update_fields=["level", "since"])
    record("system", "restriction.changed", member.member_no, {"from": previous, "to": level})
    # A change not yet sent and only seconds old (e.g. 'awaiting approval' then 'hold' while
    # one member is onboarded) is replaced rather than followed, so core banking gets one
    # clear instruction. Anything already sent is never rewritten.
    last = (RestrictionChange.objects.filter(member_id=member.pk, delivered_at__isnull=True,
                                             created_at__gte=timezone.now() - COALESCE_WITHIN)
            .order_by("-created_at", "-id").first())
    if last is not None:
        if last.previous == level:
            last.delete()
        else:
            last.level = level
            last.save(update_fields=["level"])
        return True
    RestrictionChange.objects.create(member_id=member.pk, previous=previous, level=level)
    return True


def sync_all() -> int:
    return sum(sync_member(m) for m in Member.objects.only("pk", "member_no", "status"))


def _post(url: str, secret: str, payload: dict) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "X-Compliance-Desk-Signature": f"sha256={sig}",
        "X-Compliance-Desk-Delivery": str(payload["id"])})
    with urllib.request.urlopen(req, timeout=10) as resp:          # noqa: S310 (URL is a setting)
        if not 200 <= resp.status < 300:
            raise OSError(f"HTTP {resp.status}")


def deliver_pending(limit: int | None = None, quiet: bool = False) -> dict:
    """Send queued changes to the core banking system's web link, oldest first.
    Stops at the first failure so a member's changes never arrive out of order."""
    url, secret = settings.CORE_BANKING_WEBHOOK_URL, settings.CORE_BANKING_WEBHOOK_SECRET
    if not url:
        return {"nothing_to_do": True, "result": "No core banking web link set up; use the restrictions file"}
    sent = failed = 0
    pending = RestrictionChange.objects.filter(delivered_at__isnull=True).select_related("member")
    for ch in pending[:limit] if limit else pending:
        payload = {"event": "restriction.changed", "id": ch.pk, "member_no": ch.member.member_no,
                   "restriction": ch.level, "previous": ch.previous, "at": ch.created_at.isoformat()}
        ch.attempts += 1
        try:
            _post(url, secret, payload)
        except Exception as exc:          # network, TLS, HTTP error: retried by the next run
            ch.last_error = f"{type(exc).__name__}: {exc}"[:300]
            ch.save(update_fields=["attempts", "last_error"])
            failed += 1
            if not quiet:
                log.warning("Restriction delivery %s failed: %s", ch.pk, ch.last_error)
            break
        ch.delivered_at, ch.last_error = timezone.now(), ""
        ch.save(update_fields=["attempts", "delivered_at", "last_error"])
        sent += 1
    return {"sent": sent, "failed": failed,
            "waiting": RestrictionChange.objects.filter(delivered_at__isnull=True).count()}


# ---------------------------------------------------------------- the restrictions file

HEADER = ["member_no", "name", "restriction", "restriction_text", "since"]


def current_rows() -> list[list[str]]:
    states = (RestrictionState.objects.exclude(level__in=[Level.NONE, Level.CLOSED])
              .select_related("member").order_by("member__member_no"))
    return [[s.member.member_no, s.member.name, s.level, s.get_level_display(),
             timezone.localtime(s.since).strftime("%Y-%m-%d %H:%M")] for s in states]


def export_csv(actor: str, destination: str = "download") -> tuple[str, RestrictionExport]:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(HEADER)
    rows = current_rows()
    w.writerows(rows)
    text = out.getvalue()
    name = f"restrictions-{timezone.localtime():%Y%m%d-%H%M%S}.csv"
    exp = RestrictionExport.objects.create(created_by=actor, destination=destination, filename=name,
                                           sha256=hashlib.sha256(text.encode()).hexdigest(), rows=len(rows))
    record(actor, "restriction.exported", destination, {"file": name, "rows": len(rows), "sha256": exp.sha256})
    return text, exp


def write_outbox(actor: str) -> dict:
    if not settings.EXPORT_OUTBOX:
        return {"file": None}
    folder = Path(settings.EXPORT_OUTBOX)
    folder.mkdir(parents=True, exist_ok=True)
    text, exp = export_csv(actor, "outbox")
    tmp = folder / (exp.filename + ".partial")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, folder / exp.filename)          # appears complete or not at all
    (folder / (exp.filename + ".sha256")).write_text(f"{exp.sha256}  {exp.filename}\n")
    return {"file": exp.filename, "rows": exp.rows}


def run_job(actor: str = "system") -> dict:
    changed = sync_all()
    delivery = deliver_pending()
    outbox = write_outbox(actor)
    summary = {"changes_found": changed, **{k: v for k, v in delivery.items() if k != "nothing_to_do"},
               **({"outbox_file": outbox["file"], "rows": outbox["rows"]} if outbox["file"] else {})}
    if delivery.get("failed"):
        raise RuntimeError(f"Could not deliver restriction changes to core banking: "
                           f"{RestrictionChange.objects.filter(delivered_at__isnull=True).first().last_error}")
    return summary


# ---------------------------------------------------------------- confirmation from core banking

def record_report(rows: list[dict], batch, members: dict[str, int]) -> CoreReport:
    as_at = max((r["as_at"] for r in rows if r.get("as_at")), default=None) or timezone.now()
    report = CoreReport.objects.create(batch=batch, as_at=as_at)
    CoreRestricted.objects.bulk_create([CoreRestricted(report=report, member_id=members[r["member_no"]],
                                                       detail=r.get("detail", "")[:200]) for r in rows])
    return report


def reconcile() -> dict:
    report = CoreReport.objects.first()
    expected = {s.member_id: s for s in RestrictionState.objects.filter(level__in=ENFORCED).select_related("member")}
    if report is None:
        return {"report": None, "missing": [], "awaiting": list(expected.values()), "extra": [], "lift_not_applied": []}
    reported = {e.member_id: e for e in report.entries.select_related("member")}
    missing = [s for mid, s in expected.items() if mid not in reported and s.since <= report.as_at]
    awaiting = [s for mid, s in expected.items() if mid not in reported and s.since > report.as_at]
    lifted = dict(RestrictionState.objects.filter(member_id__in=[m for m in reported if m not in expected])
                  .values_list("member_id", "since"))
    lift_not_applied = [e for mid, e in reported.items() if mid in lifted and lifted[mid] <= report.as_at]
    extra = [e for mid, e in reported.items() if mid not in expected and e not in lift_not_applied]
    return {"report": report, "missing": missing, "awaiting": awaiting, "extra": extra,
            "lift_not_applied": lift_not_applied}


def member_restriction(member: Member):
    state = RestrictionState.objects.filter(member=member).first()
    if state is None or state.level not in ENFORCED:
        return state, ""
    report = CoreReport.objects.first()
    if report is None:
        return state, "not yet confirmed (no file from core banking received)"
    if report.entries.filter(member=member).exists():
        return state, f"confirmed restricted in core banking as at {timezone.localtime(report.as_at):%d %b %Y %H:%M}"
    if state.since > report.as_at:
        return state, "waiting for confirmation from core banking"
    return state, "NOT restricted in core banking at the last report"


def due() -> list[tuple[str, str]]:
    """(level, text) items for the overview's Needs attention list."""
    items = []
    r = reconcile()
    if r["missing"]:
        items.append(("overdue", f"{len(r['missing'])} member(s) held or frozen here are not restricted in core banking"))
    if r["lift_not_applied"]:
        items.append(("due", f"{len(r['lift_not_applied'])} member(s) released here are still restricted in core banking"))
    enforced = RestrictionState.objects.filter(level__in=ENFORCED).exists()
    if enforced and (r["report"] is None or timezone.now() - r["report"].received_at > REPORT_STALE):
        items.append(("due", "No restricted-accounts file from core banking in the last 2 days"))
    if settings.CORE_BANKING_WEBHOOK_URL and RestrictionChange.objects.filter(
            delivered_at__isnull=True, created_at__lt=timezone.now() - DELIVERY_GRACE).exists():
        items.append(("overdue", "Restriction changes have not reached core banking for over 30 minutes"))
    return items
