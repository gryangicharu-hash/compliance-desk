"""Tamper-evident audit trail.

Each entry stores the SHA-256 hash of its own content plus the previous entry's
hash. Changing, deleting or reordering any past entry breaks every later hash,
so verify() pinpoints where the record was altered. The database also blocks
UPDATE and DELETE on the audit table (see db/schema.sql); the hash chain is a
second, independent safeguard that an inspector can check.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime

GENESIS = "0" * 64


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    at: str                 # ISO 8601 with timezone
    actor: str              # user id, or "system"
    action: str             # e.g. "member.onboarded", "hit.confirmed", "str.filed"
    subject: str            # e.g. member number or case reference
    detail: dict
    prev_hash: str
    hash: str


def _digest(seq: int, at: str, actor: str, action: str, subject: str, detail: dict, prev_hash: str) -> str:
    body = json.dumps({"seq": seq, "at": at, "actor": actor, "action": action, "subject": subject,
                       "detail": detail, "prev": prev_hash}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def append(chain: list[AuditEntry], *, at: datetime, actor: str, action: str, subject: str,
           detail: dict | None = None) -> AuditEntry:
    if at.tzinfo is None:
        raise ValueError("Audit timestamps must carry a timezone")
    seq = chain[-1].seq + 1 if chain else 1
    prev = chain[-1].hash if chain else GENESIS
    detail = dict(detail or {})
    stamp = at.isoformat()
    entry = AuditEntry(seq, stamp, actor, action, subject, detail, prev,
                       _digest(seq, stamp, actor, action, subject, detail, prev))
    chain.append(entry)
    return entry


def verify(chain: list[AuditEntry]) -> int | None:
    """Return the sequence number of the first entry that fails, or None if intact."""
    prev = GENESIS
    expected_seq = chain[0].seq if chain else 1
    for e in chain:
        if e.seq != expected_seq or e.prev_hash != prev:
            return e.seq
        if e.hash != _digest(e.seq, e.at, e.actor, e.action, e.subject, e.detail, e.prev_hash):
            return e.seq
        prev, expected_seq = e.hash, e.seq + 1
    return None


def to_dict(entry: AuditEntry) -> dict:
    return asdict(entry)
