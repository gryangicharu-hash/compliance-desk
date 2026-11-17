from datetime import date, timedelta

from django.conf import settings
from django.db import DatabaseError, transaction
from django.urls import reverse
from django.utils import timezone

from accounts.security import db_role
from members.models import Member
from screening.services import load_list
from testing import StaffTestCase

from . import services
from .models import Engagement, Finding, FindingResponse, Sample, TestResult, TestRun, TrainingRecord

UN_XML = (settings.BASE_DIR.parent / "tests" / "fixtures" / "un_consolidated_sample.xml").read_bytes()


class AuditTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        load_list(data=UN_XML, filename="un.xml", list_name=settings.UN_LIST_NAME, kind="sanctions", fmt="un_xml", user=self.co)
        self.onboard()                                                               # complete KYC
        self.onboard(name="John Kamau Njoroge", id_number="22223333", kyc_complete="", source_of_funds="")
        self.as_user(self.auditor)
        self.client.post(reverse("assurance:list"), {"kind": "internal_audit", "title": "2026 AML audit",
                                                     "period_start": "2026-01-01", "period_end": "2026-09-30"})
        with db_role("auditor"):
            self.e = Engagement.objects.get()

    def ref(self, name="engagement", *args):
        return reverse(f"assurance:{name}", args=[self.e.ref, *args])

    def results(self):
        with db_role("auditor"):
            run = TestRun.objects.first()
            return run, {r.code: r for r in run.results.all()}

    def test_engagement_has_programme_and_only_auditors_open_them(self):
        self.assertEqual(self.e.ref, "AE-2026-01")
        with db_role("auditor"):
            self.assertEqual(self.e.steps.count(), 30)
        self.as_user(self.co)
        self.assertEqual(self.client.post(reverse("assurance:list"), {"kind": "internal_audit", "title": "x",
                         "period_start": "2026-01-01", "period_end": "2026-02-01"}).status_code, 404)
        self.as_user(self.teller)
        self.assertEqual(self.client.get(reverse("assurance:list")).status_code, 403)

    def test_tests_run_saved_with_fingerprint_and_cannot_be_changed(self):
        self.client.post(self.ref("run_tests"))
        run, res = self.results()
        self.assertEqual(len(res), 17)
        self.assertEqual(len(run.data_fingerprint), 64)
        self.assertEqual([e[0] for e in res["AT01"].exceptions], [Member.objects.get(name="John Kamau Njoroge").member_no])
        self.assertTrue(res["AT10"].passed and res["AT10"].population == 2)      # everyone screened since the list load
        self.assertTrue(res["AT15"].passed)                                      # audit trail intact
        self.assertFalse(res["AT16"].passed)                                     # no training recorded yet
        self.assertEqual(res["AT14"].outcome, "nothing to test")
        with db_role("auditor"):
            with self.assertRaises(DatabaseError), transaction.atomic():
                TestResult.objects.filter(run=run).update(passed=True)
            with self.assertRaises(DatabaseError), transaction.atomic():
                run.delete()
        page = self.client.get(self.ref())
        self.assertContains(page, run.data_fingerprint)

    def test_management_reads_findings_responds_but_cannot_write_papers(self):
        self.client.post(self.ref("run_tests"))
        self.client.post(self.ref("draft_findings"))
        with db_role("auditor"):
            codes = set(Finding.objects.values_list("test_code", flat=True))
        self.assertIn("AT01", codes)
        self.client.post(self.ref("draft_findings"))                              # no duplicates
        with db_role("auditor"):
            self.assertEqual(Finding.objects.count(), len(codes))
            f = Finding.objects.get(test_code="AT01")
        with db_role("compliance_officer"):
            self.assertEqual(Finding.objects.count(), len(codes))
            self.assertEqual(TestRun.objects.count(), 0)                         # working papers stay with the auditor
            self.assertEqual(Finding.objects.filter(pk=f.pk).update(severity="low"), 0)    # invisible to UPDATE
            self.assertEqual(Finding.objects.get(pk=f.pk).severity, "high")
            with self.assertRaises(DatabaseError), transaction.atomic():
                Finding.objects.create(engagement=self.e, ref="F99", title="x", regulation="x", severity="low",
                                       condition="x", recommendation="x")
        with db_role("front_office"):
            self.assertEqual((Engagement.objects.count(), Finding.objects.count()), (0, 0))
        self.as_user(self.co)
        page = self.client.get(self.ref())
        self.assertContains(page, "incomplete KYC files")
        self.assertNotContains(page, "Run all 17 tests")
        self.client.post(self.ref("respond", f.ref), {"response": "KYC remediation drive in branches",
                                                      "owner": "Head of operations", "due_on": "2026-12-31"})
        with db_role("auditor"):
            self.assertEqual(FindingResponse.objects.get().owner, "Head of operations")
        self.assertEqual(self.client.post(self.ref("run_tests")).status_code, 403)

    def test_closing_a_finding_needs_evidence(self):
        self.client.post(self.ref("add_finding"), {"title": "Weak file sign-off", "regulation": "Regs 12-14",
                                                   "severity": "medium", "condition": "3 of 25 files unsigned",
                                                   "recommendation": "Supervisor sign-off before activation"})
        url = self.ref("finding_status", "F01")
        self.client.post(url, {"status": "closed", "evidence": ""})
        with db_role("auditor"):
            self.assertEqual(Finding.objects.get().status, "open")
        self.client.post(url, {"status": "closed", "evidence": "Retested 25 files in November: all signed"})
        with db_role("auditor"):
            self.assertEqual(Finding.objects.get().status, "closed")

    def test_samples_are_reproducible(self):
        self.client.post(self.ref("sample"), {"population": "members", "size": 1, "seed": 42})
        self.client.post(self.ref("sample"), {"population": "members", "size": 1, "seed": 42})
        with db_role("auditor"):
            a, b = Sample.objects.all()
        self.assertEqual((a.selected, a.seed, a.population_size), (b.selected, 42, 2))

    def test_follow_up_and_report(self):
        self.client.post(self.ref("run_tests"))
        self.client.post(self.ref("draft_findings"))
        self.client.post(self.ref("status"), {"status": "closed"})
        self.client.post(reverse("assurance:list"), {"kind": "external_audit", "title": "Follow-up",
                                                     "period_start": "2026-10-01", "period_end": "2026-12-31"})
        with db_role("auditor"):
            second = Engagement.objects.get(title="Follow-up")
        Member.objects.filter(name="John Kamau Njoroge").update(kyc_complete=True, source_of_funds="Salary")
        self.client.post(reverse("assurance:run_tests", args=[second.ref]))
        txt = self.client.get(reverse("assurance:report", args=[second.ref])).content.decode()
        self.assertIn("Automated tests:", txt)
        self.assertIn("Incomplete KYC files: Passes on retest", txt.replace("Active members with incomplete KYC files", "Incomplete KYC files"))
        self.as_user(self.co)
        self.assertEqual(self.client.get(reverse("assurance:report", args=[second.ref])).status_code, 404)  # not issued yet
        self.assertEqual(self.client.get(reverse("assurance:report", args=[self.e.ref])).status_code, 200)  # closed: issued

    def test_governance_records_make_tests_pass(self):
        self.as_user(self.co)
        today = timezone.localdate()
        for u in (self.teller, self.co, self.co2, self.ceo, self.auditor):
            self.client.post(reverse("assurance:governance"), {"t-user": u.pk, "t-course": "AML refresher",
                             "t-completed_on": today.isoformat(), "t-valid_until": (today + timedelta(days=365)).isoformat()})
        self.client.post(reverse("assurance:governance"), {"a-approved_on": (today - timedelta(days=30)).isoformat(),
                         "a-board_minute_ref": "BM/2026/14", "a-document_name": "ML/TF risk assessment 2026"})
        self.assertEqual(TrainingRecord.objects.count(), 5)
        self.as_user(self.auditor)
        self.client.post(self.ref("run_tests"))
        _, res = self.results()
        self.assertTrue(res["AT16"].passed and res["AT17"].passed)

    def test_board_report(self):
        self.as_user(self.ceo)
        q = f"{date.today().year}-Q{(date.today().month - 1) // 3 + 1}"
        page = self.client.get(reverse("assurance:board"), {"q": q})
        self.assertContains(page, "Low risk members")
        self.assertNotContains(page, "Mary Wanjiru Kamau")
        txt = self.client.get(reverse("assurance:board"), {"q": q, "format": "txt"}).content.decode()
        self.assertIn("Members by risk rating: low 1, medium 1", txt)
        self.assertEqual(services.quarter("2026-Q4"), (date(2026, 10, 1), date(2026, 12, 31)))
        self.as_user(self.teller)
        self.assertEqual(self.client.get(reverse("assurance:board")).status_code, 403)

    def test_periodic_review(self):
        m = Member.objects.get(name="Mary Wanjiru Kamau")
        Member.objects.filter(pk=m.pk).update(last_review_on=date(2020, 1, 1))
        self.as_user(self.co)
        self.client.post(reverse("members:review", args=[m.member_no]), {"note": ""})
        m.refresh_from_db()
        self.assertEqual(m.last_review_on, date(2020, 1, 1))
        self.client.post(reverse("members:review", args=[m.member_no]), {"note": "ID, payslip and activity checked"})
        m.refresh_from_db()
        self.assertEqual(m.last_review_on, timezone.localdate())
        self.assertGreater(m.latest_assessment.next_review_on, timezone.localdate())
