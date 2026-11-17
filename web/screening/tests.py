from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from accounts.security import db_role
from audittrail.models import AuditEvent
from cases.models import Case
from members.models import Member, Status
from testing import StaffTestCase

from .models import Hit, ListVersion

UN_XML = (settings.BASE_DIR.parent / "tests" / "fixtures" / "un_consolidated_sample.xml").read_bytes()
UN = "UN Security Council consolidated list"


class ScreeningTests(StaffTestCase):
    def load(self, data=UN_XML, name=UN, fmt="un_xml", kind="sanctions", filename="consolidated.xml"):
        self.as_user(self.co)
        return self.client.post(reverse("screening:lists"), {
            "list_name": name, "kind": kind, "fmt": fmt, "file": SimpleUploadedFile(filename, data)})

    def member(self, name):
        return Member.objects.get(name=name)

    def test_load_list_and_reject_bad_files(self):
        self.assertRedirects(self.load(), reverse("screening:lists"), fetch_redirect_response=False)
        first = ListVersion.objects.get(status="current")
        self.assertEqual(first.entry_count, 3)
        self.assertTrue(AuditEvent.objects.filter(action="screening.list_loaded").exists())
        r = self.load(data=b"<html>not a list</html>", filename="error-page.html")
        self.assertContains(r, "The list already in use is unchanged")
        self.assertEqual(ListVersion.objects.get(status="current"), first)
        self.assertEqual(ListVersion.objects.filter(status="rejected").count(), 1)
        self.load()
        self.assertEqual(ListVersion.objects.filter(status="current").count(), 1)
        first.refresh_from_db()
        self.assertEqual(first.status, "superseded")

    def test_existing_members_rescreened_when_list_loads(self):
        self.onboard(name="Daniel Ostrowa Marenti", id_number="11112222")
        m = self.member("Daniel Ostrowa Marenti")
        self.assertEqual(m.status, Status.ACTIVE)                       # no lists loaded yet
        self.load()
        m.refresh_from_db()
        self.assertEqual(m.status, Status.SCREENING_HOLD)
        self.assertEqual(m.hits.get().entry_ref, "XXi.102")
        self.assertEqual(m.latest_assessment.rating, "high")

    def test_new_member_held_at_onboarding_then_released_as_false_positive(self):
        self.load()
        self.onboard(name="Marcus Dunmore Kariuki", id_number="33334444")
        m = self.member("Marcus Dunmore Kariuki")
        self.assertEqual(m.status, Status.SCREENING_HOLD)
        hit = m.hits.get()
        self.as_user(self.co)
        self.client.post(reverse("screening:decide", args=[hit.pk]), {"decision": "false_positive", "note": " "})
        hit.refresh_from_db()
        self.assertEqual(hit.status, Hit.Status.TO_REVIEW)              # a reason is required
        self.client.post(reverse("screening:decide", args=[hit.pk]),
                         {"decision": "false_positive", "note": "Different person: Kenyan teacher, listed person born 1971 abroad"})
        m.refresh_from_db()
        self.assertEqual((m.status, m.latest_assessment.rating), (Status.ACTIVE, "low"))

    def test_onboarding_officer_cannot_decide_own_members_match(self):
        self.load()
        self.onboard(user=self.co, name="Marcus Hale Dunmore", id_number="55556666")
        hit = self.member("Marcus Hale Dunmore").hits.get()
        self.as_user(self.co)
        r = self.client.post(reverse("screening:decide", args=[hit.pk]), {"decision": "false_positive", "note": "x"}, follow=True)
        self.assertContains(r, "cannot decide")
        self.as_user(self.co2)
        self.client.post(reverse("screening:decide", args=[hit.pk]), {"decision": "confirmed", "note": "Name and nationality match"})
        hit.refresh_from_db()
        self.assertEqual(hit.status, Hit.Status.CONFIRMED)

    def test_confirmed_sanctions_match_blocks_and_opens_case(self):
        self.load()
        self.onboard(name="Marcus Hale Dunmore", id_number="55556666")
        m = self.member("Marcus Hale Dunmore")
        self.as_user(self.co)
        self.client.post(reverse("screening:decide", args=[m.hits.get().pk]),
                         {"decision": "confirmed", "note": "Full name match, passport copy matches list photo"})
        m.refresh_from_db()
        self.assertEqual((m.status, m.latest_assessment.rating), (Status.BLOCKED, "prohibited"))
        with db_role("compliance_officer"):
            case = Case.objects.get(member=m)
        self.assertEqual(case.case_type, "sanctions_match")

    def test_pep_list_in_csv(self):
        self.load(data=b"ref,name,aliases\nPEP-001,Grace Wanjiku Mbugua,G. W. Mbugua\n", name="PEP list",
                  kind="pep", fmt="csv", filename="peps.csv")
        self.onboard(name="Grace Wanjiku Mbugua", id_number="77778888", occupation="Elected official")
        m = self.member("Grace Wanjiku Mbugua")
        self.assertEqual(m.status, Status.PENDING_APPROVAL)            # PEP hits flag but don't freeze
        self.as_user(self.co)
        self.client.post(reverse("screening:decide", args=[m.hits.get().pk]),
                         {"decision": "confirmed", "note": "Member of County Assembly, confirmed with ID"})
        m.refresh_from_db()
        self.assertEqual((m.pep, m.status), ("domestic", Status.PENDING_APPROVAL))

    def test_front_office_cannot_open_screening(self):
        self.as_user(self.teller)
        self.assertEqual(self.client.get(reverse("screening:queue")).status_code, 403)
        self.assertEqual(self.client.get(reverse("screening:lists")).status_code, 403)
