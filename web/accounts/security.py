"""Shared security helpers: field encryption, role checks."""
from functools import lru_cache, wraps

from django.conf import settings
from django.core.exceptions import PermissionDenied

from compliance_core.crypto import FieldCipher


@lru_cache(maxsize=1)
def cipher() -> FieldCipher:
    return FieldCipher(settings.FIELD_ENCRYPTION_KEYS, settings.BLIND_INDEX_KEY)


def role_required(*roles: str):
    """View decorator: only users whose role is listed may proceed; others get 403."""
    def deco(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated or not request.user.has_role(*roles):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        wrapped.allowed_roles = roles
        return wrapped
    return deco


def mask(value: str, visible: int = 3) -> str:
    return "•" * max(0, len(value) - visible) + value[-visible:] if value else ""
