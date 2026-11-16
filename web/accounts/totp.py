"""Time-based one-time passwords (RFC 6238), standard library only.

Works with Google Authenticator, Microsoft Authenticator and similar apps:
SHA-1, 6 digits, 30-second steps. One step of clock drift either way is accepted.
verify() returns the matched time step so the caller can refuse a code that has
already been used (replay protection).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def hotp(key: bytes, counter: int, digits: int = DIGITS, digest=hashlib.sha1) -> str:
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % 10 ** digits).zfill(digits)


def code_at(secret: str, at: float) -> str:
    return hotp(_key(secret), int(at // STEP))


def verify(secret: str, code: str, *, at: float | None = None, last_used_step: int | None = None,
           drift: int = 1) -> int | None:
    """Return the matching time step, or None. Codes at or before last_used_step are refused."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    now_step = int((time.time() if at is None else at) // STEP)
    key = _key(secret)
    for step in range(now_step - drift, now_step + drift + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if hmac.compare_digest(hotp(key, step), code):
            return step
    return None


def provisioning_uri(secret: str, account: str, issuer: str = "Compliance Desk") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}"
            f"&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}")
