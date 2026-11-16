-- Checks the audit module's independence safeguards. Run after schema.sql,
-- test_schema.sql (which creates users 1-3) and schema_audit_module.sql.
\set QUIET on
INSERT INTO app_user (username, full_name, role, mfa_enabled) VALUES ('aud', 'Internal Auditor', 'auditor', true);

CREATE OR REPLACE FUNCTION pg_temp.expect_error(sql text, label text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE sql;
    RAISE EXCEPTION 'SAFEGUARD FAILED: % was allowed', label;
EXCEPTION WHEN raise_exception OR check_violation OR insufficient_privilege THEN
    IF SQLERRM LIKE 'SAFEGUARD FAILED%' THEN RAISE; END IF;
    RAISE NOTICE 'ok: % blocked (%)', label, SQLERRM;
END $$;
CREATE OR REPLACE FUNCTION pg_temp.expect_rows(sql text, n int, label text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE got int;
BEGIN
    EXECUTE 'SELECT count(*) FROM (' || sql || ') q' INTO got;
    IF got <> n THEN RAISE EXCEPTION 'SAFEGUARD FAILED: % (expected %, got %)', label, n, got; END IF;
    RAISE NOTICE 'ok: % (% rows)', label, got;
END $$;

SET ROLE compliance_app;

-- the compliance officer cannot open an engagement or plant a passing result
SELECT set_config('app.user_role', 'compliance_officer', false);
SELECT pg_temp.expect_error($$INSERT INTO audit_engagement (ref, kind, period_start, period_end, lead_auditor) VALUES ('E-X','internal_audit','2026-01-01','2026-12-31',1)$$,
                            'compliance officer creating an audit engagement');

-- the auditor does the work
SELECT set_config('app.user_role', 'auditor', false);
INSERT INTO audit_engagement (ref, kind, period_start, period_end, lead_auditor) VALUES ('IA-2026-AML', 'internal_audit', '2026-01-01', '2026-12-31', (SELECT id FROM app_user WHERE username = 'aud'));
INSERT INTO audit_test_run (engagement_id, run_at, data_fingerprint, engine_version, run_by) VALUES ((SELECT id FROM audit_engagement WHERE ref = 'IA-2026-AML'), now(), repeat('f', 64), '0.2.0', (SELECT id FROM app_user WHERE username = 'aud'));
INSERT INTO audit_test_result (run_id, code, title, regulation, severity, population, exceptions, passed) VALUES
  ((SELECT max(id) FROM audit_test_run), 'AT01', 'Active members with incomplete KYC files', 'Regs 12-14', 'high', 1250, '[{"ref":"M0042","detail":"KYC file marked incomplete"}]', false),
  ((SELECT max(id) FROM audit_test_run), 'AT15', 'Audit trail integrity', 'Reg. 37', 'high', 90210, '[]', true);
SELECT pg_temp.expect_error($$INSERT INTO audit_test_result (run_id, code, title, regulation, severity, population, exceptions, passed) VALUES ((SELECT max(id) FROM audit_test_run),'AT02','x','x','low',1,'[{"ref":"M1"}]',true)$$,
                            'a result marked passed while it lists exceptions');
SELECT pg_temp.expect_error($$UPDATE audit_test_result SET passed = true, exceptions = '[]' WHERE code = 'AT01'$$,
                            'rewriting a saved test result (even by the auditor)');
SELECT pg_temp.expect_error($$DELETE FROM audit_test_run$$, 'deleting a saved test run');
INSERT INTO audit_finding (engagement_id, ref, title, regulation, severity, condition, recommendation, test_code)
VALUES ((SELECT id FROM audit_engagement WHERE ref = 'IA-2026-AML'), 'F01', 'Active members with incomplete KYC files', 'Regs 12-14', 'high', '1 exception in 1250', 'Complete the file', 'AT01');
SELECT pg_temp.expect_error($$UPDATE audit_finding SET status = 'closed', closed_on = current_date WHERE ref = 'F01'$$,
                            'closing a finding without evidence');

-- management reads and responds, but cannot soften the finding
SELECT set_config('app.user_role', 'senior_management', false);
SELECT pg_temp.expect_rows($$SELECT * FROM audit_finding$$, 1, 'management can read findings');
SELECT pg_temp.expect_rows($$SELECT * FROM audit_test_result$$, 0, 'management cannot see working papers');
INSERT INTO audit_finding_response (finding_id, response, owner, due_on, responded_by)
VALUES ((SELECT id FROM audit_finding WHERE ref = 'F01'), 'File will be completed this month', 'Head of Operations', '2026-12-15', (SELECT id FROM app_user WHERE username = 'ceo'));
SELECT pg_temp.expect_rows($$SELECT 1 FROM audit_finding WHERE severity = 'high' AND ref = 'F01'$$, 1, 'finding still high after response');
DO $$ BEGIN
  UPDATE audit_finding SET severity = 'low' WHERE ref = 'F01';
  IF FOUND THEN RAISE EXCEPTION 'SAFEGUARD FAILED: management downgraded a finding'; END IF;
  RAISE NOTICE 'ok: management cannot downgrade a finding (0 rows updated)';
END $$;

-- front office sees nothing
SELECT set_config('app.user_role', 'front_office', false);
SELECT pg_temp.expect_rows($$SELECT * FROM audit_finding$$, 0, 'front office sees no findings');
RESET ROLE;
