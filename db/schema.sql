-- Compliance Desk: PostgreSQL schema (one database per SACCO).
-- Tested on PostgreSQL 16. Amounts in KES. Times are timestamptz.
--
-- Safeguards enforced by the database itself, not only the application:
--   * audit_log is append-only (UPDATE, DELETE and TRUNCATE are blocked by trigger).
--   * screening decisions and case closures must name who decided and why.
--   * case tables use row-level security: front-office sessions see no cases
--     (tipping-off control). The app sets app.user_role on each connection.
--   * identity numbers are stored encrypted, with a blind index for lookups.

BEGIN;

CREATE TABLE app_user (
    id           bigserial PRIMARY KEY,
    username     text NOT NULL UNIQUE,
    full_name    text NOT NULL,
    role         text NOT NULL CHECK (role IN ('front_office','compliance_officer','senior_management','auditor','administrator')),
    mfa_enabled  boolean NOT NULL DEFAULT false,
    is_active    boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT privileged_roles_need_mfa CHECK (NOT is_active OR role = 'front_office' OR mfa_enabled)
);

CREATE TABLE risk_settings_version (
    version         text PRIMARY KEY,
    settings        jsonb NOT NULL,
    approved_by     text NOT NULL,           -- board resolution or approver
    approved_on     date NOT NULL,
    effective_from  timestamptz NOT NULL
);

CREATE TABLE member (
    id                       bigserial PRIMARY KEY,
    member_no                text NOT NULL UNIQUE,
    member_type              text NOT NULL CHECK (member_type IN ('individual','group','corporate')),
    name                     text NOT NULL,
    id_number_enc            text NOT NULL,
    id_number_bidx           char(64) NOT NULL,
    date_of_birth            date,
    nationality              text NOT NULL DEFAULT 'Kenya',
    residence                text NOT NULL DEFAULT 'Kenya',
    occupation               text NOT NULL DEFAULT '',
    employer                 text NOT NULL DEFAULT '',
    checkoff                 boolean NOT NULL DEFAULT false,
    channel                  text NOT NULL CHECK (channel IN ('branch','agent','mobile_app','online')),
    products                 text[] NOT NULL DEFAULT '{bosa_deposits}',
    pep                      text NOT NULL DEFAULT 'none' CHECK (pep IN ('none','domestic','foreign','international_organisation','family_or_close_associate')),
    pep_approved_by          bigint REFERENCES app_user(id),
    pep_approved_at          timestamptz,
    source_of_wealth         text NOT NULL DEFAULT '',
    declared_monthly_income  numeric(18,2) NOT NULL DEFAULT 0 CHECK (declared_monthly_income >= 0),
    expected_cash            text NOT NULL DEFAULT 'low' CHECK (expected_cash IN ('low','medium','high')),
    source_of_funds          text NOT NULL DEFAULT '',
    kyc_complete             boolean NOT NULL DEFAULT false,
    adverse_media            boolean NOT NULL DEFAULT false,
    id_expiry                date,
    joined_on                date NOT NULL,
    last_review_on           date,
    status                   text NOT NULL DEFAULT 'pending_approval'
                             CHECK (status IN ('active','pending_approval','screening_hold','blocked','exited')),
    exited_on                date,
    retention_until          date,
    legal_hold               boolean NOT NULL DEFAULT false,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT exit_date_when_exited CHECK ((status = 'exited') = (exited_on IS NOT NULL)),
    CONSTRAINT pep_approval_complete CHECK ((pep_approved_by IS NULL) = (pep_approved_at IS NULL)),
    CONSTRAINT active_pep_is_approved CHECK (pep = 'none' OR status <> 'active' OR pep_approved_by IS NOT NULL),
    CONSTRAINT active_pep_has_source_of_wealth CHECK (pep = 'none' OR status <> 'active' OR source_of_wealth <> '')
);
CREATE INDEX member_id_bidx ON member (id_number_bidx);
CREATE INDEX member_next_retention ON member (retention_until) WHERE retention_until IS NOT NULL;

CREATE TABLE member_person (
    id              bigserial PRIMARY KEY,
    member_id       bigint NOT NULL REFERENCES member(id),
    kind            text NOT NULL CHECK (kind IN ('official','beneficial_owner')),
    name            text NOT NULL,
    role            text NOT NULL,
    id_number_enc   text,
    date_of_birth   date,
    nationality     text,
    ownership_pct   numeric(5,2) CHECK (ownership_pct BETWEEN 0 AND 100)
);

