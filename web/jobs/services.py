"""Scheduled jobs and the list of things due.

Jobs are started by the server's scheduler (cron or a systemd timer) through
`python manage.py runjobs <job>`; see docs/OPERATIONS.md. Each run is recorded as a
JobRun and in the audit trail. A job that is still running is not started twice.
"""
from __future__ import annotations

import logging
import shutil
import urllib.request
import zlib
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import connection
from django.db.models import Min, OuterRef, Subquery
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from accounts.security import db_role
from audittrail.services import record

from .models import JobRun

log = logging.getLogger(__name__)
SYSTEM_USERNAME = "system"


def system_user() -> User:
    """The account jobs act as. It can't log in (inactive, no usable password)."""
    user, made = User.objects.get_or_create(username=SYSTEM_USERNAME,
                                            defaults={"role": Role.ADMINISTRATOR, "is_active": False})
    if made:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


def run_job(name: str, fn, **kwargs) -> JobRun:
    lock = 7340100 + zlib.crc32(name.encode()) % 1000
    with db_role("system"):
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [lock])
            got = cur.fetchone()[0]
        if not got:
            return JobRun.objects.create(job=name, status=JobRun.Status.SKIPPED, finished_at=timezone.now())
        run = JobRun.objects.create(job=name)
        try:
            summary = fn(**kwargs) or {}
            run.status = JobRun.Status.NOTHING_TO_DO if summary.pop("nothing_to_do", False) else JobRun.Status.SUCCEEDED
            run.summary = summary
        except Exception as exc:          # recorded and shown on the Jobs page; the next job still runs
            log.exception("Job %s failed", name)
            run.status = JobRun.Status.FAILED
            run.error = "; ".join(exc.messages) if isinstance(exc, ValidationError) else f"{type(exc).__name__}: {exc}"
        finally:
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [lock])
        run.finished_at = timezone.now()
        run.save()
        record(SYSTEM_USERNAME, f"job.{run.status}", name, {**run.summary, **({"error": run.error} if run.error else {})})
        return run


# ---------------------------------------------------------------- jobs

def fetch_un_list(source: str | None = None) -> dict:
    """Download the UN Security Council consolidated list; load it only if it changed."""
    import hashlib

    from screening.models import Hit, ListVersion
    from screening.services import load_list
    src = source or settings.UN_LIST_URL
    limit = settings.LIST_UPLOAD_MAX_BYTES
    if "://" in src:
        with urllib.request.urlopen(src, timeout=120) as resp:      # noqa: S310 (URL is a setting, not user input)
            data = resp.read(limit + 1)
    else:
        data = Path(src).read_bytes()[: limit + 1]
    if len(data) > limit:
        raise ValidationError("The downloaded list is larger than the size limit; nothing was loaded.")
    sha = hashlib.sha256(data).hexdigest()
    current = ListVersion.objects.filter(list_name=settings.UN_LIST_NAME, status=ListVersion.Status.CURRENT).first()
    if current and current.sha256 == sha:
        return {"nothing_to_do": True, "result": "List unchanged", "entries": current.entry_count}
    v = load_list(data=data, filename=src, list_name=settings.UN_LIST_NAME, kind="sanctions", fmt="un_xml",
                  user=system_user())
    return {"result": "New version loaded", "entries": v.entry_count,
            "matches_to_review": Hit.objects.filter(status=Hit.Status.TO_REVIEW).count()}


def import_inbox() -> dict:
    """Import files the core banking system dropped in IMPORT_INBOX:
    <inbox>/members/*.csv, <inbox>/loans/*.csv, <inbox>/transactions/*.csv in the
    standard layouts, or <inbox>/<kind>/<profile name>/*.csv using that saved profile.
    Files move to processed/<date>/ or rejected/<date>/ afterwards."""
    from corebanking.models import ImportBatch, ImportProfile
    from corebanking.services import import_file
    inbox = Path(settings.IMPORT_INBOX) if settings.IMPORT_INBOX else None
    if not inbox or not inbox.is_dir():
        return {"nothing_to_do": True, "result": "No import folder set up"}
    stamp = timezone.localdate().isoformat()
    results = []
    for kind in ("members", "loans", "transactions"):          # members first, so loans and transactions find them
        folder = inbox / kind
        if not folder.is_dir():
            continue
        groups = [(None, folder)] + [(p.name, p) for p in sorted(folder.iterdir()) if p.is_dir()]
        for profile_name, where in groups:
            profile = ImportProfile.objects.filter(name=profile_name, kind=kind).first() if profile_name else None
            for f in sorted(where.glob("*.csv")):
                if profile_name and profile is None:
                    status, note = "rejected", f"No saved profile called '{profile_name}'"
                else:
                    batch = import_file(data=f.read_bytes(), filename=f.name, kind=kind, user=system_user(), profile=profile)
                    status = "processed" if batch.status == ImportBatch.Status.IMPORTED else "rejected"
                    note = f"{batch.created} new, {batch.updated} updated" if status == "processed" else batch.errors[0]
                dest = inbox / status / stamp
                dest.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), dest / f.name)
                results.append({"file": f"{kind}/{f.name}", "result": status, "note": note})
    if not results:
        return {"nothing_to_do": True, "result": "No new files"}
    return {"files": results, "rejected": sum(r["result"] == "rejected" for r in results)}


def refresh_guarantors() -> dict:
    from corebanking.services import refresh_network
    return refresh_network(SYSTEM_USERNAME)


def run_monitoring() -> dict:
    from monitoring.services import run_monitoring as run
    return run(SYSTEM_USERNAME)


