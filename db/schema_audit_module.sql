-- AML audit module: auditors' working papers. Load after schema.sql.
-- Independence safeguards enforced by the database:
--   * only sessions with app.user_role = 'auditor' can create engagements, runs,
--     samples and findings, or edit them;
--   * saved test runs and their exceptions can never be changed or deleted;
--   * management (compliance officer, senior management) can read findings and
--     record responses, but cannot change the finding itself.

BEGIN;

CREATE FUNCTION app_is_auditor() RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT coalesce(current_setting('app.user_role', true), '') = 'auditor'
$$;
CREATE FUNCTION app_is_management() RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT coalesce(current_setting('app.user_role', true), '') IN ('compliance_officer','senior_management')
$$;

CREATE TABLE audit_engagement (
    id            bigserial PRIMARY KEY,
    ref           text NOT NULL UNIQUE,
    kind          text NOT NULL CHECK (kind IN ('internal_audit','external_audit','inspection_preparation')),
    period_start  date NOT NULL,
    period_end    date NOT NULL CHECK (period_end >= period_start),
    lead_auditor  bigint NOT NULL REFERENCES app_user(id),
    status        text NOT NULL DEFAULT 'planning' CHECK (status IN ('planning','fieldwork','reporting','closed')),
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_test_run (
    id                bigserial PRIMARY KEY,
    engagement_id     bigint NOT NULL REFERENCES audit_engagement(id),
    run_at            timestamptz NOT NULL,
    data_fingerprint  char(64) NOT NULL,
    engine_version    text NOT NULL,
    run_by            bigint NOT NULL REFERENCES app_user(id)
);

CREATE TABLE audit_test_result (
    id           bigserial PRIMARY KEY,
    run_id       bigint NOT NULL REFERENCES audit_test_run(id),
    code         text NOT NULL,
    title        text NOT NULL,
    regulation   text NOT NULL,
    severity     text NOT NULL CHECK (severity IN ('high','medium','low')),
    population   integer NOT NULL CHECK (population >= 0),
    exceptions   jsonb NOT NULL DEFAULT '[]',
    passed       boolean NOT NULL,
    UNIQUE (run_id, code),
    CONSTRAINT passed_matches_exceptions CHECK (passed = (jsonb_array_length(exceptions) = 0))
);

CREATE TABLE audit_sample (
    id               bigserial PRIMARY KEY,
    engagement_id    bigint NOT NULL REFERENCES audit_engagement(id),
    method           text NOT NULL,
    seed             bigint NOT NULL,
    population_size  integer NOT NULL,
    selected         text[] NOT NULL,
    created_by       bigint NOT NULL REFERENCES app_user(id),
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_programme_step (
    id             bigserial PRIMARY KEY,
    engagement_id  bigint NOT NULL REFERENCES audit_engagement(id),
    step_no        integer NOT NULL,
    regulation     text NOT NULL,
    procedure      text NOT NULL,
    test_code      text,
    result         text CHECK (result IN ('satisfactory','exception','not_applicable')),
    workpaper_ref  text,
    notes          text,
    UNIQUE (engagement_id, step_no)
);

CREATE TABLE audit_finding (
    id                   bigserial PRIMARY KEY,
    engagement_id        bigint NOT NULL REFERENCES audit_engagement(id),
    ref                  text NOT NULL,
    title                text NOT NULL,
    regulation           text NOT NULL,
    severity             text NOT NULL CHECK (severity IN ('high','medium','low')),
    condition            text NOT NULL,
    recommendation       text NOT NULL,
    test_code            text,
    status               text NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','closed','risk_accepted')),
    closed_on            date,
    closure_evidence     text,
    UNIQUE (engagement_id, ref),
    CONSTRAINT closed_needs_evidence CHECK (status <> 'closed'
        OR (closed_on IS NOT NULL AND coalesce(closure_evidence, '') <> ''))
);

-- management's side of a finding lives in its own table, so management can answer
-- without being able to edit what the auditor wrote
CREATE TABLE audit_finding_response (
    finding_id    bigint PRIMARY KEY REFERENCES audit_finding(id),
    response      text NOT NULL,
    owner         text NOT NULL,
    due_on        date NOT NULL,
    responded_by  bigint NOT NULL REFERENCES app_user(id),
    responded_at  timestamptz NOT NULL DEFAULT now()
);

-- saved runs and their results are permanent
CREATE TRIGGER audit_test_run_frozen BEFORE UPDATE OR DELETE ON audit_test_run
    FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();
CREATE TRIGGER audit_test_result_frozen BEFORE UPDATE OR DELETE ON audit_test_result
    FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();
CREATE TRIGGER audit_sample_frozen BEFORE UPDATE OR DELETE ON audit_sample
    FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();

-- row-level security: auditors write; management reads findings and responds
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['audit_engagement','audit_test_run','audit_test_result','audit_sample',
                             'audit_programme_step','audit_finding'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY auditor_all ON %I USING (app_is_auditor()) WITH CHECK (app_is_auditor())', t);
    END LOOP;
END $$;
CREATE POLICY management_reads ON audit_finding FOR SELECT USING (app_is_management());
CREATE POLICY management_reads ON audit_engagement FOR SELECT USING (app_is_management());

ALTER TABLE audit_finding_response ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_finding_response FORCE ROW LEVEL SECURITY;
CREATE POLICY read_responses ON audit_finding_response FOR SELECT USING (app_is_auditor() OR app_is_management());
CREATE POLICY management_responds ON audit_finding_response FOR INSERT WITH CHECK (app_is_management());
CREATE POLICY management_updates ON audit_finding_response FOR UPDATE USING (app_is_management()) WITH CHECK (app_is_management());

GRANT SELECT, INSERT, UPDATE ON audit_engagement, audit_test_run, audit_test_result, audit_sample,
      audit_programme_step, audit_finding, audit_finding_response TO compliance_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO compliance_app;

COMMIT;
