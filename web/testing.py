"""Shared test helpers (not a test module)."""
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User

PW = "correct horse battery 9"

FORM = {
    "member_type": "individual", "name": "Mary Wanjiru Kamau", "id_number": "23456789", "nationality": "Kenya",
    "residence": "Kenya", "occupation": "Teacher", "employer": "Public school (TSC)", "checkoff": "on",
    "declared_monthly_income": "60000", "source_of_funds": "Salary", "channel": "branch",
    "products": ["bosa_deposits", "fosa_account"], "pep": "none", "expected_cash": "low", "kyc_complete": "on",
}


class StaffTestCase(TestCase):
    def setUp(self):
        mk = lambda name, role, mfa=True: User.objects.create_user(name, password=PW, role=role, mfa_confirmed=mfa)
        self.teller = mk("teller", Role.FRONT_OFFICE, mfa=False)
        self.co = mk("co", Role.COMPLIANCE_OFFICER)
        self.co2 = mk("co2", Role.COMPLIANCE_OFFICER)
        self.ceo = mk("ceo", Role.SENIOR_MANAGEMENT)
        self.auditor = mk("aud", Role.AUDITOR)

    def as_user(self, user):
        self.client.force_login(user)
        s = self.client.session
        s["mfa_ok"] = True
        s.save()

    def onboard(self, user=None, **changes):
        self.as_user(user or self.teller)
        return self.client.post(reverse("members:onboard"), {**FORM, **changes})
