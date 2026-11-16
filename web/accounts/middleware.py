from django.conf import settings
from django.db import connection
from django.shortcuts import redirect
from django.urls import reverse

MFA_SESSION_KEY = "mfa_ok"


def mfa_done(request) -> bool:
    user = request.user
    return user.is_authenticated and (not user.requires_mfa or request.session.get(MFA_SESSION_KEY) is True)


class LoginAndMfaRequired:
    """Every page needs a logged-in user, and users who need two-factor login must have
    completed it in this session. Only the login and two-factor pages are open."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        login = reverse("accounts:login")
        if path.startswith(settings.STATIC_URL) or path == login:
            return self.get_response(request)
        if not request.user.is_authenticated:
            return redirect(f"{login}?next={request.get_full_path()}")
        if not mfa_done(request):
            allowed = {reverse("accounts:mfa_setup"), reverse("accounts:mfa_verify"), reverse("accounts:logout")}
            if path not in allowed:
                target = "accounts:mfa_verify" if request.user.mfa_confirmed else "accounts:mfa_setup"
                return redirect(target)
        return self.get_response(request)


class DatabaseRole:
    """Tell PostgreSQL the user's role for this request, so row-level security policies
    (for example, hiding suspicious-activity cases from front office) apply. The setting
    is cleared after every request; connections are not reused (CONN_MAX_AGE = 0)."""

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _set(role: str) -> None:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.user_role', %s, false)", [role])

    def __call__(self, request):
        role = request.user.role if mfa_done(request) else ""
        self._set(role)
        try:
            return self.get_response(request)
        finally:
            self._set("")
