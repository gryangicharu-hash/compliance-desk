import time

from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from . import totp
from .middleware import DatabaseRole
from .models import Role, User
from .security import cipher

PW = "correct horse battery 9"


def wrong_code(secret: str) -> str:
    now = time.time()
    valid = {totp.code_at(secret, now + d * 30) for d in (-1, 0, 1)}
    return next(c for c in ("000000", "111111", "222222", "333333") if c not in valid)


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.teller = User.objects.create_user("teller", password=PW, role=Role.FRONT_OFFICE)
        self.co = User.objects.create_user("co", password=PW, role=Role.COMPLIANCE_OFFICER)

    def login(self, username, password=PW, **extra):
        return self.client.post(reverse("accounts:login"), {"username": username, "password": password, **extra})

    def enrol(self):
        self.login("co")
        self.client.get(reverse("accounts:mfa_setup"))
        self.co.refresh_from_db()
        secret = cipher().decrypt(self.co.mfa_secret_enc)
        r = self.client.post(reverse("accounts:mfa_setup"), {"code": totp.code_at(secret, time.time())})
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)
        self.co.refresh_from_db()
        return secret

    def test_every_page_needs_login(self):
        for name in ("home", "members:list", "members:onboard", "audittrail:list"):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302)
            self.assertTrue(r["Location"].startswith(reverse("accounts:login")))

    def test_front_office_logs_in_without_two_factor(self):
        self.assertRedirects(self.login("teller"), reverse("home"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("members:list")).status_code, 200)

    def test_compliance_officer_must_set_up_two_factor_first(self):
        self.assertRedirects(self.login("co"), reverse("accounts:mfa_setup"), fetch_redirect_response=False)
        r = self.client.get(reverse("members:list"))
        self.assertRedirects(r, reverse("accounts:mfa_setup"), fetch_redirect_response=False)
        self.client.get(reverse("accounts:mfa_setup"))
        self.co.refresh_from_db()
        secret = cipher().decrypt(self.co.mfa_secret_enc)
        self.assertNotIn(secret, self.co.mfa_secret_enc)            # stored encrypted
        r = self.client.post(reverse("accounts:mfa_setup"), {"code": wrong_code(secret)})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "doesn")
        r = self.client.post(reverse("accounts:mfa_setup"), {"code": totp.code_at(secret, time.time())})
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("members:list")).status_code, 200)

    def test_used_code_cannot_be_replayed(self):
        secret = self.enrol()
        self.client.post(reverse("accounts:logout"))
        self.assertRedirects(self.login("co"), reverse("accounts:mfa_verify"), fetch_redirect_response=False)
        used = totp.code_at(secret, self.co.mfa_last_step * 30)
        r = self.client.post(reverse("accounts:mfa_verify"), {"code": used})
        self.assertContains(r, "already been used")
        fresh = totp.code_at(secret, (self.co.mfa_last_step + 1) * 30)
        r = self.client.post(reverse("accounts:mfa_verify"), {"code": fresh})
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)

    def test_lockout_after_five_failures(self):
        for _ in range(5):
            self.assertEqual(self.login("teller", "wrong password").status_code, 200)
        r = self.login("teller")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Too many failed attempts")

    def test_no_redirect_to_other_sites(self):
        r = self.login("teller", next="https://evil.example/steal")
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)

    def test_logout_needs_post(self):
        self.login("teller")
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)


class DatabaseRoleTests(TestCase):
    def setUp(self):
        self.teller = User.objects.create_user("teller", password=PW, role=Role.FRONT_OFFICE)
        self.co = User.objects.create_user("co", password=PW, role=Role.COMPLIANCE_OFFICER)

    @staticmethod
    def current() -> str:
        with connection.cursor() as cur:
            cur.execute("SELECT coalesce(current_setting('app.user_role', true), '')")
            return cur.fetchone()[0]

    def run_as(self, user, session):
        seen = {}

        def view(request):
            seen["during"] = self.current()
            return HttpResponse("ok")
        req = RequestFactory().get("/")
        req.user, req.session = user, session
        DatabaseRole(view)(req)
        return seen["during"], self.current()

    def test_role_set_for_request_then_cleared(self):
        self.assertEqual(self.run_as(self.teller, {}), ("front_office", ""))

    def test_no_role_until_two_factor_done(self):
        self.assertEqual(self.run_as(self.co, {"mfa_ok": False}), ("", ""))
        self.assertEqual(self.run_as(self.co, {"mfa_ok": True}), ("compliance_officer", ""))
