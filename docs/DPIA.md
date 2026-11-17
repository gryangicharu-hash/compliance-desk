# Data protection impact assessment (draft)

**Status: draft for the SACCO's data protection officer and legal adviser.** Section 31
of the Data Protection Act, 2019 requires a DPIA before processing likely to result in
a high risk to data subjects. Profiling members for money-laundering risk, screening them
against sanctions lists and monitoring their transactions qualifies. This draft describes
the system; the SACCO completes the assessment, consults where needed and signs it off.

## 1. The processing

| | |
| --- | --- |
| Controller | The SACCO (registered with the ODPC as a data controller) |
| Processor | The hosting or support provider, if any (needs a written contract, s.42) |
| Purpose | Meeting POCAMLA, the POCAML Regulations 2023, the Prevention of Terrorism Act and SASRA requirements: customer due diligence, risk assessment, sanctions screening, transaction monitoring, reporting to the FRC, record keeping, audit |
| Lawful basis | Compliance with a legal obligation (s.30(1)(b)(ii)) |
| Data subjects | Members; officials, signatories and beneficial owners of group and corporate members; guarantors; staff users |
| Personal data | Name, ID or registration number (encrypted), date of birth, nationality, residence, occupation, employer, income, source of funds and wealth, PEP status, products, loans and guarantees, transactions (amount, channel, counterparty phone or account), risk rating and its reasons, screening matches, monitoring alerts, suspicious-activity cases, staff login and audit records |
| Sensitive data (s.2) | None collected by design. Free-text notes must not record health, religion, ethnicity and similar; train staff on this |
| Sources | The member at onboarding; the core banking system; UN and other sanctions/PEP lists |
| Recipients | The FRC (STRs and cash reports, by law); SASRA and auditors on inspection. No other sharing |
| Retention | 7 years after the relationship ends or the last transaction (reg. 37), then deletion, unless a legal hold applies |

## 2. Necessity and proportionality

- Every field maps to a regulatory requirement (docs/REGULATORY_MAP.md). Fields not needed are not collected.
- Automated risk scores inform staff and are **not** final decisions: high-risk relationships
  need a person's approval, matches a person's decision, and alerts a person's review. Under
  s.35, members have the right not to be subject to a decision based solely on automated
  processing; the design keeps a person in each decision.
- Suspicion-related processing can't be disclosed to the member (tipping-off offence). The
  SACCO should document how data subject access requests are handled where s.51 exemptions
  for crime prevention apply, and record each decision.

## 3. Risks and controls

| Risk to members | Likelihood / impact before controls | Controls in the system | Remaining |
| --- | --- | --- | --- |
| Unauthorised access to member data | Medium / High | Individual accounts, roles, two-factor login (all roles except front office), lockout, 30-minute session timeout, HTTPS only, audit trail of every action | Low |
| Disclosure of suspicion (tipping-off, reputational harm) | Medium / High | Cases and alerts hidden from front office by database row-level security; restricted wording on member pages; reminder emails carry counts only | Low |
| Theft of the database or a backup | Low / High | ID numbers and two-factor secrets encrypted with keys kept outside the database; backups restricted and to be encrypted off-site | Low (if keys kept separately) |
| Wrong match leads to a frozen account | Medium / High | Matches held for review, not auto-blocked except pending decision; reasons recorded; segregation of duties; members re-screened when lists change | Medium: review times must be monitored (audit test AT08) |
| Inaccurate risk rating | Medium / Medium | Reasons stored with every rating; settings version recorded; periodic review; board approval of settings | Low |
| Records altered to hide wrongdoing | Low / High | Append-only audit trail (database triggers) and hash chain checked by audit test AT15 | Low |
| Records kept too long | Medium / Low | Retention dates calculated (engine `deadlines.retention_until`) | Medium: deletion job not yet built |
| Transfer outside Kenya | Depends on hosting | Host in Kenya, or meet ss.48–50 conditions | Decide at hosting |
| Data breach not handled in time | Low / High | Audit trail and logs support investigation | Medium: SACCO needs a breach plan; ODPC notification within 72 hours of becoming aware (s.43) |

## 4. Actions before go-live

1. Confirm ODPC registration covers this processing.
2. Choose hosting; if outside Kenya, document the transfer safeguards.
3. Contract with any processor (hosting, support) under s.42.
4. Breach response plan with named people and the 72-hour ODPC notification.
5. Procedure for data subject requests, including the tipping-off limits.
6. Build and test the retention deletion job.
7. Staff training on confidentiality, tipping-off and free-text notes.
8. Independent penetration test; fix findings.
9. Sign-off by the DPO and the board.

## Sign-off

| Role | Name | Date | Decision |
| --- | --- | --- | --- |
| Data protection officer | | | |
| MLRO / compliance officer | | | |
| CEO | | | |
