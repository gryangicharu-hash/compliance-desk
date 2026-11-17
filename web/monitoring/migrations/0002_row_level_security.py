from django.db import migrations

# Alerts are suspicion information: like cases, front office must not see them (tipping-off).
ROLES = "('compliance_officer','senior_management','auditor','system')"

SQL = f"""
ALTER TABLE monitoring_alert ENABLE ROW LEVEL SECURITY;
ALTER TABLE monitoring_alert FORCE ROW LEVEL SECURITY;
CREATE POLICY alert_visibility ON monitoring_alert
    USING (coalesce(current_setting('app.user_role', true), '') IN {ROLES})
    WITH CHECK (coalesce(current_setting('app.user_role', true), '') IN {ROLES});
"""

REVERSE = """
DROP POLICY IF EXISTS alert_visibility ON monitoring_alert;
ALTER TABLE monitoring_alert NO FORCE ROW LEVEL SECURITY;
ALTER TABLE monitoring_alert DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("monitoring", "0001_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
