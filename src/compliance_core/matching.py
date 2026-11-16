"""Name matching for sanctions and PEP screening.

Combines three comparisons and keeps the best:
  1. Jaro-Winkler on the whole normalised name.
  2. Jaro-Winkler on the name with its words sorted (catches reordered names).
  3. Word-by-word: each word of the list name is matched to its closest word in
     the subject's name, and the scores are averaged. Catches extra or missing
     middle names ("Marc Dunmore" vs "Marcus Dunmore Kariuki"). Single letters
     are treated as initials.

Scores run from 0 to 1. The threshold is a setting; 0.88 is the starting value.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

TITLES = {
    "mr", "mrs", "ms", "miss", "dr", "prof", "hon", "eng", "rev", "sir", "sheikh",
    "pastor", "bishop", "fr", "capt", "gen", "col", "maj", "lt", "amb", "cpa",
}
ENTITY_SUFFIXES = {
    "ltd", "limited", "plc", "llc", "inc", "co", "company", "corp", "corporation",
    "fze", "fzco", "fzc", "gmbh", "sa", "sarl", "bv", "the", "and",
}


def normalise(name: str, *, entity: bool = False) -> str:
    """Lower-case, strip accents and punctuation, drop titles and company suffixes."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    drop = TITLES | (ENTITY_SUFFIXES if entity else set())
    words = [w for w in text.split() if w not in drop]
    return " ".join(words)


def jaro(a: str, b: str) -> float:
    if a == b:
        return 1.0 if a else 0.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    window = max(0, max(la, lb) // 2 - 1)
    a_hit = [False] * la
    b_hit = [False] * lb
    matches = 0
    for i, ch in enumerate(a):
        lo, hi = max(0, i - window), min(lb, i + window + 1)
        for j in range(lo, hi):
            if not b_hit[j] and b[j] == ch:
                a_hit[i] = b_hit[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    transpositions, k = 0, 0
    for i in range(la):
        if a_hit[i]:
            while not b_hit[k]:
                k += 1
            if a[i] != b[k]:
                transpositions += 1
            k += 1
    m = matches
    return (m / la + m / lb + (m - transpositions / 2) / m) / 3


def jaro_winkler(a: str, b: str, prefix_scale: float = 0.1) -> float:
    j = jaro(a, b)
    prefix = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        prefix += 1
    return j + prefix * prefix_scale * (1 - j)


def _word_score(list_word: str, subject_words: list[str]) -> float:
    best = 0.0
    for w in subject_words:
        if len(list_word) == 1 or len(w) == 1:          # initial
            s = 0.9 if list_word[0] == w[0] else 0.0
        else:
            s = jaro_winkler(list_word, w)
        best = max(best, s)
    return best


@dataclass(frozen=True)
class MatchResult:
    score: float
    method: str          # "full", "sorted" or "word-by-word"


def similarity(subject: str, listed: str, *, entity: bool = False) -> MatchResult:
    a, b = normalise(subject, entity=entity), normalise(listed, entity=entity)
    if not a or not b:
        return MatchResult(0.0, "full")
    results = [
        MatchResult(jaro_winkler(a, b), "full"),
        MatchResult(jaro_winkler(" ".join(sorted(a.split())), " ".join(sorted(b.split()))), "sorted"),
    ]
    a_words, b_words = a.split(), b.split()
    if len(b_words) >= 2 and len(a_words) >= 2:
        avg = sum(_word_score(w, a_words) for w in b_words) / len(b_words)
        results.append(MatchResult(avg, "word-by-word"))
    return max(results, key=lambda r: r.score)
