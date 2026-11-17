from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from accounts.security import cipher
from members.models import Member, Status
from members.services import find_by_id_number
from testing import StaffTestCase

from . import services
from .models import Guarantee, ImportBatch, ImportProfile, NetworkFlag

MEMBERS = b"""member_no,name,id_number,monthly_income,kyc_complete,source_of_funds,occupation,joined_on
1001,Alice Njeri Mwangi,30000001,50000,yes,Salary,Teacher,2019-03-01
1002,Brian Otieno Ouma,30000002,"40,000",yes,Salary,Clerk,01/06/2020
1003,Carol Akinyi Odhiambo,30000003,60000,yes,Business,Trader,2021-01-15
1004,David Kiprop Rotich,30000004,30000,yes,Salary,Driver,2022-07-30
"""
LOANS = b"""loan_no,borrower_no,loan_amount,guarantor_no,guaranteed_amount
L-01,1001,100000,1002,50000
L-01,1001,100000,1003,50000
L-02,1002,80000,1001,80000
"""
UN_XML = (settings.BASE_DIR.parent / "tests" / "fixtures" / "un_consolidated_sample.xml").read_bytes()


class ImportTestCase(StaffTestCase):
    def imp(self, data, kind="members", filename="extract.csv", profile=None, user=None):
        return services.import_file(data=data, filename=filename, kind=kind, user=user or self.co, profile=profile)

    def members(self):
        b = self.imp(MEMBERS)
        self.assertEqual(b.status, ImportBatch.Status.IMPORTED, b.errors)
        return b


class MemberImportTests(ImportTestCase):
    def test_members_created_encrypted_and_assessed(self):
        b = self.members()
        self.assertEqual((b.created, b.updated, b.unchanged), (4, 0, 0))
        m = Member.objects.get(member_no="1002")
        self.assertNotIn("30000002", m.id_number_enc)
        self.assertEqual(cipher().decrypt(m.id_number_enc), "30000002")
        self.assertEqual(find_by_id_number("30000002"), m)
        self.assertEqual(m.declared_monthly_income, 40000)
        self.assertEqual(str(m.joined_on), "2020-06-01")            # day first
        self.assertEqual((m.status, m.onboarded_by), (Status.ACTIVE, None))
        self.assertEqual(m.latest_assessment.rating, "low")

    def test_same_file_twice_is_refused_and_changes_are_applied(self):
        self.members()
        again = self.imp(MEMBERS)
        self.assertEqual(again.status, ImportBatch.Status.REJECTED)
        self.assertIn("already imported", again.errors[0])
        changed = MEMBERS.replace(b"Driver,2022", b"Boda boda rider,2022")
        b = self.imp(changed, filename="extract-2.csv")
        self.assertEqual((b.created, b.updated, b.unchanged), (0, 1, 3))
        self.assertEqual(Member.objects.get(member_no="1004").occupation, "Boda boda rider")

    def test_a_bad_row_rejects_the_whole_file(self):
        bad = MEMBERS + b"1005,,30000005,10000,yes,Salary,Cook,2023-01-01\n1006,Eve Wairimu,30000006,ten,yes,Salary,Cook,31/31/2023\n"
        b = self.imp(bad)
        self.assertEqual(b.status, ImportBatch.Status.REJECTED)
        self.assertEqual(Member.objects.count(), 0)
        self.assertTrue(any(e.startswith("Row 6: name is required") for e in b.errors), b.errors)
        self.assertTrue(any(e.startswith("Row 7:") for e in b.errors), b.errors)

    def test_id_number_cannot_move_between_members(self):
        self.members()
        b = self.imp(b"member_no,name,id_number\n2001,Someone Else,30000001\n1001,Alice Njeri Mwangi,39999999\n")
        self.assertEqual(b.status, ImportBatch.Status.REJECTED)
        self.assertIn("already belongs to member 1001", b.errors[0])
        self.assertIn("different ID number", b.errors[1])
        self.assertNotIn("30000001", " ".join(b.errors))           # ID numbers never shown back

    def test_listed_person_in_import_goes_on_hold(self):
        from screening.services import load_list
        load_list(data=UN_XML, filename="un.xml", list_name=settings.UN_LIST_NAME, kind="sanctions", fmt="un_xml", user=self.co)
        self.imp(b"member_no,name,id_number\n3001,Marcus Hale Dunmore,40000001\n")
        self.assertEqual(Member.objects.get(member_no="3001").status, Status.SCREENING_HOLD)

    def test_profile_maps_other_column_names_and_onboarding_numbers_continue(self):
        p = ImportProfile.objects.create(name="BankersFile", kind="members",
                                         mapping={"Member No": "member_no", "Full Name": "name", "ID No": "id_number"})
        b = self.imp(b"Member No,Full Name,ID No\n7001,Grace Muthoni,50000001\n", profile=p)
        self.assertEqual(b.status, ImportBatch.Status.IMPORTED, b.errors)
        self.onboard()
        self.assertTrue(Member.objects.filter(member_no="M000001").exists())

    def test_upload_page(self):
        self.as_user(self.co)
        r = self.client.post(reverse("corebanking:imports"), {"kind": "members", "file": SimpleUploadedFile("m.csv", MEMBERS)})
        b = ImportBatch.objects.get()
        self.assertRedirects(r, reverse("corebanking:batch", args=[b.pk]), fetch_redirect_response=False)
        self.as_user(self.teller)
        self.assertEqual(self.client.get(reverse("corebanking:imports")).status_code, 403)
        self.as_user(self.auditor)
        self.client.post(reverse("corebanking:imports"), {"kind": "members", "file": SimpleUploadedFile("m2.csv", MEMBERS)})
        self.assertEqual(ImportBatch.objects.count(), 1)               # auditors can look, not import


