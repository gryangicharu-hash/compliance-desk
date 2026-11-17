"""KYC document storage: encrypted files on disk, fingerprinted, every view logged."""
from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.security import cipher
from audittrail.services import record

from .models import Member, MemberDocument, Status

# Recognised by their first bytes, not by the name or the browser's claim.
SIGNATURES = [(b"%PDF-", "application/pdf", ".pdf"), (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
              (b"\x89PNG\r\n\x1a\n", "image/png", ".png")]


def _root() -> Path:
    root = Path(settings.DOCUMENT_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def sniff(data: bytes) -> str:
    for magic, ctype, _ in SIGNATURES:
        if data.startswith(magic):
            return ctype
    raise ValidationError("Only PDF, JPEG or PNG files can be stored (scan or photograph the document).")


def store(member: Member, *, data: bytes, filename: str, doc_type: str, user, description: str = "",
          expires_on=None) -> MemberDocument:
    if member.status == Status.EXITED:
        raise ValidationError("Documents can't be added to an exited member's file.")
    if not data:
        raise ValidationError("The file is empty.")
    if len(data) > settings.DOCUMENT_MAX_BYTES:
        raise ValidationError(f"The file is larger than {settings.DOCUMENT_MAX_BYTES // (1024 * 1024)} MB.")
    ctype = sniff(data)
    stored = secrets.token_hex(20) + ".enc"
    path = _root() / stored
    path.write_bytes(cipher().encrypt_bytes(data))
    try:
        with transaction.atomic():
            doc = MemberDocument.objects.create(
                member=member, doc_type=doc_type, description=description.strip(), filename=Path(filename).name[:200],
                content_type=ctype, size=len(data), sha256=hashlib.sha256(data).hexdigest(), stored_name=stored,
                expires_on=expires_on, uploaded_by=user)
            record(user.username, "document.uploaded", member.member_no,
                   {"document": doc.pk, "type": doc_type, "sha256": doc.sha256, "size": doc.size})
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return doc


def read(doc: MemberDocument, *, user) -> bytes:
    """Decrypt, check the fingerprint, and log who looked."""
    path = _root() / doc.stored_name
    if not path.exists():
        raise ValidationError("The stored file is missing. Report this to the administrator.")
    try:
        data = cipher().decrypt_bytes(path.read_bytes())
    except ValueError:
        data = b""                       # altered or unreadable: treated like a fingerprint mismatch
    if hashlib.sha256(data).hexdigest() != doc.sha256:
        record(user.username, "document.integrity_failed", doc.member.member_no, {"document": doc.pk})
        raise ValidationError("The stored file does not match its fingerprint. Report this to the administrator.")
    record(user.username, "document.viewed", doc.member.member_no, {"document": doc.pk})
    return data


def supersede(doc: MemberDocument, *, user, reason: str) -> MemberDocument:
    if doc.superseded_at:
        raise ValidationError("Already marked as replaced.")
    if not reason.strip():
        raise ValidationError("Say why the document is being replaced.")
    doc.superseded_at, doc.superseded_by, doc.superseded_reason = timezone.now(), user, reason.strip()[:300]
    doc.save(update_fields=["superseded_at", "superseded_by", "superseded_reason"])
    record(user.username, "document.superseded", doc.member.member_no, {"document": doc.pk, "reason": doc.superseded_reason})
    return doc
