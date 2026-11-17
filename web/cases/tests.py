from datetime import timedelta

from django.db import DatabaseError, transaction
from django.urls import reverse
from django.utils import timezone

from accounts.security import db_role
from members.models import Member
from testing import StaffTestCase

from .models import Case


class CaseTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.onboard()
        self.m = Member.objects.get()

    def open(self):
        self.as_user(self.co)
        return self.client.post(reverse("cases:open", args=[self.m.member_no]),
                                {"case_type": "unusual_activity", "reason": "Cash deposits of KES 4.4m in 3 days against KES 60,000 income"})

    def case(self):
        with db_role("compliance_officer"):
            return Case.objects.get()

    def test_open_case_starts_two_day_clock_and_raises_rating(self):
        self.open()
        c = self.case()
        self.assertEqual(c.str_due_at - c.suspicion_formed_at, timedelta(days=2))
        self.assertEqual(self.m.latest_assessment.rating, "high")
        self.assertTrue(any("case" in o for o in self.m.latest_assessment.overrides))

    def test_front_office_never_sees_the_case(self):
        self.open()
        self.as_user(self.teller)
        page = self.client.get(reverse("members:detail", args=[self.m.member_no]))
        self.assertContains(page, "Additional compliance factors (restricted)")
        self.assertNotContains(page, "suspicious activity case")
        self.assertNotContains(page, "C-000001")
        self.assertEqual(self.client.get(reverse("cases:list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("cases:detail", args=["C-000001"])).status_code, 403)

    def test_database_hides_cases_from_front_office(self):
        self.open()
        with db_role("front_office"):
            self.assertEqual(Case.objects.count(), 0)
            with self.assertRaises(DatabaseError), transaction.atomic():
                Case.objects.create(ref="C-999999", member=self.m, case_type="other", reason="x",
                                    suspicion_formed_at=timezone.now(), str_due_at=timezone.now() + timedelta(days=2),
                                    opened_by=self.teller)
        with db_role(""):
            self.assertEqual(Case.objects.count(), 0)
        with db_role("auditor"):
            self.assertEqual(Case.objects.count(), 1)

    def test_filing_needs_frc_reference_and_produces_xml(self):
        self.open()
        url = reverse("cases:decide", args=["C-000001", "file"])
        self.client.post(url, {"frc_reference": "", "note": "Reported cash pattern"})
        self.assertEqual(self.case().status, "open")
        self.client.post(url, {"frc_reference": "FRC-STR-2026-00123", "note": "Reported cash pattern"})
        c = self.case()
        self.assertEqual((c.status, c.frc_reference, c.decided_by), ("str_filed", "FRC-STR-2026-00123", self.co))
        xml = self.client.get(reverse("cases:str_xml", args=["C-000001"]))
        self.assertEqual(xml.status_code, 200)
        self.assertIn(b"<report_code>STR</report_code>", xml.content)
        self.assertIn(b"Mary Wanjiru Kamau", xml.content)
        self.assertIn("attachment", xml["Content-Disposition"])

    def test_close_needs_reason_and_restores_rating(self):
        self.open()
        url = reverse("cases:decide", args=["C-000001", "close"])
        self.client.post(url, {"note": ""})
        self.assertEqual(self.case().status, "open")
        self.client.post(url, {"note": "Payslips confirm a land sale; documents on file"})
        self.assertEqual(self.case().status, "closed_not_suspicious")
        self.assertEqual(self.m.latest_assessment.rating, "low")

    def test_senior_management_can_view_but_not_decide(self):
        self.open()
        self.as_user(self.ceo)
        self.assertEqual(self.client.get(reverse("cases:list")).status_code, 200)
        r = self.client.post(reverse("cases:decide", args=["C-000001", "close"]), {"note": "x"})
        self.assertEqual(r.status_code, 403)