CREATE TABLE document (
    id           bigserial PRIMARY KEY,
    member_id    bigint NOT NULL REFERENCES member(id),
    doc_type     text NOT NULL,
    storage_key  text NOT NULL,           -- object-store key; the file itself is encrypted at rest
    sha256       char(64) NOT NULL,
    expiry       date,
    uploaded_by  bigint NOT NULL REFERENCES app_user(id),
    uploaded_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE list_version (
    id           bigserial PRIMARY KEY,
    list_name    text NOT NULL,
    source       text NOT NULL,
    fetched_at   timestamptz NOT NULL,
    sha256       char(64) NOT NULL,
    entry_count  integer NOT NULL CHECK (entry_count >= 0),
    status       text NOT NULL CHECK (status IN ('loaded','rejected','current','superseded')),
    error        text
);
CREATE UNIQUE INDEX one_current_version_per_list ON list_version (list_name) WHERE status = 'current';

CREATE TABLE list_entry (
    id               bigserial PRIMARY KEY,
    list_version_id  bigint NOT NULL REFERENCES list_version(id),
    ref              text NOT NULL,
    entry_type       text NOT NULL CHECK (entry_type IN ('individual','entity')),
    primary_name     text NOT NULL,
    aliases          jsonb NOT NULL DEFAULT '[]',
    dates_of_birth   text[] NOT NULL DEFAULT '{}',
    nationalities    text[] NOT NULL DEFAULT '{}',
    listed_on        date,
    UNIQUE (list_version_id, ref)
);

CREATE TABLE screening_hit (
    id                 bigserial PRIMARY KEY,
    member_id          bigint NOT NULL REFERENCES member(id),
    subject_name       text NOT NULL,
    subject_role       text NOT NULL CHECK (subject_role IN ('member','official','beneficial_owner')),
    list_name          text NOT NULL,
    entry_ref          text NOT NULL,
    matched_name       text NOT NULL,
    score              numeric(5,4) NOT NULL CHECK (score BETWEEN 0 AND 1),
    dob_check          text NOT NULL,
    nationality_check  text NOT NULL,
    confidence         text NOT NULL CHECK (confidence IN ('strong','possible')),
    status             text NOT NULL DEFAULT 'to_review' CHECK (status IN ('to_review','false_positive','confirmed')),
    raised_at          timestamptz NOT NULL DEFAULT now(),
    decided_by         bigint REFERENCES app_user(id),
    decided_at         timestamptz,
    decision_note      text,
    UNIQUE (member_id, subject_name, list_name, entry_ref),
    CONSTRAINT decision_recorded CHECK (status = 'to_review'
        OR (decided_by IS NOT NULL AND decided_at IS NOT NULL AND coalesce(decision_note, '') <> ''))
);
CREATE INDEX hits_to_review ON screening_hit (raised_at) WHERE status = 'to_review';

CREATE TABLE risk_assessment (
    id                bigserial PRIMARY KEY,
    member_id         bigint NOT NULL REFERENCES member(id),
    assessed_at       timestamptz NOT NULL DEFAULT now(),
    score             smallint NOT NULL CHECK (score BETWEEN 0 AND 100),
    points            jsonb NOT NULL,
    base_rating       text NOT NULL CHECK (base_rating IN ('low','medium','high')),
    rating            text NOT NULL CHECK (rating IN ('low','medium','high','prohibited')),
    reasons           jsonb NOT NULL DEFAULT '[]',
    overrides         jsonb NOT NULL DEFAULT '[]',
    due_diligence     text NOT NULL CHECK (due_diligence IN ('simplified','standard','enhanced','prohibited')),
    next_review_on    date,
    settings_version  text NOT NULL REFERENCES risk_settings_version(version),
    assessed_by       text NOT NULL       -- user id or 'system'
);
CREATE INDEX risk_latest ON risk_assessment (member_id, assessed_at DESC);

CREATE TABLE guarantee (
    loan_id       text NOT NULL,
    borrower_id   bigint NOT NULL REFERENCES member(id),
    guarantor_id  bigint NOT NULL REFERENCES member(id),
    amount        numeric(18,2) NOT NULL CHECK (amount > 0),
    start_on      date NOT NULL,
    released_on   date,
    PRIMARY KEY (loan_id, guarantor_id)
);
CREATE INDEX guarantee_guarantor ON guarantee (guarantor_id) WHERE released_on IS NULL;

CREATE TABLE txn (
    txn_id        text PRIMARY KEY,
    member_id     bigint NOT NULL REFERENCES member(id),
    ts            timestamptz NOT NULL,
    amount        numeric(18,2) NOT NULL CHECK (amount > 0),
    direction     text NOT NULL CHECK (direction IN ('in','out')),
    channel       text NOT NULL CHECK (channel IN ('cash','mobile_money','bank_transfer','checkoff','internal')),
    counterparty  text,
    purpose       text NOT NULL DEFAULT ''
);
CREATE INDEX txn_member_time ON txn (member_id, ts);

CREATE TABLE aml_case (
    id                   bigserial PRIMARY KEY,
    ref                  text NOT NULL UNIQUE,
    member_id            bigint NOT NULL REFERENCES member(id),
    case_type            text NOT NULL,
    reason               text NOT NULL,
    suspicion_formed_at  timestamptz NOT NULL,
    str_due_at           timestamptz NOT NULL,
    status               text NOT NULL DEFAULT 'open' CHECK (status IN ('open','str_filed','closed_not_suspicious')),
    decided_by           bigint REFERENCES app_user(id),
    decided_at           timestamptz,
    decision_note        text,
    frc_reference        text,
    CONSTRAINT due_after_suspicion CHECK (str_due_at > suspicion_formed_at),
    CONSTRAINT decision_recorded CHECK (status = 'open'
        OR (decided_by IS NOT NULL AND decided_at IS NOT NULL AND coalesce(decision_note, '') <> '')),
    CONSTRAINT filed_has_reference CHECK (status <> 'str_filed' OR coalesce(frc_reference, '') <> '')
);

CREATE TABLE alert (
    id               bigserial PRIMARY KEY,
    rule             text NOT NULL,
    member_id        bigint NOT NULL REFERENCES member(id),
    txn_ids          text[] NOT NULL,
    description      text NOT NULL,
    reportable_only  boolean NOT NULL DEFAULT false,
    raised_at        timestamptz NOT NULL DEFAULT now(),
    status           text NOT NULL DEFAULT 'new' CHECK (status IN ('new','escalated','dismissed')),
    case_id          bigint REFERENCES aml_case(id),
    UNIQUE (rule, member_id, txn_ids),
    CONSTRAINT escalated_has_case CHECK (status <> 'escalated' OR case_id IS NOT NULL)
);

CREATE TABLE cash_report (
    id             bigserial PRIMARY KEY,
    period_start   date NOT NULL,
    period_end     date NOT NULL CHECK (period_end >= period_start),
    due_on         date NOT NULL,
    txn_count      integer NOT NULL CHECK (txn_count >= 0),
    filed_at       timestamptz,
    filed_by       bigint REFERENCES app_user(id),
    frc_reference  text,
    UNIQUE (period_start, period_end)
);

CREATE TABLE training_record (
    id            bigserial PRIMARY KEY,
    staff_name    text NOT NULL,
    user_id       bigint REFERENCES app_user(id),
    course        text NOT NULL,
    completed_on  date NOT NULL,
    valid_until   date NOT NULL CHECK (valid_until > completed_on)
);

-- ---------- append-only, hash-chained audit trail ----------
CREATE TABLE audit_log (
    seq        bigint PRIMARY KEY,
    at         timestamptz NOT NULL,
    actor      text NOT NULL,
    action     text NOT NULL,
    subject    text NOT NULL,
    detail     jsonb NOT NULL DEFAULT '{}',
    prev_hash  char(64) NOT NULL,
    hash       char(64) NOT NULL UNIQUE
);

CREATE FUNCTION audit_log_is_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP;
END $$;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_is_append_only();

CREATE FUNCTION audit_log_chain_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE last audit_log%ROWTYPE;
BEGIN
    SELECT * INTO last FROM audit_log ORDER BY seq DESC LIMIT 1;
    IF last.seq IS NULL THEN
        IF NEW.seq <> 1 OR NEW.prev_hash <> repeat('0', 64) THEN
            RAISE EXCEPTION 'first audit entry must have seq 1 and the genesis hash';
        END IF;
    ELSIF NEW.seq <> last.seq + 1 OR NEW.prev_hash <> last.hash THEN
        RAISE EXCEPTION 'audit entry % does not follow entry %', NEW.seq, last.seq;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER audit_log_chain BEFORE INSERT ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_chain_check();

-- ---------- tipping-off control: case data hidden from front office ----------
CREATE FUNCTION app_can_see_cases() RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT coalesce(current_setting('app.user_role', true), '')
           IN ('compliance_officer','senior_management','auditor')
$$;
ALTER TABLE aml_case ENABLE ROW LEVEL SECURITY;
ALTER TABLE aml_case FORCE ROW LEVEL SECURITY;
CREATE POLICY case_visibility ON aml_case USING (app_can_see_cases()) WITH CHECK (app_can_see_cases());
ALTER TABLE alert ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert FORCE ROW LEVEL SECURITY;
CREATE POLICY alert_visibility ON alert USING (app_can_see_cases()) WITH CHECK (app_can_see_cases());

-- ---------- least-privilege role for the application ----------
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'compliance_app') THEN
        CREATE ROLE compliance_app LOGIN;
    END IF;
END $$;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO compliance_app;
REVOKE UPDATE ON audit_log FROM compliance_app;
REVOKE UPDATE ON risk_assessment FROM compliance_app;      -- history is kept, never edited
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO compliance_app;

COMMIT;