class GuarantorTests(ImportTestCase):
    def setUp(self):
        super().setUp()
        self.members()

    def rating(self, no):
        return Member.objects.get(member_no=no).latest_assessment

    def test_circular_guarantee_flags_both_members_and_raises_rating(self):
        b = self.imp(LOANS, kind="loans")
        self.assertEqual((b.status, b.created), (ImportBatch.Status.IMPORTED, 2), b.errors)
        self.assertEqual(Guarantee.objects.count(), 3)
        codes = set(NetworkFlag.objects.filter(member__member_no__in=["1001", "1002"]).values_list("code", flat=True))
        self.assertEqual(codes, {"CIRCULAR"})
        self.assertFalse(NetworkFlag.objects.filter(member__member_no="1003").exists())
        ra = self.rating("1001")
        self.assertEqual(ra.rating, "high")
        self.assertTrue(any("Circular guarantee" in o for o in ra.overrides))
        self.as_user(self.co)
        page = self.client.get(reverse("corebanking:guarantors"))
        self.assertContains(page, "1001 → 1002 → 1001")

    def test_replacing_a_guarantor_clears_the_flag(self):
        self.imp(LOANS, kind="loans")
        self.imp(b"loan_no,borrower_no,loan_amount,guarantor_no,guaranteed_amount\nL-02,1002,80000,1004,80000\n",
                 kind="loans", filename="loans-2.csv")
        self.assertFalse(NetworkFlag.objects.exists())
        self.assertEqual(self.rating("1001").rating, "low")
        self.assertEqual(list(Guarantee.objects.filter(loan__loan_no="L-02").values_list("guarantor__member_no", flat=True)), ["1004"])

    def test_unknown_member_rejects_loans_file(self):
        b = self.imp(b"loan_no,borrower_no,loan_amount,guarantor_no\nL-09,1001,5000,9999\n", kind="loans")
        self.assertEqual(b.status, ImportBatch.Status.REJECTED)
        self.assertIn("member 9999 is not on record", b.errors[0])

    def test_front_office_cannot_open_guarantor_screen(self):
        self.as_user(self.teller)
        self.assertEqual(self.client.get(reverse("corebanking:guarantors")).status_code, 403)
