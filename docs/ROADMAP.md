# Roadmap

## Done

- v0.1.0: compliance engine; PostgreSQL schema with safeguards verified by test scripts.
- v0.2.0: AML audit module (17 automated control tests, sampling, findings and follow-up),
  auditor working-paper tables with independence safeguards, guarantor flags in risk scoring,
  automatic tests on GitHub. 59 engine tests.

- v0.3.0-alpha (web stage 1): Django app with logins, two-factor authentication (RFC 6238,
  replay-protected), lockout, roles, per-request database role for row-level security,
  member onboarding with encrypted ID numbers, risk rating, PEP approval with
  segregation of duties, hash-chained append-only audit trail. 19 web tests.

- v0.4.0-alpha (web stage 2): sanctions and PEP list uploads (UN XML or CSV; versioned,
  bad files rejected and recorded while the list in use stays unchanged); every member
  re-screened when a list loads; screening hold at onboarding; match queue with recorded
  reasons and segregation (the onboarding officer cannot decide); confirmed sanctions match
  blocks the member and opens a case. Suspicious-activity cases: 2-day STR clock, filing
  with FRC reference, closing with reasons, STR XML download, row-level security so front
  office cannot see cases even directly in the database (tipping-off). 32 web tests.

- v0.5.0-alpha (web stage 3): core banking imports (members, loans with guarantors,
  transactions) as CSV in standard layouts or with saved column-name profiles; all-or-nothing
  with row errors, safe to re-send. Guarantor network screen (circular guarantees,
  concentration, links to blocked/PEP/high-risk/unresolved-match members) feeding the risk
  rating. Monitoring alerts from the six rules with review decisions or escalation to a case,
  hidden from front office by row-level security. Weekly large cash report (CSV, FRC reference
  recorded). Scheduled jobs (daily UN list download, nightly import, guarantor refresh,
  monitoring, reminder email) with a run log, and a "Needs attention" list on the overview.
  55 web tests. See docs/OPERATIONS.md.

- v0.6.0-alpha (web stage 4): audit workspace for the auditor role: engagements with the
  30-step programme, the 17 automated control tests run over live data and saved with a
  data fingerprint, reproducible samples, findings drafted from failed tests or raised by
  hand, management responses, closure with evidence, automatic follow-up at the next
  engagement, downloadable report. Independence enforced by PostgreSQL (only auditors
  write working papers; runs and samples frozen; management only reads and responds).
  Governance records (staff AML training, board approval of the institutional risk
  assessment), quarterly board report (counts only), periodic member review. 64 web tests.

## Next: towards a pilot

Built on Django with the engine as a library.

1. Project setup, settings from environment, Django models matching `db/schema.sql`
   (the schema's constraints and triggers kept as a migration).
2. Logins with two-factor authentication; roles: front office, compliance officer,
   senior management, auditor, administrator. Set `app.user_role` on every
   database connection for row-level security.
3. Remaining screens from the prototype: periodic review workflow, risk settings approval.
4. Import profiles for the core banking systems pilot SACCOs actually use (built from
   their real export files).
5. Weekly cash report: confirm the week and nil-return reading with the FRC.
6. Second approval for closing cases and lowering ratings.
7. Pilot hardening: deployment scripts, backups and restore test, penetration test,
   user guide, data-protection impact assessment (Kenya Data Protection Act 2019).

## Open items and owners

| Item | Owner |
| --- | --- |
| goAML XSD mapping for STR and cash reports | Developer, with FRC documentation |
| Weekly cash report deadline reading; STR calendar vs working days | Legal adviser / FRC |
| Kenya domestic sanctions list: source and format | Compliance lead |
| PEP and adverse-media data licence | Business owner |
| Board-approved risk settings for each pilot SACCO | Each SACCO |
| Hosting, backups, monitoring, on-call | Technical owner |
| Penetration test | Independent security firm |
| DPIA and ODPC registration | Business owner, with legal adviser |
