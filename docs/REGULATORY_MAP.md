# Regulation map

POCAMLA Regulations as amended in 2023, mapped to where the platform meets them.
"Engine" = `src/compliance_core`; "DB" = `db/schema.sql`; "Web" = next stage.

| Obligation | Reg. | Where | Status |
| --- | --- | --- | --- |
| Risk assessment and controls, reviewed at least every 2 years | 6 | Engine `risk` (factors, versioned settings); DB `risk_settings_version` | Built; board approval workflow in web |
| New products and technologies assessed | 7(2)(a) | Web: product register | Planned |
| Non-face-to-face customers | 7A | Engine `risk` (channel factor, reason recorded) | Built |
| Identify individuals | 12–13 | DB `member`, `document`; encrypted ID with blind index | Built (DB); forms in web |
| Identify legal persons, officials, beneficial owners | 2, 14 | DB `member_person`; engine screens officials and owners; missing owners raise risk | Built |
| Simplified due diligence only where low risk, never on suspicion | 18A | Engine `risk` due diligence rules | Built |
| Failure to produce ID: consider STR | 21(2)(e) | Web: incomplete-KYC case type | Planned |
| PEPs: detect, approve, source of wealth and funds, enhanced monitoring | 22 | Engine `risk` override; DB constraints on active PEPs | Built |
| Reliance on third parties | 28 | Web: introducer register | Planned |
| Ongoing monitoring; keep information current | 29–30 | Engine `monitoring`, review dates; DB `alert` | Built; scheduling in web |
| STR within 2 days | 33 | Engine `deadlines.str_clock`, `reports.str_report_xml`; DB `aml_case` | Built; goAML mapping open |
| Weekly cash transaction report (USD 15,000) | 35 | Engine `monitoring` LARGE_CASH, `reports.cash_report_csv`, `deadlines.cash_report_due`; DB `cash_report` | Built; deadline reading to confirm |
| Records kept at least 7 years | 37 | Engine `deadlines.retention_until`, legal hold; DB `retention_until`, append-only audit | Built |
| Targeted financial sanctions without delay | PTA; UNSC resolutions | Engine `lists`, `screening`; confirmed match = prohibited | Built; daily job in web |
| Tipping-off | POCAMLA | DB row-level security on cases and alerts | Built |
| Staff training | Internal controls | DB `training_record`; board summary | Built (DB) |

Source: [POCAMLA Regulations, National Treasury](https://www.treasury.go.ke/wp-content/uploads/2023/09/POCAMLA-REGULATIONS.pdf).
Check against the current consolidated text before go-live; amendments after 2023 are not reflected here.
