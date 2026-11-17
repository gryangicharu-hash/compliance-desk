from datetime import timedelta

from django.db import DatabaseError, transaction
from django.urls import reverse
from django.utils import timezone

from accounts.security import db_role
from cases.models import Case
from corebanking.models import ImportBatch, Transaction
from corebanking.tests import MEMBERS, ImportTestCase
from members.models import Member

from . import services
from .models import Alert, CashReport


def txn_csv(rows) -> bytes:
    lines = ["txn_id,member_no,date_time,amount,direction,channel,counterparty,purpose"]
    lines += [",".join(str(x) for x in r) for r in rows]
    return ("\n".join(lines) + "\n").encode()


def ago(days, hour=10):
    return f"{timezone.localtime() - timedelta(days=days):%Y-%m-%d} {hour:02d}:00"


class MonitoringTests(ImportTestCase):
    def setUp(self):
        super().setUp()
        self.members()
        # Three cash deposits just under the large cash threshold in three days, then one large one.
        self.rows = [("T1", 1001, ago(3), 1500000, "in", "cash", "", "deposit"),
                     ("T2", 1001, ago(2), 1450000, "cr", "Cash", "", "deposit"),
                     ("T3", 1001, ago(1), 1400000, "in", "cash", "", "deposit"),
                     ("T4", 1003, ago(1), '"2,100,000"', "in", "cash", "", "deposit"),
                     ("T5", 1002, ago(1), 3000, "in", "M-Pesa", "0712000000", "loan_repayment")]
        self.batch = self.imp(txn_csv(self.rows), kind="transactions")

    def alerts(self, **kw):
        with db_role("compliance_officer"):
            return list(Alert.objects.filter(**kw).select_related("member", "case").order_by("rule"))

    def test_import_runs_rules_once(self):
        self.assertEqual(self.batch.created, 5, self.batch.errors)
        rules = [a.rule for a in self.alerts(member__member_no="1001")]
        self.assertEqual(rules, ["INCOME_MISMATCH", "STRUCTURING"])
        large = self.alerts(rule="LARGE_CASH")
        self.assertEqual([(a.member.member_no, a.status) for a in large], [("1003", "for_cash_report")])
        self.assertEqual([a.rule for a in self.alerts(member__member_no="1003")], ["INCOME_MISMATCH", "LARGE_CASH"])
        services.run_monitoring("test")                                 # the nightly run finds nothing new
        self.assertEqual(len(self.alerts()), 4)

    def test_overlapping_file_and_conflicts(self):
        more = self.rows + [("T6", 1004, ago(0), 500, "out", "cash", "", "withdrawal")]
        b = self.imp(txn_csv(more), kind="transactions", filename="overlap.csv")
        self.assertEqual((b.created, b.unchanged), (1, 5))
        bad = self.imp(txn_csv([("T1", 1001, ago(3), 999, "in", "cash", "", "")]), kind="transactions", filename="bad.csv")
        self.assertEqual(bad.status, ImportBatch.Status.REJECTED)
        self.assertIn("imported before with different details", bad.errors[0])
        self.assertEqual(Transaction.objects.get(txn_id="T1").amount, 1500000)

    def test_alerts_hidden_from_front_office(self):
        with db_role("front_office"):
            self.assertEqual(Alert.objects.count(), 0)
        with db_role("administrator"):
            self.assertEqual(Alert.objects.count(), 0)
            with self.assertRaises(DatabaseError), transaction.atomic():
                Alert.objects.create(rule="STRUCTURING", member=Member.objects.first(), key="x" * 64,
                                     description="x", status="to_review")
        self.as_user(self.teller)
        self.assertEqual(self.client.get(reverse("monitoring:alerts")).status_code, 403)
        page = self.client.get(reverse("members:detail", args=["1001"]))
        self.assertNotContains(page, "Possible structuring")

    def test_decide_no_action_or_open_case(self):
        mismatch, structuring = self.alerts(member__member_no="1001")
        self.as_user(self.co)
        url = lambda a: reverse("monitoring:decide", args=[a.pk])
        self.client.post(url(mismatch), {"action": "no_action", "note": ""})
        self.assertEqual(self.alerts(pk=mismatch.pk)[0].status, "to_review")
        self.client.post(url(mismatch), {"action": "no_action", "note": "Proceeds of land sale; title deed on file"})
        self.assertEqual(self.alerts(pk=mismatch.pk)[0].status, "no_action")
        r = self.client.post(url(structuring), {"action": "open_case", "note": "No explanation for split deposits"})
        a = self.alerts(pk=structuring.pk)[0]
        self.assertEqual(a.status, "case_opened")
        self.assertRedirects(r, reverse("cases:detail", args=[a.case.ref]), fetch_redirect_response=False)
        with db_role("compliance_officer"):
            self.assertIn("Possible structuring", Case.objects.get().reason)

    def test_weekly_cash_report(self):
        with db_role("compliance_officer"):
            week = services.pending_weeks()[0]
        self.assertEqual(len(week["alerts"]), 1)
        end = week["period_end"]
        self.assertEqual(end.weekday(), 4)                                # Friday
        self.as_user(self.co)
        csv = self.client.get(reverse("monitoring:cash_report_csv", args=[end.isoformat()]))
        self.assertEqual(csv.status_code, 200)
        self.assertIn(b"Carol Akinyi Odhiambo,30000003,in,2100000.00", csv.content)
        url = reverse("monitoring:submit", args=[end.isoformat()])
        self.client.post(url, {"frc_reference": "CTR-2026-0042"})
        self.assertEqual(CashReport.objects.get().rows, 1)
        self.assertEqual(self.alerts(rule="LARGE_CASH")[0].status, "reported")
        with db_role("compliance_officer"):
            self.assertEqual(services.pending_weeks(), [])
        self.as_user(self.ceo)
        self.assertEqual(self.client.get(reverse("monitoring:cash_report_csv", args=[end.isoformat()])).status_code, 403)

    def test_member_import_before_transactions_is_required(self):
        b = self.imp(txn_csv([("T9", 8888, ago(1), 100, "in", "cash", "", "")]), kind="transactions", filename="x.csv")
        self.assertIn("member 8888 is not on record", b.errors[0])


class NoMembersTests(ImportTestCase):
    def test_missing_columns(self):
        b = self.imp(b"member_no,name\n1,A\n")
        self.assertEqual(b.status, ImportBatch.Status.REJECTED)
        self.assertIn("Missing column(s): id_number", b.errors[0])
        self.assertTrue(MEMBERS)
