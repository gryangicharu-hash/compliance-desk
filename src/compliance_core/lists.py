"""Loading sanctions and PEP lists.

parse_un_consolidated() reads the UN Security Council consolidated list in its
published XML format (CONSOLIDATED_LIST > INDIVIDUALS / ENTITIES). The live file
is downloaded by a scheduled job in the web layer; this module only parses it.

parse_csv_list() reads lists that arrive as spreadsheets (for example Kenya's
domestic designations or a licensed PEP file), using a column mapping, because
their layouts are not fixed.

diff_lists() compares two versions so the system can log what changed and
re-screen every member whenever a list changes.
"""
from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Alias:
    name: str
    quality: str = "good"          # UN marks some aliases "Low" quality


@dataclass(frozen=True)
class ListEntry:
    list_name: str                 # e.g. "UN Security Council"
    ref: str                       # list reference, e.g. "QDi.001"
    entry_type: str                # "individual" or "entity"
    primary_name: str
    aliases: tuple[Alias, ...] = ()
    dates_of_birth: tuple[str, ...] = ()   # "YYYY-MM-DD" or "YYYY"
    nationalities: tuple[str, ...] = ()
    listed_on: str | None = None
    extra: tuple[tuple[str, str], ...] = field(default=())

    @property
    def all_names(self) -> list[tuple[str, str]]:
        """(name, quality) pairs, primary name first."""
        return [(self.primary_name, "good")] + [(a.name, a.quality) for a in self.aliases]

    @property
    def is_entity(self) -> bool:
        return self.entry_type == "entity"


class ListFormatError(ValueError):
    pass


def _text(el: ET.Element | None, tag: str) -> str:
    if el is None:
        return ""
    child = el.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def _aliases(el: ET.Element, tag: str) -> tuple[Alias, ...]:
    out = []
    for a in el.findall(tag):
        name = _text(a, "ALIAS_NAME")
        if name:
            quality = (_text(a, "QUALITY") or "good").lower()
            out.append(Alias(name, "low" if quality == "low" else "good"))
    return tuple(out)


def parse_un_consolidated(xml_data: bytes | str) -> list[ListEntry]:
    """Parse the UN consolidated sanctions list XML.

    Raises ListFormatError if the document is not a consolidated list, so a broken
    or truncated download is never mistaken for an empty list.
    """
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        raise ListFormatError(f"Not valid XML: {exc}") from exc
    if root.tag != "CONSOLIDATED_LIST":
        raise ListFormatError(f"Expected CONSOLIDATED_LIST, found {root.tag}")
    individuals = root.find("INDIVIDUALS")
    entities = root.find("ENTITIES")
    if individuals is None and entities is None:
        raise ListFormatError("List has neither INDIVIDUALS nor ENTITIES")

    entries: list[ListEntry] = []
    for ind in (individuals.findall("INDIVIDUAL") if individuals is not None else []):
        parts = [_text(ind, t) for t in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")]
        name = " ".join(p for p in parts if p)
        dobs = []
        for d in ind.findall("INDIVIDUAL_DATE_OF_BIRTH"):
            exact, year = _text(d, "DATE"), _text(d, "YEAR")
            if exact:
                dobs.append(exact[:10])
            elif year:
                dobs.append(year)
            else:
                lo, hi = _text(d, "FROM_YEAR"), _text(d, "TO_YEAR")
                if lo and hi and lo.isdigit() and hi.isdigit():
                    dobs.extend(str(y) for y in range(int(lo), int(hi) + 1))
        nats = tuple(v.text.strip() for v in ind.findall("NATIONALITY/VALUE") if v.text)
        entries.append(ListEntry(
            list_name="UN Security Council",
            ref=_text(ind, "REFERENCE_NUMBER") or _text(ind, "DATAID"),
            entry_type="individual",
            primary_name=name,
            aliases=_aliases(ind, "INDIVIDUAL_ALIAS"),
            dates_of_birth=tuple(dobs),
            nationalities=nats,
            listed_on=_text(ind, "LISTED_ON") or None,
        ))
    for ent in (entities.findall("ENTITY") if entities is not None else []):
        entries.append(ListEntry(
            list_name="UN Security Council",
            ref=_text(ent, "REFERENCE_NUMBER") or _text(ent, "DATAID"),
            entry_type="entity",
            primary_name=_text(ent, "FIRST_NAME"),
            aliases=_aliases(ent, "ENTITY_ALIAS"),
            listed_on=_text(ent, "LISTED_ON") or None,
        ))
    missing = [e.ref for e in entries if not e.primary_name]
    if missing:
        raise ListFormatError(f"Entries without a name: {', '.join(missing[:5])}")
    return entries


def parse_csv_list(text: str, *, list_name: str, mapping: dict[str, str],
                   alias_separator: str = ";") -> list[ListEntry]:
    """Parse a list delivered as CSV.

    mapping keys: ref, name, type (optional), aliases (optional), dob (optional),
    nationality (optional). Values are the CSV column headers.
    """
    reader = csv.DictReader(io.StringIO(text))
    required = {"ref", "name"}
    if not required <= mapping.keys():
        raise ListFormatError("mapping needs at least 'ref' and 'name'")
    headers = set(reader.fieldnames or [])
    absent = [mapping[k] for k in mapping if mapping[k] not in headers]
    if absent:
        raise ListFormatError(f"Columns not found in file: {', '.join(absent)}")
    out = []
    for row in reader:
        name = (row.get(mapping["name"]) or "").strip()
        if not name:
            continue
        get = lambda k: (row.get(mapping[k]) or "").strip() if k in mapping else ""
        aliases = tuple(Alias(a.strip()) for a in get("aliases").split(alias_separator) if a.strip())
        etype = get("type").lower() or "individual"
        out.append(ListEntry(
            list_name=list_name, ref=get("ref"), primary_name=name,
            entry_type="entity" if etype.startswith("ent") else "individual",
            aliases=aliases,
            dates_of_birth=tuple(d for d in [get("dob")] if d),
            nationalities=tuple(n for n in [get("nationality")] if n),
        ))
    return out


@dataclass(frozen=True)
class ListDiff:
    added: tuple[ListEntry, ...]
    removed: tuple[ListEntry, ...]
    changed: tuple[ListEntry, ...]

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


def diff_lists(old: list[ListEntry], new: list[ListEntry]) -> ListDiff:
    old_by = {(e.list_name, e.ref): e for e in old}
    new_by = {(e.list_name, e.ref): e for e in new}
    added = tuple(e for k, e in new_by.items() if k not in old_by)
    removed = tuple(e for k, e in old_by.items() if k not in new_by)
    changed = tuple(e for k, e in new_by.items() if k in old_by and old_by[k] != e)
    return ListDiff(added, removed, changed)
