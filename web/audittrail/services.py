import json

from django.db import connection, transaction
from django.utils import timezone

from compliance_core import audit as chain

from .models import AuditEvent

LOCK_ID = 7340001     # advisory lock serialising appends, so two requests can't take the same seq


def _entry(e: AuditEvent) -> chain.AuditEntry:
    return chain.AuditEntry(e.seq, e.at_text, e.actor, e.action, e.subject, e.detail, e.prev_hash, e.hash)


def record(actor: str, action: str, subject: str, detail: dict | None = None) -> AuditEvent:
    detail = json.loads(json.dumps(detail or {}, default=str))     # JSON-native, so the hash re-computes exactly
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", [LOCK_ID])
        last = AuditEvent.objects.order_by("-seq").first()
        entries = [_entry(last)] if last else []
        e = chain.append(entries, at=timezone.localtime(), actor=actor, action=action, subject=subject, detail=detail)
        return AuditEvent.objects.create(seq=e.seq, at=timezone.now(), at_text=e.at, actor=e.actor, action=e.action,
                                         subject=e.subject, detail=e.detail, prev_hash=e.prev_hash, hash=e.hash)


def verify_all() -> int | None:
    """None if the whole trail is intact, else the first sequence number that fails."""
    return chain.verify([_entry(e) for e in AuditEvent.objects.order_by("seq").iterator()])
