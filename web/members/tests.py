from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from audittrail.models import AuditEvent
from audittrail.services import verify_all

from . import services
from .models import Member, Status

PW = "correct horse battery 9"

FORM = {
    "member_type": "individual", "name": "Mary Wanjiru Kamau", "id_number": "23456789", "nationality": "Kenya",
    "residence": "Kenya", "occupation": "Teacher", "employer": "Public school (TSC)", "checkoff": "on",
    "declared_monthly_income": "60000", "source_of_funds": "Salary", "channel": "branch",
    "products": ["bosa_deposits", "fosa_account"], "pep": "none", "expected_cash": "low", "kyc_complete": "on",
}


class Base(TestCase):
    def setUp(self):
        self.teller = User.objects.create_user("teller", password=PW, role=Role.FRONT_OFFICE)
        self.ceo = User.objects.create_user("ceo", password=PW, role=Role.SENIOR_MANAGEMENT, mfa_confirmed=True)
        self.auditor = User.objects.create_user("aud", password=PW, role=Role.AUDITOR, mfa_confirmed=True)

    def as_user(self, user):
        self.client.force_login(user)
        s = self.client.session
        s["mfa_ok"] = True
        s.save()

    def onboard(self, **changes):
        return self.client.post(reverse("members:onboard"), {**FORM, **changes})


class OnboardingTests(Base):
    def test_low_risk_member_is_encrypted_rated_logged_and_active(self):
        self.as_user(self.teller)
        r = self.onboard()
        m = Member.objects.get()
        self.assertRedirects(r, reverse("members:detail", args=[m.member_no]), fetch_redirect_response=False)
        self.assertEqual(m.member_no, "M000001")
        self.assertNotIn("23456789", m.id_number_enc)
        self.assertEqual(services.find_by_id_number("2345 6789"), m)
        ra = m.latest_assessment
        self.assertEqual((ra.rating, ra.due_diligence, ra.score), ("low", "simplified", 10))
        self.assertEqual(m.status, Status.ACTIVE)
        actions = list(AuditEvent.objects.order_by("seq").values_list("action", flat=True))
        self.assertEqual(actions, ["member.onboarded", "risk.assessed", "member.activated"])
        self.assertIsNone(verify_all())
        page = self.client.get(reverse("members:detail", args=[m.member_no]))
        self.assertContains(page, "•••••789")
        self.assertNotContains(page, "23456789")

    def test_pep_waits_for_senior_management_with_source_of_wealth(self):
        self.as_user(self.teller)
        self.onboard(name="Halima Abdi Noor", id_number="31112222", pep="domestic", occupation="Elected official")
        m = Member.objects.get()
        self.assertEqual((m.status, m.latest_assessment.rating), (Status.PENDING_APPROVAL, "high"))
        # front office cannot approve
        self.assertEqual(self.client.post(reverse("members:approve", args=[m.member_no])).status_code, 403)
        self.as_user(self.ceo)
        self.client.post(reverse("members:approve", args=[m.member_no]))
        m.refresh_from_db()
        self.assertEqual(m.status, Status.PENDING_APPROVAL)               # no source of wealth yet
        Member.objects.filter(pk=m.pk).update(source_of_wealth="Salary and allowances")
        self.client.post(reverse("members:approve", args=[m.member_no]))
        m.refresh_from_db()
        self.assertEqual((m.status, m.pep_approved_by), (Status.ACTIVE, self.ceo))

    def test_onboarding_officer_cannot_approve_own_member(self):
        m = services.onboard(data={k: v for k, v in FORM.items() if k not in ("checkoff", "kyc_complete")} | {
            "pep": "domestic", "source_of_wealth": "Salary", "declared_monthly_income": 60000,
            "checkoff": True, "kyc_complete": True}, persons=[], user=self.ceo)
        with self.assertRaises(ValidationError):
            services.approve(m, self.ceo)

    def test_duplicate_id_number_rejected(self):
        self.as_user(self.teller)
        self.onboard()
        r = self.onboard(name="Someone Else", id_number="2345-6789")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "already exists")
        self.assertEqual(Member.objects.count(), 1)

    def test_group_needs_officials_and_records_owners(self):
        self.as_user(self.teller)
        r = self.onboard(member_type="group", name="Upendo Women Group", id_number="SHG/NYR/2019/0442")
        self.assertContains(r, "at least one official")
        self.onboard(member_type="group", name="Upendo Women Group", id_number="SHG/NYR/2019/0442",
                     officials="Esther Gitau, Chairperson\nLucy Omondi, Treasurer", beneficial_owners="Esther Gitau, 12.5")
        m = Member.objects.get()
        self.assertEqual(m.persons.count(), 3)
        bad = self.onboard(member_type="group", name="Another", id_number="SHG-2", officials="A B, Chair",
                           beneficial_owners="A B, 150")
        self.assertContains(bad, "between 0 and 100")

    def test_auditor_and_senior_management_cannot_onboard(self):
        for user in (self.auditor, self.ceo):
            self.as_user(user)
            self.assertEqual(self.client.get(reverse("members:onboard")).status_code, 403)

    def test_search_by_id_number(self):
        self.as_user(self.teller)
        self.onboard()
        self.assertContains(self.client.get(reverse("members:list"), {"q": "23456789"}), "Mary Wanjiru Kamau")
        self.assertNotContains(self.client.get(reverse("members:list"), {"q": "99999999"}), "Mary Wanjiru Kamau")

    def test_database_refuses_active_unapproved_pep(self):
        self.as_user(self.teller)
        self.onboard(name="Halima Abdi Noor", id_number="31112222", pep="domestic")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Member.objects.update(status=Status.ACTIVE)
