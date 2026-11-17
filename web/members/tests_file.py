"""Keeping the member file: edits, documents, exits, lifting a freeze."""
import tempfile
from datetime import timedelta

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.security import db_role
from audittrail.models import AuditEvent
from cases.services import open_case
from screening.models import Hit
from screening.services import load_list
from testing import FORM, StaffTestCase

from .models import Member, MemberDocument, Status, StatusRelease
from .services import find_by_id_number

UN_XML = (settings.BASE_DIR.parent / "tests" / "fixtures" / "un_consolidated_sample.xml").read_bytes()
PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
_tmp = tempfile.mkdtemp()


@override_settings(DOCUMENT_ROOT=_tmp)
class MemberFileTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.onboard()
        self.m = Member.objects.get()

    def edit(self, user=None, **changes):
        self.as_user(user or self.teller)
        data = {**FORM, "id_number": "", "reason": "Periodic review: new employer letter", **changes}
        return self.client.post(reverse("members:edit", args=[self.m.member_no]), data)

    # ---------------------------------------------------------------- editing

    def test_edit_keeps_history_and_rerates(self):
        r = self.edit(occupation="Head teacher", declared_monthly_income="95000")
        self.assertRedirects(r, reverse("members:detail", args=[self.m.member_no]), fetch_redirect_response=False)
        self.m.refresh_from_db()
        self.assertEqual((self.m.occupation, self.m.declared_monthly_income), ("Head teacher", 95000))
        ch = self.m.changes.get()
        self.assertEqual(ch.fields, ["declared_monthly_income", "occupation"])
        self.assertNotIn("Teacher", ch.previous_enc)                          # previous values encrypted
        self.as_user(self.co)
        page = self.client.get(reverse("members:detail", args=[self.m.member_no]))
        self.assertContains(page, "Previous values")
        self.assertContains(page, "60000")
        self.assertEqual(self.m.assessments.count(), 2)

    def test_reason_required_and_nothing_changed(self):
        self.edit(reason="", occupation="Nurse")
        self.m.refresh_from_db()
        self.assertEqual(self.m.occupation, "Teacher")
        self.edit()
        self.assertFalse(self.m.changes.exists())

    def test_id_change_and_name_change_rescreen(self):
        load_list(data=UN_XML, filename="un.xml", list_name=settings.UN_LIST_NAME, kind="sanctions", fmt="un_xml", user=self.co)
        self.edit(id_number="99990000", name="Marcus Hale Dunmore", reason="Name corrected from ID copy")
        self.m.refresh_from_db()
        self.assertEqual(find_by_id_number("99990000"), self.m)
        self.assertEqual(self.m.status, Status.SCREENING_HOLD)
        self.assertTrue(Hit.objects.filter(member=self.m, status="to_review").exists())
        self.onboard(name="Other Person", id_number="12121212")
        self.edit(id_number="12121212")
        self.assertEqual(find_by_id_number("12121212").name, "Other Person")   # duplicate refused

    def test_becoming_a_pep_needs_approval_again(self):
        self.edit(pep="domestic", source_of_wealth="Salary and farm income", reason="Elected MCA in by-election")
        self.m.refresh_from_db()
        self.assertEqual(self.m.status, Status.PENDING_APPROVAL)

    # ---------------------------------------------------------------- documents

    def upload(self, data=PDF, name="id.pdf", user=None):
        self.as_user(user or self.teller)
        return self.client.post(reverse("members:upload_document", args=[self.m.member_no]),
                                {"doc_type": "national_id", "file": SimpleUploadedFile(name, data)})

    def test_documents_encrypted_fingerprinted_and_view_logged(self):
        self.upload()
        doc = MemberDocument.objects.get()
        from pathlib import Path
        stored = (Path(_tmp) / doc.stored_name).read_bytes()
        self.assertNotIn(b"%PDF", stored)
        r = self.client.get(reverse("members:document", args=[self.m.member_no, doc.pk]))
        self.assertEqual((r.status_code, r.content, r["Content-Type"]), (200, PDF, "application/pdf"))
        self.assertTrue(AuditEvent.objects.filter(action="document.viewed").exists())
        (Path(_tmp) / doc.stored_name).write_bytes(stored[:-10] + b"tampered!!")
        r = self.client.get(reverse("members:document", args=[self.m.member_no, doc.pk]), follow=True)
        self.assertNotContains(r, "%PDF")

    def test_only_real_pdf_or_images_accepted(self):
        self.upload(data=b"<html><script>alert(1)</script>", name="id.pdf")
        self.assertFalse(MemberDocument.objects.exists())
        self.upload(data=b"\x89PNG\r\n\x1a\n" + b"0" * 100, name="photo.png")
        self.assertEqual(MemberDocument.objects.get().content_type, "image/png")

    def test_replacing_a_document_needs_a_reason(self):
        self.upload()
        doc = MemberDocument.objects.get()
        url = reverse("members:supersede_document", args=[self.m.member_no, doc.pk])
        self.client.post(url, {"reason": ""})
        doc.refresh_from_db()
        self.assertIsNone(doc.superseded_at)
        self.client.post(url, {"reason": "New ID card issued"})
        doc.refresh_from_db()
        self.assertIsNotNone(doc.superseded_at)

    # ---------------------------------------------------------------- exits

    def test_exit(self):
        self.as_user(self.teller)
        url = reverse("members:exit", args=[self.m.member_no])
        self.client.post(url, {"exited_on": (timezone.localdate() + timedelta(days=1)).isoformat(), "reason": "Retired"})
        self.m.refresh_from_db()
        self.assertEqual(self.m.status, Status.ACTIVE)                          # future date refused
        self.client.post(url, {"exited_on": timezone.localdate().isoformat(), "reason": "Retired and withdrew savings"})
        self.m.refresh_from_db()
        self.assertEqual((self.m.status, self.m.exited_on), (Status.EXITED, timezone.localdate()))
        page = self.client.get(reverse("members:detail", args=[self.m.member_no]))
        self.assertContains(page, "Records are kept until")
        self.assertEqual(self.client.get(reverse("members:edit", args=[self.m.member_no])).status_code, 200)
        self.edit(occupation="Retired")
        self.m.refresh_from_db()
        self.assertEqual(self.m.occupation, "Teacher")                         # exited files are closed

    def test_front_office_cannot_exit_member_with_open_case_and_is_not_told_why(self):
        with db_role("compliance_officer"):
            open_case(self.m, user=self.co, case_type="unusual_activity", reason="Cash pattern")
        self.as_user(self.teller)
        r = self.client.post(reverse("members:exit", args=[self.m.member_no]),
                             {"exited_on": timezone.localdate().isoformat(), "reason": "Moving abroad"})
        self.assertContains(r, "Refer the request to the compliance officer")
        self.assertNotContains(r, "C-000001")
        self.assertNotContains(r, "suspicio")
        self.m.refresh_from_db()
        self.assertEqual(self.m.status, Status.ACTIVE)


