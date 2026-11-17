from django.db import migrations

# Independence safeguards, enforced by PostgreSQL whatever the application does:
#   * only the auditor role can create or change the working papers;
#   * saved test runs, results and samples can never be changed or deleted;
#   * management reads engagements and findings and writes only its own responses.
PAPERS = ["assurance_engagement", "assurance_testrun", "assurance_testresult", "assurance_sample",
          "assurance_programmestep", "assurance_finding"]
FROZEN = ["assurance_testrun", "assurance_testresult", "assurance_sample"]
ROLE = "coalesce(current_setting('app.user_role', true), '')"
AUDITOR = f"{ROLE} = 'auditor'"
MANAGEMENT = f"{ROLE} IN ('compliance_officer', 'senior_management')"

SQL = []
for t in PAPERS:
    SQL += [f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;", f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY;",
            f"CREATE POLICY auditor_all ON {t} USING ({AUDITOR}) WITH CHECK ({AUDITOR});"]
for t in ("assurance_engagement", "assurance_finding"):
    SQL.append(f"CREATE POLICY management_reads ON {t} FOR SELECT USING ({MANAGEMENT});")
SQL += [
    "ALTER TABLE assurance_findingresponse ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE assurance_findingresponse FORCE ROW LEVEL SECURITY;",
    f"CREATE POLICY read_responses ON assurance_findingresponse FOR SELECT USING ({AUDITOR} OR {MANAGEMENT});",
    f"CREATE POLICY management_responds ON assurance_findingresponse FOR INSERT WITH CHECK ({MANAGEMENT});",
    f"CREATE POLICY management_updates ON assurance_findingresponse FOR UPDATE USING ({MANAGEMENT}) WITH CHECK ({MANAGEMENT});",
]
for t in FROZEN:
    SQL += [f"CREATE TRIGGER {t}_frozen BEFORE UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION audittrail_append_only();",
            f"CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {t} FOR EACH STATEMENT EXECUTE FUNCTION audittrail_append_only();"]

REVERSE = []
for t in FROZEN:
    REVERSE += [f"DROP TRIGGER IF EXISTS {t}_frozen ON {t};", f"DROP TRIGGER IF EXISTS {t}_no_truncate ON {t};"]
for t in PAPERS + ["assurance_findingresponse"]:
    REVERSE += [f"DROP POLICY IF EXISTS {p} ON {t};" for p in
                ("auditor_all", "management_reads", "read_responses", "management_responds", "management_updates")]
    REVERSE += [f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY;", f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;"]


class Migration(migrations.Migration):
    dependencies = [("assurance", "0001_initial"), ("audittrail", "0002_append_only")]
    operations = [migrations.RunSQL("\n".join(SQL), "\n".join(REVERSE))]
