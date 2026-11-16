-- Checks that the database itself enforces the safeguards. Run after schema.sql:
--   psql -v ON_ERROR_STOP=1 -d <db> -f db/test_schema.sql
-- Each block raises an error (and stops the script) if a safeguard does not hold.
\set QUIET on

CREATE OR REPLACE FUNCTION pg_temp.expect_error(sql text, label text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE sql;
    RAISE EXCEPTION 'SAFEGUARD FAILED: % was allowed', label;
EXCEPTION WHEN raise_exception OR check_violation OR insufficient_privilege OR unique_violation THEN
    IF SQLERRM LIKE 'SAFEGUARD FAILED%' THEN RAISE; END IF;
    RAISE NOTICE 'ok: % blocked (%)', label, SQLERRM;
END $$;

INSERT INTO app_user (username, full_name, role, mfa_enabled) VALUES
  ('co', 'Compliance Officer', 'compliance_officer', true),
  ('ceo', 'Chief Executive', 'senior_management', true),
  ('teller', 'Branch Teller', 'front_office', false);
INSERT INTO risk_settings_version VALUES ('v1', '{}', 'Board resolution 12/2026', '2026-10-30', now());
INSERT INTO member (member_no, member_type, name, id_number_enc, id_number_bidx, channel, joined_on, status)
VALUES ('M0001', 'individual', 'Mary Wanjiru Kamau', 'gAAAA-encrypted', repeat('a', 64), 'branch', '2020-03-01', 'active'),
       ('M0002', 'individual', 'Halima Abdi Noor', 'gAAAA-encrypted', repeat('b', 64), 'branch', '2026-11-10', 'pending_approval');
UPDATE member SET pep = 'domestic' WHERE member_no = 'M0002';

SELECT pg_temp.expect_error($$INSERT INTO app_user (username, full_name, role) VALUES ('x','X','compliance_officer')$$,
                            'compliance officer without two-factor login');
SELECT pg_temp.expect_error($$UPDATE member SET status = 'active' WHERE member_no = 'M0002'$$,
                            'activating a PEP without senior management approval');
SELECT pg_temp.expect_error($$UPDATE member SET status = 'exited' WHERE member_no = 'M0001'$$,
                            'exit without an exit date');
SELECT pg_temp.expect_error($$INSERT INTO screening_hit (member_id, subject_name, subject_role, list_name, entry_ref, matched_name, score, dob_check, nationality_check, confidence, status)
    VALUES (1, 'Mary', 'member', 'UN', 'X1', 'Mary K', 0.9, 'n', 'n', 'possible', 'false_positive')$$,
                            'clearing a screening hit without decider and note');
SELECT pg_temp.expect_error($$INSERT INTO aml_case (ref, member_id, case_type, reason, suspicion_formed_at, str_due_at, status, decided_by, decided_at, decision_note)
    VALUES ('C-1', 1, 'x', 'x', now(), now() + interval '2 days', 'str_filed', 1, now(), 'filed')$$,
                            'STR marked filed without an FRC reference');

-- PEP approved with source of wealth: allowed
UPDATE member SET pep_approved_by = 2, pep_approved_at = now(), source_of_wealth = 'Salary and allowances', status = 'active'
 WHERE member_no = 'M0002';

-- tipping-off control: the application role sees cases only for permitted roles
SET ROLE compliance_app;
SELECT set_config('app.user_role', 'compliance_officer', false);
INSERT INTO aml_case (ref, member_id, case_type, reason, suspicion_formed_at, str_due_at)
VALUES ('C-0007', 1, 'unusual_activity', 'Cash far above income', now(), now() + interval '2 days');
DO $$ BEGIN IF (SELECT count(*) FROM aml_case) <> 1 THEN RAISE EXCEPTION 'SAFEGUARD FAILED: compliance officer cannot see case'; END IF; END $$;
SELECT set_config('app.user_role', 'front_office', false);
DO $$ BEGIN IF (SELECT count(*) FROM aml_case) <> 0 THEN RAISE EXCEPTION 'SAFEGUARD FAILED: front office can see cases'; END IF;
  RAISE NOTICE 'ok: front office sees 0 cases'; END $$;
SELECT pg_temp.expect_error($$INSERT INTO aml_case (ref, member_id, case_type, reason, suspicion_formed_at, str_due_at) VALUES ('C-9', 1, 'x', 'x', now(), now() + interval '2 days')$$,
                            'front office creating a case');
SELECT set_config('app.user_role', '', false);
DO $$ BEGIN IF (SELECT count(*) FROM aml_case) <> 0 THEN RAISE EXCEPTION 'SAFEGUARD FAILED: session without role can see cases'; END IF; END $$;
SELECT pg_temp.expect_error($$UPDATE risk_assessment SET score = 1$$, 'application editing risk history');
RESET ROLE;