@override_settings(DOCUMENT_ROOT=_tmp)
class FreezeReleaseTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        load_list(data=UN_XML, filename="un.xml", list_name=settings.UN_LIST_NAME, kind="sanctions", fmt="un_xml", user=self.co)
        self.onboard(name="Marcus Hale Dunmore", id_number="55556666")
        self.m = Member.objects.get()
        self.as_user(self.co)
        self.client.post(reverse("screening:decide", args=[self.m.hits.get().pk]),
                         {"decision": "confirmed", "note": "Passport matches"})
        self.m.refresh_from_db()
        self.assertEqual(self.m.status, Status.BLOCKED)

    def test_two_people_lift_a_freeze(self):
        url = reverse("members:request_release", args=[self.m.member_no])
        self.client.post(url, {"reason": "Delisted", "evidence": "UN SC press release SC/99999, 3 Nov 2026"})
        r = StatusRelease.objects.get()
        decide = lambda d: reverse("members:decide_release", args=[self.m.member_no, r.pk, d])
        self.client.post(decide("approve"), {"note": "I asked, so I approve"})
        r.refresh_from_db()
        self.assertEqual(r.state, "pending")                                    # same person refused
        self.as_user(self.teller)
        self.assertEqual(self.client.post(decide("approve"), {"note": "x"}).status_code, 403)
        self.as_user(self.ceo)
        self.client.post(decide("approve"), {"note": "Delisting verified on the UN website"})
        r.refresh_from_db()
        self.m.refresh_from_db()
        self.assertEqual(r.state, "approved")
        self.assertNotEqual(self.m.status, Status.BLOCKED)
        self.assertEqual(self.m.hits.get().status, "lifted")
        self.assertTrue(AuditEvent.objects.filter(action="member.freeze_lifted").exists())

    def test_rejection_keeps_freeze_and_frozen_member_cannot_exit(self):
        self.client.post(reverse("members:request_release", args=[self.m.member_no]),
                         {"reason": "Member says it's not him", "evidence": "Letter from member"})
        r = StatusRelease.objects.get()
        self.as_user(self.co2)
        self.client.post(reverse("members:decide_release", args=[self.m.member_no, r.pk, "reject"]),
                         {"note": "Letter is not evidence; passport matches"})
        self.m.refresh_from_db()
        self.assertEqual(self.m.status, Status.BLOCKED)
        r = self.client.post(reverse("members:exit", args=[self.m.member_no]),
                             {"exited_on": timezone.localdate().isoformat(), "reason": "Wants to leave"})
        self.assertContains(r, "can&#x27;t be exited")
