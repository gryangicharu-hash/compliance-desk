"""Compliance engine for AML/CFT work in Kenyan deposit-taking SACCOs.

Pure Python, no web framework. Modules:
  models       domain objects (members, transactions, guarantees)
  matching     name similarity for screening
  lists        UN consolidated list and CSV list import, list version diffs
  screening    member, official and beneficial-owner screening
  guarantors   guarantor network red flags
  risk         member risk scoring with override rules
  monitoring   transaction monitoring rules
  deadlines    STR clock, cash report dates, record retention, ID expiry
  reports      weekly cash report, STR XML, board summary
  audit        hash-chained audit trail
  crypto       field encryption and blind indexes
  assurance    AML audit: control tests, sampling, findings, follow-up
"""
__version__ = "0.2.0"
