# AML/CFT audit programme

For internal auditors, external auditors and SACCOs preparing for a SASRA inspection.
Steps marked with a test code are run automatically by `compliance_core.assurance`
over the whole population; the auditor reviews the exceptions. Other steps are
manual, using samples from `random_sample()` or `risk_based_sample()`.

## How an engagement runs

1. Open an engagement (auditor role only). Record period and scope.
2. Run the automated tests. The run is saved permanently with a fingerprint
   (SHA-256) of the data tested.
3. Draw samples for manual testing; method and seed are recorded so anyone can reproduce them.
4. Work through the programme below; record result and working-paper reference per step.
5. Raise findings (`findings_from_run()` drafts them from failed tests).
6. Management records responses, owners and dates. They can't edit or downgrade the finding.
7. At the next audit, `follow_up()` retests each earlier finding automatically.

## Programme

| No. | Area | Regulation | Procedure | Test |
| --- | --- | --- | --- | --- |
| 1 | Governance | Reg. 6 | Confirm the board approved the institutional risk assessment within two years | AT17 |
| 2 | Governance | Reg. 6 | Inspect board minutes for review of AML reports and the risk settings in force | Manual |
| 3 | Governance | — | Confirm an MLRO is appointed and reports independently to the board | Manual |
| 4 | Risk assessment | Reg. 6 | Every active member has a current risk assessment under current settings | AT06 |
| 5 | Risk assessment | Reg. 6 | Risk ratings were not lowered without a second approver | AT14 |
| 6 | Risk assessment | Reg. 7(2)(a) | New products and channels were risk-assessed before launch | Manual |
| 7 | Customer due diligence | Regs 12–14 | No active member has an incomplete KYC file | AT01 |
| 8 | Customer due diligence | Regs 12–13 | No active member has an expired ID | AT02 |
| 9 | Customer due diligence | Regs 12, 22 | Source of funds recorded for every member | AT03 |
| 10 | Customer due diligence | Regs 12–14 | Sample member files: documents match the system, verified, signed off | Manual (sample) |
| 11 | Customer due diligence | Reg. 14 | Sample groups and companies: officials and beneficial owners identified and verified | Manual (sample) |
| 12 | Customer due diligence | Reg. 18A | Simplified due diligence used only for low-risk members without red flags | Manual (sample) |
| 13 | PEPs | Reg. 22 | No active PEP lacks senior management approval | AT04 |
| 14 | PEPs | Reg. 22 | Sample PEP files: source of wealth and funds evidenced; enhanced monitoring in place | Manual (sample) |
| 15 | Ongoing monitoring | Regs 29–30 | High-risk member reviews are up to date | AT05 |
| 16 | Ongoing monitoring | Regs 29–30 | Monitoring alerts reviewed within the agreed time | AT13 |
| 17 | Ongoing monitoring | Regs 29–30 | Sample dismissed alerts: reasons adequate and supported | Manual (sample) |
| 18 | Sanctions | PTA | Every member re-screened after each list update | AT10 |
| 19 | Sanctions | PTA | Potential matches decided promptly | AT08 |
| 20 | Sanctions | PTA | Screening decisions have a reason and were not made by the onboarding officer | AT07 |
| 21 | Sanctions | PTA | Confirmed matches are blocked and reported | AT09 |
| 22 | Sanctions | PTA | List imports ran daily; rejected imports were investigated | Manual |
| 23 | Reporting | Reg. 33 | STRs filed within 2 days of suspicion | AT11 |
| 24 | Reporting | Reg. 33 | Sample closed cases: the "not suspicious" conclusion is supported | Manual (sample) |
| 25 | Reporting | Reg. 35 | Every large cash transaction is in a weekly report filed on time | AT12 |
| 26 | Reporting | POCAMLA | Case information restricted to authorised staff (tipping-off) | Manual (access review) |
| 27 | Records | Reg. 37 | Audit trail intact | AT15 |
| 28 | Records | Reg. 37 | No records destroyed before their retention date; legal holds respected | Manual |
| 29 | Training | Internal controls | All staff have current AML training | AT16 |
| 30 | Guarantors | Reg. 6 (SACCO risk) | Sample flagged guarantor networks: red flags reviewed and documented | Manual (sample) |

The step list is a starting point and should be adapted to each engagement's scope.
