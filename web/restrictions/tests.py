import json
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from accounts.security import db_role
from corebanking.services import import_file
from jobs.services import due_items
from members.models import Member, Status
from screening.models import Hit
from screening.services import load_list
from testing import StaffTestCase

from . import services
from .models import RestrictionChange, RestrictionState

UN_XML = (settings.BASE_DIR.parent / "tests" / "fixtures" / "un_consolidated_sample.xml").read_bytes()
HOOK = {"CORE_BANKING_WEBHOOK_URL": "https://corebanking.example/hooks/compliance",
        "CORE_BANKING_WEBHOOK_SECRET": "s" * 40}


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class RestrictionTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        load_list(data=UN_XML, filename="un.xml", list_name=settings.UN_LIST_NAME, kind="sanctions", fmt="un_xml", user=self.co)
        self.onboard(name="Marcus Hale Dunmore", id_number="55556666")          # held at onboarding
        self.m = Member.objects.get()

    def confirm_match(self):
        self.as_user(self.co)
        self.client.post(reverse("screening:decide", args=[self.m.hits.get().pk]),
                         {"decision": "confirmed", "note": "Passport matches the list entry"})
        self.m.refresh_from_db()

    def test_status_changes_are_queued_in_order(self):
        self.assertEqual(RestrictionState.objects.get(member=self.m).level, "hold")
        # Onboarding passed through 'awaiting approval' for a moment: one instruction, not two.
        self.assertEqual(list(RestrictionChange.objects.values_list("previous", "level")), [("none", "hold")])
        from django.utils import timezone
        RestrictionChange.objects.update(delivered_at=timezone.now())          # core banking has it
        self.confirm_match()
        self.assertEqual(self.m.status, Status.BLOCKED)
        changes = list(RestrictionChange.objects.filter(member=self.m).values_list("previous", "level"))
        self.assertEqual(changes, [("none", "hold"), ("hold", "freeze")])

    def test_web_link_delivery_is_signed_and_retried(self):
        sent = []

        def fake(req, timeout):
            sent.append(req)
            return FakeResponse()

        with override_settings(**HOOK), mock.patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            r = services.deliver_pending()
        self.assertEqual((r["sent"], r["failed"]), (0, 1))
        self.assertIn("connection refused", RestrictionChange.objects.get().last_error)
        with override_settings(**HOOK), mock.patch("urllib.request.urlopen", side_effect=fake):
            r = services.deliver_pending()
        self.assertEqual(r["sent"], 1)
        req = sent[0]
        body = json.loads(req.data)
        self.assertEqual((body["member_no"], body["restriction"]), (self.m.member_no, "hold"))
        self.assertNotIn("sanction", req.data.decode().lower())                 # what to do, never why
        import hashlib
        import hmac
        expected = hmac.new(("s" * 40).encode(), req.data, hashlib.sha256).hexdigest()
        self.assertEqual(req.get_header("X-compliance-desk-signature"), f"sha256={expected}")
        self.assertIsNotNone(RestrictionChange.objects.get().delivered_at)

    def test_outbox_file_and_reconciliation(self):
        with tempfile.TemporaryDirectory() as out, override_settings(EXPORT_OUTBOX=out):
            services.run_job()
            files = sorted(p.name for p in Path(out).iterdir())
            self.assertEqual(len(files), 2)                                      # csv + sha256
            text = (Path(out) / files[0]).read_text()
        self.assertIn(f"{self.m.member_no},Marcus Hale Dunmore,hold,", text)
        r = services.reconcile()
        self.assertEqual([s.member for s in r["awaiting"]], [self.m])          # no report yet
        self.assertIn("No restricted-accounts file", " ".join(i["text"] for i in due_items()))
        # Core banking reports nothing restricted: the hold is not applied there.
        b = import_file(data=b"member_no,restriction,as_at\n", filename="restricted.csv",
                        kind="restricted_accounts", user=self.co)
        self.assertEqual(b.status, "imported", b.errors)
        r = services.reconcile()
        self.assertEqual([s.member for s in r["missing"]], [self.m])
        self.assertIn("not restricted in core banking", " ".join(i["text"] for i in due_items()))
        self.as_user(self.teller)
        page = self.client.get(reverse("members:detail", args=[self.m.member_no]))
        self.assertContains(page, "NOT restricted in core banking")
        # Next day the list includes the member: confirmed. The same file twice is accepted.
        for _ in range(2):
            b = import_file(data=f"member_no,restriction\n{self.m.member_no},POST NO DEBIT/CREDIT\n".encode(),
                            filename="restricted-2.csv", kind="restricted_accounts", user=self.co)
            self.assertEqual(b.status, "imported", b.errors)
        r = services.reconcile()
        self.assertEqual((r["missing"], r["awaiting"]), ([], []))
        self.as_user(self.co)
        self.assertContains(self.client.get(reverse("restrictions:overview")), "Every hold and freeze up to the last list is applied")

    def test_release_shows_lift_not_applied(self):
        import_file(data=f"member_no\n{self.m.member_no}\n".encode(), filename="r.csv", kind="restricted_accounts", user=self.co)
        self.as_user(self.co)
        self.client.post(reverse("screening:decide", args=[self.m.hits.get().pk]),
                         {"decision": "false_positive", "note": "Different person: Kenyan, born 1990"})
        self.assertEqual(RestrictionState.objects.get(member=self.m).level, "none")
        import_file(data=f"member_no\n{self.m.member_no}\n".encode(), filename="r2.csv", kind="restricted_accounts", user=self.co)
        self.assertEqual(len(services.reconcile()["lift_not_applied"]), 1)

    def test_access(self):
        self.as_user(self.teller)
        self.assertEqual(self.client.get(reverse("restrictions:overview")).status_code, 403)
        self.as_user(self.ceo)
        self.assertEqual(self.client.get(reverse("restrictions:download")).status_code, 403)
        self.as_user(self.co)
        r = self.client.get(reverse("restrictions:download"))
        self.assertIn(self.m.member_no.encode(), r.content)
        with db_role("auditor"):
            self.assertTrue(Hit.objects.exists())
