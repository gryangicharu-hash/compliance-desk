# Architecture

## Layers

```
 Web application (next stage)          screens, logins, roles, scheduled jobs, file storage
        │  loads records, calls engine, saves results
        ▼
 compliance_core (this package)        pure functions: screening, risk, monitoring, deadlines,
        │                              reports, audit chain, encryption
        ▼
 PostgreSQL (db/schema.sql)            records + safeguards the database enforces itself
```

The engine never touches the database or the network. That keeps the rules
easy to test, easy to review by an auditor, and independent of the web framework.

## Main flows

**Onboarding**
1. Front office captures the member, officials, beneficial owners and documents.
2. `screening.screen_member()` against current lists. Any hit: status `screening_hold`.
3. `risk.assess()` gives the score, rating, due diligence level and next review date.
4. PEP or high risk: status `pending_approval` until senior management approves
   (the database refuses an active PEP without an approver and source of wealth).
5. Every step is written to the audit trail with `audit.append()`.

**List update (daily job)**
1. Download the UN consolidated list; `lists.parse_un_consolidated()`. A parse
   error stores a `rejected` list version and alerts the compliance officer. It
   never replaces the current list with an empty one.
2. `lists.diff_lists()` against the current version, logged.
3. Re-screen every member. Decisions on changed entries are cleared so they are reviewed again.

**Transaction monitoring (nightly job)**
1. Import transactions from the core banking system.
2. `monitoring.run_rules()` per member; `new_alerts()` drops alerts already raised.
3. Compliance officer escalates an alert to a case or dismisses it with a reason.
4. A case starts the STR clock at the time suspicion was formed (`deadlines.str_clock()`).

**Risk re-assessment** runs on any change to a member, a screening decision, a
case opening or closing, guarantor network changes, or a new settings version.
Each result is a new row in `risk_assessment`; history is never edited.

## Deployment model

One deployment and one database per SACCO (see DECISIONS.md, D2).
