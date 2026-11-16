"""Screening members, their officials and beneficial owners against lists.

A hit is never cleared automatically. Secondary identifiers (date of birth,
nationality) only change the confidence shown to the compliance officer, who
records the decision. Low-quality UN aliases need a higher score to raise a hit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .lists import ListEntry
from .matching import similarity
from .models import Member, MemberType


@dataclass(frozen=True)
class Subject:
    name: str
    role: str                      # "member", "official", "beneficial_owner"
    date_of_birth: date | None
    nationality: str | None
    is_entity: bool


@dataclass(frozen=True)
class Hit:
    member_no: str
    subject: Subject
    entry: ListEntry
    matched_list_name: str
    score: float
    method: str
    dob_check: str                 # "match", "mismatch", "not available"
    nationality_check: str
    confidence: str                # "strong" or "possible"

    @property
    def key(self) -> tuple[str, str, str, str]:
        """Stable identity: the same hit is not raised twice for a decided pair."""
        return (self.member_no, self.subject.name, self.entry.list_name, self.entry.ref)


def subjects_of(member: Member) -> list[Subject]:
    entity = member.member_type is not MemberType.INDIVIDUAL
    subs = [Subject(member.name, "member", member.date_of_birth, member.nationality, entity)]
    for p in member.officials:
        subs.append(Subject(p.name, "official", p.date_of_birth, p.nationality, False))
    for p in member.beneficial_owners:
        subs.append(Subject(p.name, "beneficial_owner", p.date_of_birth, p.nationality, False))
    return subs


def _dob_check(subject: Subject, entry: ListEntry) -> str:
    if not subject.date_of_birth or not entry.dates_of_birth:
        return "not available"
    full = subject.date_of_birth.isoformat()
    year = str(subject.date_of_birth.year)
    for d in entry.dates_of_birth:
        if d == full or d == year or (len(d) == 10 and d[:4] == year):
            return "match"
    return "mismatch"


def _nat_check(subject: Subject, entry: ListEntry) -> str:
    if not subject.nationality or not entry.nationalities:
        return "not available"
    s = subject.nationality.strip().lower()
    return "match" if any(s == n.strip().lower() for n in entry.nationalities) else "mismatch"


def screen_member(member: Member, entries: list[ListEntry], *, threshold: float = 0.88,
                  low_quality_threshold: float = 0.95) -> list[Hit]:
    hits: list[Hit] = []
    for subject in subjects_of(member):
        for entry in entries:
            if entry.is_entity != subject.is_entity:
                continue
            best = None
            for listed, quality in entry.all_names:
                r = similarity(subject.name, listed, entity=entry.is_entity)
                needed = low_quality_threshold if quality == "low" else threshold
                if r.score >= needed and (best is None or r.score > best[0].score):
                    best = (r, listed)
            if best is None:
                continue
            r, listed = best
            dob, nat = _dob_check(subject, entry), _nat_check(subject, entry)
            # Date of birth is the stronger identifier: an exact match outweighs a different
            # nationality (dual nationals, false documents). Without a DOB, nationality decides.
            strong = r.score >= 0.95 and (dob == "match" or (dob == "not available" and nat != "mismatch"))
            hits.append(Hit(member.member_no, subject, entry, listed, round(r.score, 4), r.method,
                            dob, nat, "strong" if strong else "possible"))
    return hits


def new_hits(found: list[Hit], already_decided: set[tuple[str, str, str, str]]) -> list[Hit]:
    """Hits not previously decided. A changed list entry gets a new decision because
    the web layer clears its decisions when diff_lists() reports the entry as changed."""
    return [h for h in found if h.key not in already_decided]
