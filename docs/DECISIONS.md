# Decisions

Each entry: what was decided, why, and what would change it.

**D1. Engine in plain Python, separate from the web framework.**
The rules are what regulators and auditors examine. Keeping them as pure
functions makes them testable on their own and lets the web layer change
without touching them. Planned web framework: Django (mature, strong security
defaults, admin, migrations, widely known in Kenya).

**D2. One deployment and database per SACCO.**
Strongest separation of member data between clients, simpler to explain to a
SACCO's board and to the ODPC, and a breach or bug in one client can't expose
another. Cost: more deployments to run. Revisit if the client count makes this
expensive; row-level security per tenant would be the alternative.

**D3. Safeguards in the database as well as the application.**
Append-only audit log, decision-recorded constraints, PEP approval rules and
row-level security on cases hold even if the application has a bug or someone
connects directly.

**D4. Hits are never cleared automatically.**
Date of birth and nationality only change the confidence shown. A person decides
and the decision is recorded. Reason: sanctions must be applied without delay,
and a missed match costs far more than a review.

**D5. Override rules can only raise a rating.**
A weighted score alone can let a PEP or a member under investigation come out
low risk. Overrides make the regulatory minimums impossible to miss.

**D6. STR deadline counted in calendar days by default.**
Conservative reading of "within two days" (reg. 33). Switchable if legal advice differs.

**D7. Settings are versioned and every assessment records the version.**
An inspector can see which board-approved settings produced any past rating.
