import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core import mail
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.security import db_role
from cases.services import open_case
from corebanking.tests import MEMBERS
from members.models import Member
from screening.models import ListVersion
from testing import StaffTestCase

from .models import JobRun
from .services import due_items

UN_FILE = settings.BASE_DIR.parent / "tests" / "fixtures" / "un_consolidated_sample.xml"


class JobTests(StaffTestCase):
    def run_cmd(self, *args, **kw):
        call_command("runjobs", *args, stdout=open("/dev/null", "w"), **kw)

    def test_daily_list_check_loads_only_when_changed(self):
        self.assertIn("never been loaded", " ".join(i["text"] for i in due_items()))
        self.run_cmd("fetch_un_list", source=str(UN_FILE))
        self.assertEqual(JobRun.objects.get().status, JobRun.Status.SUCCEEDED)
        v = ListVersion.objects.get(status="current")
        self.assertEqual((v.entry_count, v.loaded_by.username), (3, "system"))
        self.run_cmd("fetch_un_list", source=UN_FILE.as_uri())          # same file via a URL: unchanged
        self.assertEqual(JobRun.objects.first().status, JobRun.Status.NOTHING_TO_DO)
        self.assertEqual(ListVersion.objects.count(), 1)
        self.assertNotIn("UN sanctions list", " ".join(i["text"] for i in due_items()))

    def test_failed_download_is_recorded_and_list_kept(self):
        self.run_cmd("fetch_un_list", source=str(UN_FILE))
        with tempfile.NamedTemporaryFile(suffix=".xml") as bad:
            bad.write(b"<html>Service unavailable</html>")
            bad.flush()
            with self.assertRaises(CommandError):
                self.run_cmd("fetch_un_list", source=bad.name)
        run = JobRun.objects.first()
        self.assertEqual(run.status, JobRun.Status.FAILED)
        self.assertIn("unchanged", run.error)
        self.assertEqual(ListVersion.objects.filter(status="current").count(), 1)
        self.assertEqual(ListVersion.objects.filter(status="rejected").count(), 1)
        self.assertIn("fetch un list' job failed", " ".join(i["text"] for i in due_items()))

    def test_inbox_files_imported_and_moved(self):
        with tempfile.TemporaryDirectory() as inbox:
            (Path(inbox) / "members").mkdir()
            (Path(inbox) / "members" / "a-members.csv").write_bytes(MEMBERS)
            (Path(inbox) / "members" / "b-broken.csv").write_bytes(b"member_no,name\n1,x\n")
            with override_settings(IMPORT_INBOX=inbox):
                self.run_cmd("nightly")
            today = timezone.localdate().isoformat()
            self.assertTrue((Path(inbox) / "processed" / today / "a-members.csv").exists())
            self.assertTrue((Path(inbox) / "rejected" / today / "b-broken.csv").exists())
            self.assertEqual(list((Path(inbox) / "members").glob("*.csv")), [])
        self.assertEqual(Member.objects.count(), 4)
        self.assertEqual(set(JobRun.objects.values_list("job", flat=True)),
                         {"import_inbox", "refresh_guarantors", "run_monitoring", "send_reminders"})

    def test_reminder_email_has_counts_not_names(self):
        self.co.email = "co@example.org"
        self.co.save()
        self.onboard()
        m = Member.objects.get()
        with db_role("compliance_officer"):
            open_case(m, user=self.co, case_type="unusual_activity", reason="Cash pattern",
                      suspicion_formed_at=timezone.now() - timedelta(days=3))
        self.run_cmd("send_reminders")
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("1 case past the 2-day STR deadline", body)
        self.assertNotIn(m.name, body)
        self.assertNotIn("C-000001", body)
        self.assertEqual(mail.outbox[0].to, ["co@example.org"])

    def test_overview_and_jobs_page_by_role(self):
        self.as_user(self.co)
        self.assertContains(self.client.get(reverse("home")), "Needs attention")
        r = self.client.post(reverse("jobs:run", args=["run_monitoring"]), follow=True)
        self.assertContains(r, "Run monitoring")
        self.assertEqual(self.client.post(reverse("jobs:run", args=["send_reminders"])).status_code, 404)
        self.as_user(self.teller)
        self.assertNotContains(self.client.get(reverse("home")), "Needs attention")
        self.assertEqual(self.client.get(reverse("jobs:list")).status_code, 403)
        self.as_user(self.auditor)
        self.assertContains(self.client.get(reverse("jobs:list")), "run_monitoring")
        self.assertEqual(self.client.post(reverse("jobs:run", args=["run_monitoring"])).status_code, 403)
