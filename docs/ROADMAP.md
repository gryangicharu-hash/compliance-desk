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

## Next: web application, remaining stages

Built on Django with the engine as a library.

1. Project setup, settings from environment, Django models matching `db/schema.sql`
   (the schema's constraints and triggers kept as a migration).
2. Logins with two-factor authentication; roles: front office, compliance officer,
   senior management, auditor, administrator. Set `app.user_role` on every
   database connection for row-level security.
3. Screens from the prototype: overview, members, onboarding, screening queue,
   guarantors, reviews, cases and reports, audit trail, risk settings.
4. Scheduled jobs: daily UN list download and re-screen; nightly transaction import
   and monitoring; weekly cash report reminder; review and ID-expiry reminders.
5. Imports: member and transaction files from the main core banking systems used by pilot SACCOs.
6. Second approval for closing cases and lowering ratings.
7. Board report screen from `reports.board_summary()`.
8. Audit workspace for the auditor role: engagements, test runs, samples, programme, findings, report.

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
