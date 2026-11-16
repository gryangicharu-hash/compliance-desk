"""Regulatory and board reports.

str_report_xml() builds the suspicious transaction report as XML shaped like a
goAML report. IMPORTANT: element names here are illustrative. Before go-live
they must be mapped to the XSD schema the Financial Reporting Centre publishes
for goAML, and validated against it.
"""
from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime

from .models import Member, Rating, Transaction


def cash_report_csv(txns: list[Transaction], members: dict[str, Member], *, period_start: date,
                    period_end: date, threshold_kes) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["transaction_id", "date", "member_no", "member_name", "id_number", "direction", "amount_kes"])
    for t in sorted(txns, key=lambda t: t.timestamp):
        if t.channel.value == "cash" and t.amount >= threshold_kes and period_start <= t.timestamp.date() <= period_end:
            m = members[t.member_no]
            w.writerow([t.txn_id, t.timestamp.date().isoformat(), m.member_no, m.name, m.id_number,
                        t.direction.value, f"{t.amount:.2f}"])
    return out.getvalue()


def str_report_xml(*, reporting_entity_id: str, case_ref: str, member: Member, reason: str,
                   action_taken: str, txns: list[Transaction], submitted_at: datetime) -> str:
    root = ET.Element("report")
    ET.SubElement(root, "rentity_id").text = reporting_entity_id
    ET.SubElement(root, "submission_code").text = "E"
    ET.SubElement(root, "report_code").text = "STR"
    ET.SubElement(root, "entity_reference").text = case_ref
    ET.SubElement(root, "submission_date").text = submitted_at.replace(microsecond=0).isoformat()
    ET.SubElement(root, "reason").text = reason
    ET.SubElement(root, "action").text = action_taken
    party = ET.SubElement(ET.SubElement(root, "report_party"), "person")
    ET.SubElement(party, "full_name").text = member.name
    ET.SubElement(party, "id_number").text = member.id_number
    ET.SubElement(party, "nationality").text = member.nationality
    ET.SubElement(party, "occupation").text = member.occupation
    for t in txns:
        el = ET.SubElement(root, "transaction")
        ET.SubElement(el, "transactionnumber").text = t.txn_id
        ET.SubElement(el, "date_transaction").text = t.timestamp.replace(microsecond=0).isoformat()
        ET.SubElement(el, "amount_local").text = f"{t.amount:.2f}"
        ET.SubElement(el, "transmode_code").text = t.channel.value
        ET.SubElement(el, "direction").text = t.direction.value
    ET.indent(root)
    note = "<!-- Illustrative structure: map to the FRC goAML XSD before use. -->\n"
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + note + ET.tostring(root, encoding="unicode") + "\n"


@dataclass(frozen=True)
class BoardSummary:
    period: str
    members_by_rating: dict[str, int]
    pep_count: int
    reviews_completed: int
    reviews_overdue: int
    screening_hits_raised: int
    screening_hits_confirmed: int
    alerts_raised: dict[str, int]
    strs_filed: int
    strs_filed_late: int
    cash_reports_filed: int
    staff_trained: int
    staff_total: int

    def to_text(self) -> str:
        lines = [f"AML/CFT compliance summary: {self.period}", ""]
        lines.append("Members by risk rating: " + ", ".join(f"{k} {v}" for k, v in self.members_by_rating.items()))
        lines.append(f"PEPs: {self.pep_count}")
        lines.append(f"Reviews completed: {self.reviews_completed}; overdue at period end: {self.reviews_overdue}")
        lines.append(f"Screening matches raised: {self.screening_hits_raised}; confirmed: {self.screening_hits_confirmed}")
        lines.append("Monitoring alerts: " + (", ".join(f"{k} {v}" for k, v in sorted(self.alerts_raised.items())) or "none"))
        lines.append(f"STRs filed: {self.strs_filed} (late: {self.strs_filed_late})")
        lines.append(f"Weekly cash reports filed: {self.cash_reports_filed}")
        pct = round(100 * self.staff_trained / self.staff_total) if self.staff_total else 0
        lines.append(f"Staff with current AML training: {self.staff_trained} of {self.staff_total} ({pct}%)")
        return "\n".join(lines)


def board_summary(*, period: str, ratings: dict[str, Rating], peps: int, reviews_completed: int,
                  reviews_overdue: int, hits_raised: int, hits_confirmed: int, alert_rules: list[str],
                  strs_filed: int, strs_late: int, cash_reports: int, staff_trained: int,
                  staff_total: int) -> BoardSummary:
    counts = Counter(r.value for r in ratings.values())
    by_rating = {r.value: counts.get(r.value, 0) for r in Rating}
    return BoardSummary(period, by_rating, peps, reviews_completed, reviews_overdue, hits_raised,
                        hits_confirmed, dict(Counter(alert_rules)), strs_filed, strs_late, cash_reports,
                        staff_trained, staff_total)
