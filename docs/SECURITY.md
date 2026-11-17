# Security

## In place

- **Identity numbers encrypted** in the database (Fernet via `crypto.FieldCipher`), with key
  rotation and an HMAC blind index for lookups. Keys live in the deployment's secret
  store, never in code or the database.
- **Append-only audit log**, enforced by database triggers and independently by a SHA-256
  hash chain (`audit.verify()` finds the first altered entry).
- **Tipping-off control**: row-level security hides cases and alerts from front-office sessions.
- **Least privilege**: the application role cannot update the audit log or risk history.
- **Two-factor login required** for every role except front office (database constraint).
- **Untrusted list files**: a malformed list is rejected, never loaded as empty.
  Python's XML parser does not resolve external entities; production should also
  cap download size and verify the source over HTTPS.

- **Staff accounts**: created by an administrator with a one-time password that must be
  changed at first login; deactivated, never deleted; every change audited. Passwords at
  least 12 characters and checked against common passwords.
- **Login lockout** shared across app processes (database cache).
- **Two-factor setup QR code** drawn on the server; the secret never goes to a third party.
- **Auditor independence**: audit working papers writable only by the auditor role; saved
  test runs and samples frozen (database row-level security and triggers).
- **Automated checks on every change**: Django's production settings check, known
  vulnerabilities in installed packages (pip-audit), the image build, and a full backup
  and restore test with the audit trail verified after restore.
- **Containers**: the app runs as a non-root user; the database login it uses is not a
  superuser (so row-level security applies) and never sees the superuser password.

## Required before real member data

1. Independent penetration test and secure code review.
2. Data protection impact assessment; ODPC registration (SACCO as controller, operator as processor).
3. Hosting decision (Kenya or approved cloud region); backups copied off-site and encrypted;
   restore tested and recorded (docs/DEPLOYMENT.md).
4. Document storage encrypted at rest, with SHA-256 recorded per file (column exists).
5. Rate limiting at the reverse proxy (login page) if the app is reachable from the internet.
6. Monitoring and alerting on failed list imports, failed jobs and unusual access.
7. Incident and breach response plan, including ODPC notification timelines.

## Reporting a vulnerability

Set a contact address here before go-live.
