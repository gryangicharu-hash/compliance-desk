# Compliance Desk

AML/CFT compliance platform for Kenyan deposit-taking SACCOs, built around the
Proceeds of Crime and Anti-Money Laundering Act (POCAMLA) and its Regulations
(2023 amendments).

**Status: v0.6.0-alpha.** Engine, AML audit module and database are built and tested. The web
application (`web/`, Django) has stage 1: logins with two-factor authentication, roles,
member onboarding with risk rating, approval of PEPs, and the audit trail. Stage 2 adds
sanctions and PEP screening (list uploads, match queue, decisions) and suspicious-activity
cases (2-day STR clock, filing, STR XML, hidden from front office). Stage 3 adds core
banking imports, the guarantor network screen, transaction monitoring alerts, the weekly
large cash report and scheduled jobs (see [docs/OPERATIONS.md](docs/OPERATIONS.md)). Stage 4
adds the auditor's workspace (control tests, samples, programme, findings, follow-up,
report) with database-enforced independence, governance records and the quarterly board
report. Pilot hardening comes next; see [docs/ROADMAP.md](docs/ROADMAP.md).

## What is here

| Path | Contents |
| --- | --- |
| `src/compliance_core/` | The compliance engine: pure Python, no web framework |
| `tests/` | 59 automated tests (pytest) |
| `db/schema.sql` | PostgreSQL 16 schema with database-enforced safeguards |
| `db/schema_audit_module.sql` | Auditors' working papers, with independence safeguards |
| `db/test_*.sql`, `db/test_audit_chain.py` | Checks that the safeguards hold |
| `.github/workflows/tests.yml` | Runs all tests automatically on GitHub |
| `docs/` | Architecture, decisions, regulation map, security, roadmap |

## Engine modules

| Module | Does |
| --- | --- |
| `matching` | Name similarity (Jaro-Winkler, reordered names, word-by-word, initials) |
| `lists` | Imports the UN consolidated sanctions list (XML) and CSV lists; compares list versions |
| `screening` | Screens members, group officials and beneficial owners; uses date of birth and nationality to set confidence |
| `risk` | Five-factor risk score, override rules, due diligence level, next review date |
| `guarantors` | Guarantor network red flags: circular guarantees of any length up to 4, concentration, links to blocked members |
| `monitoring` | Six transaction rules: large cash, structuring, income mismatch, rapid in-out, dormant reactivation, many payers |
| `deadlines` | 2-day STR clock, weekly cash report date, 7-year retention, ID expiry |
| `reports` | Weekly cash report (CSV), STR (goAML-shaped XML), board summary |
| `audit` | Hash-chained, tamper-evident audit trail |
| `crypto` | Encryption of ID numbers with key rotation, and blind indexes for lookups |
| `assurance` | AML audit: 17 automated control tests tied to the Regulations, reproducible sampling, findings, follow-up, audit report (see [docs/AUDIT_PROGRAMME.md](docs/AUDIT_PROGRAMME.md)) |

## Running the tests

Requirements: Python 3.11+, `cryptography`, `pytest`; PostgreSQL 16 for the database checks.

```bash
python -m pip install -e ".[test]"
python -m pytest                                   # engine: 59 tests

createdb compliance_test
psql -v ON_ERROR_STOP=1 -d compliance_test -f db/schema.sql
psql -v ON_ERROR_STOP=1 -d compliance_test -f db/test_schema.sql        # expect "ok:" lines only
psql -v ON_ERROR_STOP=1 -d compliance_test -f db/schema_audit_module.sql
psql -v ON_ERROR_STOP=1 -d compliance_test -f db/test_audit_module.sql
python db/test_audit_chain.py | psql -v ON_ERROR_STOP=1 -d compliance_test
```

## Running the web application (development)

```bash
python -m pip install -e . -r web/requirements.txt
export DJANGO_DEBUG=1 DJANGO_SECRET_KEY=dev-only-change-me PGDATABASE=compliance_desk PGUSER=... PGPASSWORD=...
export FIELD_ENCRYPTION_KEYS=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export BLIND_INDEX_KEY=dev-only-blind-index-key-at-least-32-chars
cd web && python manage.py migrate && python manage.py createsuperuser && python manage.py runserver
```

Keep the encryption keys safe: data encrypted with a lost key cannot be recovered.
Every change pushed to GitHub runs the engine, database and web tests automatically.

## Before any production use

These items are open. They are listed with owners in [docs/ROADMAP.md](docs/ROADMAP.md).

1. Map the STR XML to the FRC's official goAML schema and validate against it.
2. Confirm with the FRC how the weekly cash report deadline is counted, and with legal advice whether the 2-day STR deadline is calendar or working days.
3. Have the SACCO's board approve the risk settings (weights, bands, review cycles, high-risk lists).
4. Independent penetration test and code review.
5. Data protection impact assessment and registration with the ODPC.

All sample names in tests are fictitious.
