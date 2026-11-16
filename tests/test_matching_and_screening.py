from datetime import date

import pytest

from compliance_core.lists import Alias, ListEntry, ListFormatError, diff_lists, parse_csv_list, parse_un_consolidated
from compliance_core.matching import jaro_winkler, normalise, similarity
from compliance_core.models import MemberType, Person
from compliance_core.screening import new_hits, screen_member


# ---------- matching ----------
def test_jaro_winkler_known_values():
    assert jaro_winkler("martha", "marhta") == pytest.approx(0.9611, abs=1e-4)
    assert jaro_winkler("dwayne", "duane") == pytest.approx(0.84, abs=1e-2)
    assert jaro_winkler("", "abc") == 0.0
    assert jaro_winkler("same", "same") == 1.0


def test_normalise_strips_titles_accents_and_suffixes():
    assert normalise("Hon. Dr. José  O'Brien") == "jose o brien"
    assert normalise("Kestrel Trading FZE", entity=True) == "kestrel trading"


@pytest.mark.parametrize("subject,listed,minimum", [
    ("Marcus Dunmore Kariuki", "Marc Dunmore", 0.88),        # extra name, short form
    ("Dunmore Hale Marcus", "Marcus Hale Dunmore", 0.99),    # reordered
    ("Daniel Ostrowa Marenti", "Daniel Ostrowa Marent", 0.95),
    ("Salome V. Ritter", "Salome Varga Ritter", 0.9),        # initial
])
def test_similar_names_score_high(subject, listed, minimum):
    assert similarity(subject, listed).score >= minimum


@pytest.mark.parametrize("subject,listed", [
    ("Mary Wanjiru Kamau", "Marcus Hale Dunmore"),
    ("Peter Otieno", "Ivo Brandt Kessler"),
])
def test_unrelated_names_score_low(subject, listed):
    assert similarity(subject, listed).score < 0.8


# ---------- list import ----------
def test_parse_un_list(un_xml):
    entries = parse_un_consolidated(un_xml)
    assert len(entries) == 3
    marcus = entries[0]
    assert marcus.primary_name == "MARCUS HALE DUNMORE"
    assert marcus.ref == "XXi.101"
    assert marcus.dates_of_birth == ("1971-08-14",)
    assert Alias("Abu Hale", "low") in marcus.aliases
    assert entries[1].dates_of_birth == ("1980", "1981", "1982")
    assert entries[2].is_entity and entries[2].primary_name == "KESTREL TRADING FZE"


@pytest.mark.parametrize("bad", [b"<html></html>", b"not xml", b"<CONSOLIDATED_LIST/>"])
def test_bad_list_files_are_rejected(bad):
    with pytest.raises(ListFormatError):
        parse_un_consolidated(bad)


def test_csv_list_with_mapping():
    text = "Ref,Full Name,Kind,Also Known As\nKE-1,Northwind Logistics Ltd,Entity,Northwind;NW Logistics\nKE-2,Ivo Brandt Kessler,Individual,\n"
    entries = parse_csv_list(text, list_name="Kenya domestic", mapping={"ref": "Ref", "name": "Full Name", "type": "Kind", "aliases": "Also Known As"})
    assert [e.entry_type for e in entries] == ["entity", "individual"]
    assert entries[0].aliases == (Alias("Northwind"), Alias("NW Logistics"))
    with pytest.raises(ListFormatError):
        parse_csv_list(text, list_name="x", mapping={"ref": "Ref", "name": "Name"})


def test_list_diff(un_xml):
    old = parse_un_consolidated(un_xml)
    new = old[1:] + [ListEntry("UN Security Council", "XXi.999", "individual", "NEW PERSON")]
    changed = ListEntry(**{**old[1].__dict__, "aliases": (Alias("Dan Marent"),)})
    new[0] = changed
    d = diff_lists(old, new)
    assert [e.ref for e in d.added] == ["XXi.999"]
    assert [e.ref for e in d.removed] == ["XXi.101"]
    assert [e.ref for e in d.changed] == ["XXi.102"]


# ---------- screening ----------
def test_member_hit_with_dob_confirms_strength(un_xml, make_member):
    entries = parse_un_consolidated(un_xml)
    m = make_member(name="Marcus Hale Dunmore", date_of_birth=date(1971, 8, 14))
    hits = screen_member(m, entries)
    assert len(hits) == 1
    h = hits[0]
    assert h.entry.ref == "XXi.101" and h.dob_check == "match" and h.confidence == "strong"


def test_dob_mismatch_keeps_hit_but_lowers_confidence(un_xml, make_member):
    entries = parse_un_consolidated(un_xml)
    m = make_member(name="Marcus Hale Dunmore", date_of_birth=date(1990, 1, 1))
    [h] = screen_member(m, entries)
    assert h.dob_check == "mismatch" and h.confidence == "possible"


def test_low_quality_alias_needs_higher_score(un_xml, make_member):
    entries = parse_un_consolidated(un_xml)
    # "Abu Halima" scores 0.915 against the low-quality alias "Abu Hale": above the normal
    # threshold (0.88) but below the low-quality one (0.95), so no hit.
    assert screen_member(make_member(name="Abu Halima"), entries) == []
    assert len(screen_member(make_member(name="Abu Hale"), entries)) == 1


def test_officials_and_owners_are_screened_but_entities_only_against_entities(un_xml, make_member):
    entries = parse_un_consolidated(un_xml)
    group = make_member(member_type=MemberType.GROUP, name="Upendo Women Group",
                        officials=[Person("Daniel Ostrowa Marent", "Treasurer", date_of_birth=date(1981, 5, 2))])
    hits = screen_member(group, entries)
    assert [(h.subject.role, h.entry.ref, h.dob_check) for h in hits] == [("official", "XXi.102", "match")]
    company = make_member(member_type=MemberType.CORPORATE, name="Kestrel General Trading Ltd")
    assert [h.entry.ref for h in screen_member(company, entries)] == ["XXe.201"]


def test_decided_hits_are_not_raised_again(un_xml, make_member):
    entries = parse_un_consolidated(un_xml)
    hits = screen_member(make_member(name="Marcus Dunmore Kariuki"), entries)
    assert hits and new_hits(hits, {h.key for h in hits}) == []
