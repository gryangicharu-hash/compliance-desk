from django.db import DatabaseError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User

from .models import AuditEvent
from .services import record, verify_all


class AuditTrailTests(TestCase):
    def test_chain_verifies_and_database_refuses_changes(self):
        for i in range(3):
            record("co", "member.updated", f"M{i:06d}", {"field": "occupation", "n": i})
        self.assertEqual(list(AuditEvent.objects.order_by("seq").values_list("seq", flat=True)), [1, 2, 3])
        self.assertIsNone(verify_all())
        with self.assertRaises(DatabaseError), transaction.atomic():
            AuditEvent.objects.filter(seq=2).update(actor="someone else")
        with self.assertRaises(DatabaseError), transaction.atomic():
            AuditEvent.objects.filter(seq=3).delete()
        self.assertEqual(AuditEvent.objects.count(), 3)

    def test_access_by_role(self):
        teller = User.objects.create_user("teller", password="correct horse battery 9", role=Role.FRONT_OFFICE)
        co = User.objects.create_user("co", password="correct horse battery 9", role=Role.COMPLIANCE_OFFICER, mfa_confirmed=True)
        record("system", "lists.updated", "UN", {})
        self.client.force_login(teller)
        self.assertEqual(self.client.get(reverse("audittrail:list")).status_code, 403)
        self.client.force_login(co)
        s = self.client.session
        s["mfa_ok"] = True
        s.save()
        r = self.client.get(reverse("audittrail:list"), {"verify": "1"})
        self.assertContains(r, "Integrity check passed")
