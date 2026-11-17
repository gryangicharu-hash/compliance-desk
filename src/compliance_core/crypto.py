"""Field-level encryption for identity numbers and other sensitive fields.

Values are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) through MultiFernet,
so keys can be rotated: the first key encrypts, all keys decrypt, and rotate()
re-encrypts old values with the new key.

Because encrypted values can't be searched, a "blind index" (HMAC-SHA256 of the
normalised value under a separate key) is stored next to each one. Staff can
look a member up by ID number without the database ever holding it in clear.

Keys come from the deployment's secret store, never from the code or database.
"""
from __future__ import annotations

import hashlib
import hmac
import re

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class FieldCipher:
    def __init__(self, encryption_keys: list[str], index_key: str):
        if not encryption_keys:
            raise ValueError("At least one encryption key is required")
        if len(index_key) < 32:
            raise ValueError("Index key must be at least 32 characters")
        self._f = MultiFernet([Fernet(k) for k in encryption_keys])
        self._index_key = index_key.encode()

    @staticmethod
    def new_key() -> str:
        return Fernet.generate_key().decode()

    def encrypt(self, value: str) -> str:
        return self._f.encrypt(value.encode("utf-8")).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._f.decrypt(token.encode()).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Value cannot be decrypted with the configured keys") from exc

    def encrypt_bytes(self, data: bytes) -> bytes:
        """For files (KYC documents). Same keys and rotation as field values."""
        return self._f.encrypt(data)

    def decrypt_bytes(self, token: bytes) -> bytes:
        try:
            return self._f.decrypt(token)
        except InvalidToken as exc:
            raise ValueError("File cannot be decrypted with the configured keys") from exc

    def rotate(self, token: str) -> str:
        return self._f.rotate(token.encode()).decode()

    @staticmethod
    def _normalise(value: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", value.upper())

    def blind_index(self, value: str) -> str:
        return hmac.new(self._index_key, self._normalise(value).encode(), hashlib.sha256).hexdigest()
