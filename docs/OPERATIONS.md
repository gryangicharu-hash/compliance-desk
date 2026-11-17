# Operations: scheduled jobs and core banking imports

## Jobs

All jobs run through one command, as the server user that runs the app, with the
same environment variables:

```
python manage.py runjobs fetch_un_list     # daily
python manage.py runjobs nightly           # import_inbox, refresh_guarantors, run_monitoring, send_reminders
```

Each run is recorded on the **Data and jobs → Scheduled jobs** page and in the audit
trail. A job already running is not started again (PostgreSQL advisory lock). The
command exits non-zero when a job fails, so the scheduler's own alerting can pick it up;
the overview page also shows "The last … job failed".

Example crontab (server time zone Africa/Nairobi):

```
15 5 * * *  cd /srv/compliance-desk/web && ./venv/bin/python manage.py runjobs fetch_un_list
30 1 * * *  cd /srv/compliance-desk/web && ./venv/bin/python manage.py runjobs nightly
```

| Job | What it does |
| --- | --- |
| `fetch_un_list` | Downloads `UN_LIST_URL` (default: the UN Security Council consolidated list XML). If the file changed it is loaded as a new version and every member is re-screened; if it can't be read it is recorded as rejected and the list in use is kept. |
| `import_inbox` | Imports CSV files from `IMPORT_INBOX` (see below), members first, then loans, then transactions. |
| `refresh_guarantors` | Recalculates guarantor network flags (circular guarantees, concentration, links to blocked, PEP, high-risk or unresolved-match members) and re-assesses members whose flags changed. |
| `run_monitoring` | Runs the six monitoring rules for members with transactions in the last 31 days. Alerts already raised are not raised again; rolling 30-day rules stay quiet for 30 days after an alert. |
| `send_reminders` | Emails active compliance officers who have an email address a list of counts (overdue STRs, cash report due, alerts, matches, reviews, IDs). No names or case details are put in email. |

`fetch_un_list --source <url or file>` loads from a mirror or a downloaded copy instead.

## Settings

| Variable | Default | Notes |
| --- | --- | --- |
| `UN_LIST_URL` | `https://scsanctions.un.org/resources/xml/en/consolidated.xml` | Confirm against the UN Security Council website before go-live. The server needs outbound HTTPS to it. |
| `IMPORT_INBOX` | empty (no automatic imports) | Folder the core banking system or SFTP drop writes to. |
| `USD_KES_RATE` | `129.00` | Used for the USD 15,000 large cash threshold. Keep in line with the CBK rate. |
| `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL` | console backend | Set the SMTP backend to send reminders. |

## Import folder

```
<IMPORT_INBOX>/members/*.csv                 standard layout
<IMPORT_INBOX>/members/<profile name>/*.csv  using a saved column-name profile
<IMPORT_INBOX>/loans/...
<IMPORT_INBOX>/transactions/...
```

After each run files move to `processed/<date>/` or `rejected/<date>/`; the reason for
a rejection is on the import's page. A file with any bad row is rejected as a whole.
Sending the same records again is safe: unchanged rows are skipped, and the exact same
file is refused as already imported. A loans file is the current picture of each loan
in it: guarantors missing from it are treated as released.

Layouts are listed on the **Data and jobs** page. Members imported this way are existing
members: they stay active (unless a sanctions match puts them on hold) and are rated
straight away. If `kyc_complete` is not sent, their KYC file is treated as incomplete
(at least medium risk) until checked, which is the cautious reading for a go-live.

## Weekly large cash report (reg. 35)

Cash transactions at or above USD 15,000 go on the **Monitoring → Weekly large cash
reports** page, grouped in Saturday-to-Friday weeks due on the Friday. Download the CSV,
submit it through goAML, then record the FRC's reference. The week reading and whether
a nil return is needed are open items for the legal adviser (see ROADMAP).

## Holds and freezes in core banking

A member's status here decides what core banking must do with their accounts:

| Status here | Instruction (`restriction`) | Core banking should |
| --- | --- | --- |
| Active | `none` | Lift any compliance restriction |
| Awaiting approval | `no_new_business` | Open no new products or loans until approved |
| Screening hold | `hold` | Allow no transactions |
| Blocked (confirmed sanctions match) | `freeze` | Allow no funds to move in or out |
| Exited | `closed` | Close the membership |

Instructions say what to do, never why (tipping-off). They reach core banking in one or
both of these ways:

**Web link** (`CORE_BANKING_WEBHOOK_URL`, `CORE_BANKING_WEBHOOK_SECRET`). Each change is
sent as it happens, oldest first:

```
POST <CORE_BANKING_WEBHOOK_URL>
Content-Type: application/json
X-Compliance-Desk-Delivery: 1234
X-Compliance-Desk-Signature: sha256=<hex HMAC-SHA256 of the raw body with the shared secret>

{"event":"restriction.changed","id":1234,"member_no":"M000123","restriction":"freeze","previous":"hold","at":"2026-11-17T14:02:11+03:00"}
```

The receiver must check the signature, apply the instruction, and answer 2xx. It should
treat `id` as the delivery number and ignore an `id` it has already applied (deliveries can
repeat after a timeout). Anything else is retried by the `sync_restrictions` job, which
should run every 15 minutes (`deploy/systemd/compliance-desk-jobs@sync_restrictions.timer`).
Delivery stops at the first failure so instructions never arrive out of order; after 30
minutes the overview shows "Restriction changes have not reached core banking".

**File** (`EXPORT_OUTBOX`). Each `sync_restrictions` run writes the full current list,
`restrictions-<date>-<time>.csv` with a `.sha256` file beside it:
`member_no,name,restriction,restriction_text,since`. Members not in the file have no
compliance restriction. The list can also be downloaded from **Holds in core banking**.

**Confirmation back from core banking.** Core banking sends its own list of accounts it
currently restricts on compliance instruction, daily or more often, as a "Restricted
accounts" import (by hand or in `<IMPORT_INBOX>/restricted_accounts/`):
`member_no` (required), `restriction`, `as_at`. Send the full list every time; a file with
only the header means nothing is restricted. The **Holds in core banking** page and the
overview then show:

- members held or frozen here before the list was taken but **not restricted in core
  banking** (overdue: ask core banking operations to apply it at once);
- members released here but still restricted there;
- members waiting for the next list;
- accounts restricted in core banking for other reasons (for information).

## KYC documents

Stored encrypted under `DOCUMENT_ROOT` with the same keys as ID numbers; the sha256 of
each original is recorded and checked whenever the file is opened, and every opening is in
the audit trail. Only PDF, JPEG and PNG files are accepted, recognised by their content.
Replaced documents are kept, marked as replaced. The nightly backup archives the folder
next to the database dump (`compliance-desk-<date>.documents.tar`); restore it into the
documents volume together with the database.
