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
