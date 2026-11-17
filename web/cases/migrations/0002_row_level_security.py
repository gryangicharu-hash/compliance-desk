from django.db import migrations

ROLES = "('compliance_officer','senior_management','auditor','system')"

SQL = f"""
ALTER TABLE cases_case ENABLE ROW LEVEL SECURITY;
ALTER TABLE cases_case FORCE ROW LEVEL SECURITY;
CREATE POLICY case_visibility ON cases_case
    USING (coalesce(current_setting('app.user_role', true), '') IN {ROLES})
    WITH CHECK (coalesce(current_setting('app.user_role', true), '') IN {ROLES});
"""

REVERSE = """
DROP POLICY IF EXISTS case_visibility ON cases_case;
ALTER TABLE cases_case NO FORCE ROW LEVEL SECURITY;
ALTER TABLE cases_case DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("cases", "0001_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
