# Compliance Desk user guide

What each role does, step by step. Everything you do is written to the audit trail with
your name and the time.

## Logging in

1. Go to the address your administrator gave you and log in.
2. **First time:** choose your own password (at least 12 characters, not a common one).
3. **Everyone except front office** also sets up two-factor login: scan the QR code with
   an authenticator app (Google Authenticator, Microsoft Authenticator or similar) and
   enter the 6-digit code. After that you enter a fresh code at every login.
4. Five wrong passwords lock the account for 15 minutes. Sessions end after 30 minutes
   without activity.

Lost your phone? Ask the administrator to **Reset two-factor**; you set it up again at
your next login.

## Front office (tellers, member services)

**Onboard a member** (*Overview → Onboard a member*)
1. Enter the member's details. For a group or company, list the officials and the
   beneficial owners (anyone owning 10% or more, or who controls it).
2. Tick *All KYC documents received and checked* only when you have them.
3. Save. The member is screened and rated at once:
   - **Active**: low or medium risk; the account can be used.
   - **Awaiting approval**: a PEP or high risk; senior management must approve first.
   - **Screening hold**: a possible sanctions match. Do not process any transaction.
     **Don't tell the member why**; refer them to the compliance officer.

**Never discuss with a member** any suspicion, case or alert. Telling them (tipping-off)
is an offence under POCAMLA. You will see "Additional compliance factors (restricted)"
on some members: that is deliberate.

## Compliance officer

Start each day on **Overview → Needs attention**. It lists, most urgent first: STR
deadlines, the weekly cash report, alerts and matches to review, overdue member reviews,
expiring IDs, and any failed scheduled job.

**Screening matches** (*Screening*)
- For each match compare date of birth, nationality and ID with the list entry.
- Record *False positive* or *Confirmed match* with your reason. You can't decide a
  match for a member you onboarded yourself.
- A confirmed sanctions match blocks the member and opens a case: freeze funds and
  report to the FRC without delay.

**Monitoring alerts** (*Monitoring*)
- Open the alert, look at the transactions and the member's profile.
- Either *No further action* (say what you checked) or *Open a case*.

**Cases and STRs** (*Cases*): an STR is due within 2 days of the suspicion arising
(reg. 33); the clock shows hours left.
- *Download STR draft (XML)*, complete and submit it in goAML, then *File* with the
  FRC's reference.
- Or close as not suspicious, with the reason. Sanctions cases can't be closed this way.

**Weekly large cash report** (*Monitoring → Weekly large cash reports*): every cash
transaction of USD 15,000 or more, in Saturday-to-Friday weeks due on Friday. Download
the CSV, submit through goAML, then record the FRC reference.

**Periodic reviews**: on the member's page, record the review with what you checked. The
next review date follows the member's risk rating.

**Sanctions lists** (*Screening → Lists*): the UN list is checked automatically every
morning. You can also load a list by hand (UN XML, or a CSV such as a PEP list). A file
that can't be read is rejected and the list in use stays as it was.

**Core banking data** (*Data and jobs*): import members first, then loans with
guarantors, then transactions. A file with any problem is rejected as a whole; the
import page lists the rows to fix.

**Guarantors** (*Guarantors*): circular guarantees, members guaranteeing many loans, and
guarantees involving blocked, high-risk or PEP members. Flags raise the risk rating.

**Records** (*Audit → Training and risk assessment records*): staff AML training, and the
board's approval of the institutional risk assessment (at least every two years).

**Audit findings**: open the engagement under *Audit* and record management's response,
the person responsible and the agreed date for each finding.

## Senior management

- **Approve** PEPs and high-risk members from the member's page (reg. 22). A PEP needs a
  recorded source of wealth first. You can't approve a member you onboarded.
- **Board report** (*Board report*): quarterly figures for board papers. Counts only, no
  names. *Download text* to paste into the board pack.
- View cases and alerts (you can't decide them), and respond to audit findings.

## Auditor (internal, external, or inspection preparation)

1. *Audit → Open an engagement*: type, period and scope. The standard 30-step programme
   is copied in.
2. *Run all 17 tests now*: each test runs over the whole population and is saved
   permanently with a fingerprint of the data tested.
3. *Draw sample* for manual file testing. The seed is recorded; the same seed on the same
   population always gives the same sample.
4. Work through the programme: result, working-paper reference and notes per step.
5. *Draft findings from failed tests*, edit as needed, or raise findings from manual work.
6. Management records responses. Close a finding only with evidence of the fix.
7. *Move to reporting*, then *Download report*. At the next engagement, earlier findings
   are retested automatically.

Only auditors can change audit working papers, and saved test runs can never be altered,
even by an administrator; this is enforced by the database.

## Administrator

- **Staff**: create accounts (a one-time password is shown once), change roles, reset
  passwords or two-factor login, deactivate leavers. Accounts are never deleted.
- **Data and jobs**: import files, see scheduled job runs, run a job now.
- Administrators don't see cases, alerts or audit papers.
