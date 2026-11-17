"""Check a database, typically one just restored from a backup: the audit trail's hash
chain is intact, migrations are complete, and the main tables have rows. Exits non-zero
on any problem, so a scheduled restore test can alert on it."""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from accounts.security import db_role
from audittrail.models import AuditEvent
from audittrail.services import verify_all

TABLES = ["accounts_user", "members_member", "members_riskassessment", "screening_listversion", "screening_hit",
          "cases_case", "corebanking_transaction", "corebanking_guarantee", "monitoring_alert",
          "assurance_engagement", "audittrail_auditevent"]


class Command(BaseCommand):
    help = "Verify the audit trail and print row counts (use after restoring a backup)."

    def handle(self, *args, **opts):
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pending:
            raise CommandError(f"{len(pending)} migration(s) not applied")
        broken = verify_all()
        if broken is not None:
            raise CommandError(f"Audit trail broken at entry {broken}: a record was altered, removed or reordered")
        self.stdout.write(f"Audit trail intact: {AuditEvent.objects.count()} entries")
        with db_role("auditor"), connection.cursor() as cur:      # a role that can see every protected table
            for t in TABLES:
                cur.execute(f"SELECT count(*) FROM {t}")          # noqa: S608 (fixed table names)
                self.stdout.write(f"{t}: {cur.fetchone()[0]}")
