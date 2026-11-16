"""Guarantor network analysis.

Every guarantor is a member, so guarantees form a directed graph
(guarantor -> borrower). Patterns that matter for money laundering:
  - circular guarantees (A guarantees B, B guarantees A, or longer loops),
    which can hide nominee borrowing;
  - one member guaranteeing many loans (concentration);
  - guarantors who are blocked, exited, PEPs, high risk or under screening review.
Flags are returned with a severity so risk scoring can use them.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from .models import Guarantee, Member, MemberStatus, PepStatus


@dataclass(frozen=True)
class Flag:
    code: str        # CIRCULAR, CONCENTRATION, EXITED, BLOCKED, PEP, SCREENING, HIGH_RISK, LINKED_TO_BLOCKED
    severity: str    # "high" or "medium"
    text: str


@dataclass
class NetworkProfile:
    member_no: str
    loans_guaranteed: list[Guarantee] = field(default_factory=list)
    guaranteed_by: list[Guarantee] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)

    @property
    def total_guaranteed(self) -> Decimal:
        return sum((g.amount for g in self.loans_guaranteed), Decimal("0"))

    @property
    def distinct_borrowers(self) -> int:
        return len({g.borrower for g in self.loans_guaranteed})


def find_cycles(guarantees: list[Guarantee], max_length: int = 4) -> list[tuple[str, ...]]:
    """Simple cycles up to max_length members, each reported once (rotated to start
    at its smallest member number)."""
    graph: dict[str, set[str]] = defaultdict(set)
    for g in guarantees:
        if g.guarantor != g.borrower:
            graph[g.guarantor].add(g.borrower)
    found: set[tuple[str, ...]] = set()

    def walk(start: str, node: str, path: list[str]) -> None:
        for nxt in sorted(graph.get(node, ())):
            if nxt == start and len(path) >= 2:
                i = path.index(min(path))
                found.add(tuple(path[i:] + path[:i]))
            elif nxt not in path and len(path) < max_length and nxt > start:
                walk(start, nxt, path + [nxt])

    for start in sorted(graph):
        walk(start, start, [start])
    return sorted(found)


def analyse(guarantees: list[Guarantee], members: dict[str, Member], *,
            max_loans_per_guarantor: int = 4,
            high_risk_members: set[str] = frozenset(),
            screening_review_members: set[str] = frozenset()) -> dict[str, NetworkProfile]:
    profiles: dict[str, NetworkProfile] = defaultdict(lambda: NetworkProfile(""))

    def prof(no: str) -> NetworkProfile:
        p = profiles[no]
        p.member_no = no
        return p

    for g in guarantees:
        prof(g.guarantor).loans_guaranteed.append(g)
        prof(g.borrower).guaranteed_by.append(g)
        if g.guarantor == g.borrower:
            prof(g.guarantor).flags.append(Flag("SELF_GUARANTEE", "high", f"Guarantees own loan {g.loan_id}"))

    for cycle in find_cycles(guarantees):
        loop = " → ".join(cycle + (cycle[0],))
        for no in cycle:
            prof(no).flags.append(Flag("CIRCULAR", "high", f"Circular guarantee: {loop}"))

    blocked = {no for no, m in members.items() if m.status is MemberStatus.BLOCKED}
    for no, p in list(profiles.items()):
        m = members.get(no)
        n = len(p.loans_guaranteed)
        if n > max_loans_per_guarantor:
            p.flags.append(Flag("CONCENTRATION", "medium", f"Guarantees {n} loans (limit {max_loans_per_guarantor})"))
        if m is not None and p.loans_guaranteed:
            if m.status is MemberStatus.EXITED:
                p.flags.append(Flag("EXITED", "medium", "Guarantor has exited the SACCO"))
            if m.status is MemberStatus.BLOCKED:
                p.flags.append(Flag("BLOCKED", "high", "Guarantor is blocked"))
            if m.pep is not PepStatus.NONE:
                p.flags.append(Flag("PEP", "medium", "Guarantor is a PEP"))
            if no in screening_review_members:
                p.flags.append(Flag("SCREENING", "high", "Guarantor has an unresolved screening match"))
            if no in high_risk_members:
                p.flags.append(Flag("HIGH_RISK", "medium", "Guarantor is rated high risk"))
        linked = {g.guarantor for g in p.guaranteed_by} | {g.borrower for g in p.loans_guaranteed}
        bad = sorted(linked & blocked - {no})
        if bad:
            p.flags.append(Flag("LINKED_TO_BLOCKED", "high", f"Guarantee link to blocked member(s): {', '.join(bad)}"))
    return dict(profiles)
