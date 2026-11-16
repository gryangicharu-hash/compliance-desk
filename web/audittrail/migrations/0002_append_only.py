from django.db import migrations

SQL = """
CREATE FUNCTION audittrail_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP;
END $$;
CREATE TRIGGER auditevent_no_update BEFORE UPDATE OR DELETE ON audittrail_auditevent
    FOR EACH ROW EXECUTE FUNCTION audittrail_append_only();
CREATE TRIGGER auditevent_no_truncate BEFORE TRUNCATE ON audittrail_auditevent
    FOR EACH STATEMENT EXECUTE FUNCTION audittrail_append_only();
"""

REVERSE = """
DROP TRIGGER IF EXISTS auditevent_no_truncate ON audittrail_auditevent;
DROP TRIGGER IF EXISTS auditevent_no_update ON audittrail_auditevent;
DROP FUNCTION IF EXISTS audittrail_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("audittrail", "0001_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