def send_reminders() -> dict:
    items = [i for i in due_items() if i["level"] in ("overdue", "due")]
    to = list(User.objects.filter(role=Role.COMPLIANCE_OFFICER, is_active=True).exclude(email="")
              .values_list("email", flat=True))
    if not items:
        return {"nothing_to_do": True, "result": "Nothing due"}
    if not to:
        return {"result": "Nothing sent: no compliance officer has an email address", "items": len(items)}
    # Counts and dates only: email leaves the system, so no names or case details go in it.
    body = "\n".join(f"- {i['text']}" for i in items)
    send_mail(f"Compliance Desk: {len(items)} item(s) need attention",
              f"{settings.INSTITUTION_NAME}\n\n{body}\n\nLog in to Compliance Desk for details.",
              settings.DEFAULT_FROM_EMAIL, to)
    return {"items": len(items), "sent_to": len(to)}


JOBS = {
    "fetch_un_list": fetch_un_list,
    "import_inbox": import_inbox,
    "refresh_guarantors": refresh_guarantors,
    "run_monitoring": run_monitoring,
    "send_reminders": send_reminders,
}
NIGHTLY = ("import_inbox", "refresh_guarantors", "run_monitoring", "send_reminders")


# ---------------------------------------------------------------- what is due

def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def due_items(today=None) -> list[dict]:
    """Things the compliance officer must act on, most urgent first. Shown on the
    overview page and sent as the daily reminder."""
    from cases.models import Case
    from members.models import Member, RiskAssessment, Status
    from monitoring.models import Alert
    from monitoring.services import pending_weeks
    from screening.models import Hit, ListVersion
    today = today or timezone.localdate()
    now = timezone.now()
    items: list[dict] = []

    def add(level, text, url):
        items.append({"level": level, "text": text, "url": url})

    with db_role("system"):
        open_cases = list(Case.objects.filter(status=Case.Status.OPEN).values_list("str_due_at", flat=True))
        alerts = Alert.objects.filter(status=Alert.Status.TO_REVIEW)
        n_alerts, oldest = alerts.count(), alerts.aggregate(o=Min("raised_at"))["o"]
        weeks = pending_weeks()
    late = sum(d < now for d in open_cases)
    soon = sum(now <= d < now + timedelta(hours=24) for d in open_cases)
    if late:
        add("overdue", f"{_plural(late, 'case')} past the 2-day STR deadline", reverse("cases:list"))
    if soon:
        add("due", f"{_plural(soon, 'case')} with the STR deadline in the next 24 hours", reverse("cases:list"))
    for w in weeks:
        if w["period_end"] <= today:
            add("overdue" if w["overdue"] else "due",
                f"Large cash report for the week ending {w['period_end']:%d %b %Y} ({_plural(len(w['alerts']), 'transaction')})"
                + (" is overdue" if w["overdue"] else " is due today"), reverse("monitoring:cash_reports"))
    if n_alerts:
        age = (now - oldest).days
        add("due" if age >= 1 else "info", f"{_plural(n_alerts, 'monitoring alert')} to review"
            + (f", oldest {_plural(age, 'day')}" if age else ""), reverse("monitoring:alerts"))
    hits = Hit.objects.filter(status=Hit.Status.TO_REVIEW).count()
    if hits:
        add("due", f"{_plural(hits, 'screening match', 'screening matches')} waiting for a decision", reverse("screening:queue"))

    latest = RiskAssessment.objects.filter(member=OuterRef("pk")).order_by("-assessed_at", "-id")
    active = Member.objects.exclude(status=Status.EXITED).annotate(nr=Subquery(latest.values("next_review_on")[:1]))
    overdue_reviews = active.filter(nr__lt=today).count()
    soon_reviews = active.filter(nr__gte=today, nr__lte=today + timedelta(days=30)).count()
    if overdue_reviews:
        add("overdue", f"{_plural(overdue_reviews, 'member review')} overdue", reverse("members:list") + "?due=overdue")
    if soon_reviews:
        add("info", f"{_plural(soon_reviews, 'member review')} due in the next 30 days", reverse("members:list") + "?due=soon")
    expired = active.filter(id_expiry__lt=today).count()
    expiring = active.filter(id_expiry__gte=today, id_expiry__lte=today + timedelta(days=60)).count()
    if expired:
        add("due", f"{_plural(expired, 'member')} with an expired ID document", reverse("members:list") + "?id=expired")
    if expiring:
        add("info", f"{_plural(expiring, 'member')} with an ID expiring in the next 60 days", reverse("members:list") + "?id=expiring")

    checked = JobRun.objects.filter(job="fetch_un_list", status__in=[JobRun.Status.SUCCEEDED, JobRun.Status.NOTHING_TO_DO]
                                    ).values_list("finished_at", flat=True).first()
    uploaded = ListVersion.objects.filter(list_name=settings.UN_LIST_NAME, status=ListVersion.Status.CURRENT
                                          ).values_list("loaded_at", flat=True).first()
    last = max([d for d in (checked, uploaded) if d], default=None)
    if last is None:
        add("overdue", "The UN sanctions list has never been loaded: members are not being screened", reverse("screening:lists"))
    elif now - last > timedelta(days=2):
        add("due", f"The UN sanctions list was last checked {_plural((now - last).days, 'day')} ago", reverse("jobs:list"))
    for job in JOBS:
        r = JobRun.objects.filter(job=job).exclude(status=JobRun.Status.SKIPPED).first()
        if r and r.status == JobRun.Status.FAILED:
            add("due", f"The last '{job.replace('_', ' ')}' job failed", reverse("jobs:list"))
    order = {"overdue": 0, "due": 1, "info": 2}
    return sorted(items, key=lambda i: order[i["level"]])
