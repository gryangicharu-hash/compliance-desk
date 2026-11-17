# Installing Compliance Desk for a pilot

One Linux server (4 CPU, 8 GB RAM, 100 GB disk is ample for a SACCO of 50,000 members)
running Docker, with a reverse proxy for HTTPS. Hosting must meet the Data Protection
Act 2019 rules on where personal data is kept (see docs/DPIA.md); a Kenyan data centre
avoids the cross-border transfer questions.

## 1. Install

```bash
sudo mkdir -p /srv/compliance-desk && cd /srv/compliance-desk
git clone <repository> .
cp .env.example .env && chmod 600 .env
# fill in .env: secrets (commands are in the file), host name, SMTP, institution name
docker compose up -d --build
docker compose run --rm web manage createsuperuser      # the first administrator
```

Point the reverse proxy at `127.0.0.1:8000` (example: `deploy/nginx.conf`, with a
Let's Encrypt certificate). `https://<host>/healthz` returns `ok` when the app and
database are up; use it for uptime monitoring.

The administrator logs in, sets up two-factor login, and creates staff accounts under
**Staff**. Each person gets a one-time password and must choose their own at first login.

## 2. Keys: keep them safe

`FIELD_ENCRYPTION_KEYS` encrypts ID numbers and two-factor secrets; `BLIND_INDEX_KEY`
makes ID numbers searchable. **If either is lost, encrypted data in the database and in
every backup can't be read.** Keep a copy in the SACCO's safe or password manager,
separately from the backups. To rotate the encryption key, put the new key first:
`FIELD_ENCRYPTION_KEYS=<new>,<old>`; old values stay readable.

## 3. Scheduled jobs and backups

```bash
sudo cp deploy/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now compliance-desk-jobs@fetch_un_list.timer \
     compliance-desk-jobs@nightly.timer compliance-desk-backup.timer
```

| Time (Nairobi) | What |
| --- | --- |
| 00:45 | Database backup to `BACKUP_DIR_HOST` (default `./backups`) |
| 01:30 | Nightly: imports, guarantor network, monitoring, reminder email |
| 05:15 | UN sanctions list check |

Job runs show on **Data and jobs → Scheduled jobs**; see docs/OPERATIONS.md.

**Backups must leave the server.** Copy `./backups` nightly to separate storage (another
site, or an encrypted cloud bucket in an approved region), for example with `rclone` or
`restic`. Backups contain personal data: encrypt them in transit and at rest. ID numbers
inside are already encrypted with the field key, which is not in the backup.

The application's own database login can't take a complete backup (row-level security
hides cases, alerts and audit papers from it), which is why the backup runs as the
database superuser inside the database container.

## 4. Test the restore (monthly, and before go-live)

```bash
docker compose exec -T -e PGUSER=postgres db bash -c \
  'createdb -O compliance_app restore_test && pg_restore --exit-on-error -d restore_test /backups/<file>.dump'
docker compose run --rm -e PGDATABASE=restore_test web manage verify_database
docker compose exec -T -e PGUSER=postgres db dropdb restore_test
```

`verify_database` checks every migration is present, the audit trail's hash chain is
intact, and prints row counts. Record the date and result; inspectors and auditors ask
for evidence that restores work. The same test runs automatically on every code change
(GitHub Actions, "Backup and restore test").

## 5. Training and demonstrations

On a separate, empty installation (never the live one):

```bash
docker compose run --rm web manage seed_demo --yes-this-is-a-demo --with-users
```

This loads about 150 fictitious members with loans, guarantors and transactions,
including a sanctions near-match, a circular guarantee, structuring, large cash, rapid
in-and-out and many-payer patterns, and prints one-time passwords for demo staff in each
role. It refuses to run on a database that already has members.

## 6. Updating

```bash
git pull && docker compose up -d --build      # migrations run automatically at start
```

Take a backup first. Read the release notes for any change to risk scoring: risk
settings changes need board approval before they are used (reg. 6).

## 7. Before real member data

See docs/SECURITY.md ("Required before real member data") and docs/DPIA.md. In
particular: an independent penetration test, the DPIA signed off, ODPC registration,
off-site encrypted backups with a tested restore, and named people for incident response.

Production hardening not yet in the pilot set-up: a separate migration role so the
runtime login does not own the tables (it could otherwise drop the append-only
triggers); centralised log shipping; and a web application firewall if the app is
reachable from the internet rather than a VPN.
