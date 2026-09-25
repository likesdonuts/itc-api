"""Reading claims and patents out of legal prose, by rule.

Everything a claim number depends on happens here, in code: a model may
quote a claim list ("1-5, 8, and 12"), but only `expand_claims` turns it into
numbers, and it refuses lists that make no sense (a range running backward,
or one spanning more claims than any patent has).

    split_sentences   decisional prose -> sentences, without breaking on
                      "U.S.", "No.", "Inv.", "Fed. Cir.", "Inc." or initials
    find_claim_refs   "claims 1-3 of the '294 patent; claim 1 of the '508
                      patent" -> two references, with where each sits
    expand_claims     "1-3, 5, and 7 through 9" -> [1, 2, 3, 5, 7, 8, 9]
    patent_for        each reference's patent, from the same sentence, mapped
                      to the record's own patent list
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

MAX_RANGE = 150


class ClaimListError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Sentences

# Abbreviations that end in a full stop without ending a sentence. Single
# capital initials ("John A. Smith") and dotted capitals ("U.S.", "L.L.C.")
# are recognized by shape instead of listed.
ABBREVIATIONS = {
    "no", "nos", "inv", "fed", "cir", "inc", "co", "corp", "ltd", "llc", "id", "e.g", "i.e",
    "v", "vs", "reg", "pat", "app", "ex", "exs", "dkt", "mr", "ms", "mrs", "dr", "jr", "sr",
    "al", "op", "tr", "br", "supp", "sec", "secs", "art", "fig", "figs", "p", "pp", "ch",
    "cl", "cls", "para", "paras", "ser", "comm'n", "comm", "admin", "u.s.c", "c.f.r",
    "f", "f.2d", "f.3d", "f.4th", "st", "ave", "approx", "etc",
    # "Order No. 32 (Dec. 1, 2023) granted ..."
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}
# Company forms, often written in capitals, that end a name, not a sentence.
COMPANY_FORMS = {"sp", "inc", "co", "corp", "ltd", "llc", "plc", "gmbh", "bv", "nv", "sa", "srl", "spa", "ag", "kk"}
_BOUNDARY = re.compile(r"([.!?])([\"'”’)\]]*)\s+(?=[\"'“‘(\[]?[A-Z0-9])")
_DOTTED_CAPS = re.compile(r"^(?:[A-Z]\.)+[A-Z]?$")


def _ends_with_abbreviation(text: str) -> bool:
    word = text.rsplit(None, 1)[-1] if text.strip() else ""
    word = word.lstrip("(\"'“‘[")
    bare = word.rstrip(".")
    if not bare:
        return False
    if len(bare) == 1 and bare.isupper():  # an initial: "John A. Smith"
        return True
    if _DOTTED_CAPS.match(word):  # "U.S.", "L.L.C."
        return True
    if bare.lower() in COMPANY_FORMS:  # "MIRAmedtech SP. Z.O.O.", "ACME INC."
        return True
    if len(bare) > 1 and bare.isupper() and "." not in bare:
        # "the ID." (initial determination), "the ALJ." -- acronyms end
        # sentences; only "Id." and "id." are the citation abbreviation.
        return False
    return bare.lower() in ABBREVIATIONS


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences, keeping legal abbreviations intact."""
    flat = " ".join(str(text or "").split())
    sentences: list[str] = []
    start = 0
    for match in _BOUNDARY.finditer(flat):
        end = match.end(2)
        candidate = flat[start:end]
        if match.group(1) == "." and _ends_with_abbreviation(flat[start : match.start(1) + 1]):
            continue
        sentences.append(candidate.strip())
        start = match.end()
    tail = flat[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


# ---------------------------------------------------------------------------
# Claim references

_DASH = r"(?:-|–|—|\s+through\s+|\s+to\s+)"
_ITEM = rf"\d+(?:\s*{_DASH}\s*\d+)?"
_JOIN = r"(?:\s*,\s*(?:and\s+|or\s+)?|\s+and\s+|\s+or\s+|\s*&\s*)"
CLAIM_REF_RE = re.compile(rf"\bclaims?\s+(?P<list>{_ITEM}(?:{_JOIN}{_ITEM})*)", re.I)


@dataclass(frozen=True)
class ClaimRef:
    verbatim: str  # the claim list exactly as written: "1-5, 8, and 12"
    start: int  # where the reference sits in its sentence
    end: int


def find_claim_refs(sentence: str) -> list[ClaimRef]:
    return [
        ClaimRef(match.group("list").strip(" ,"), match.start(), match.end())
        for match in CLAIM_REF_RE.finditer(sentence)
    ]


def expand_claims(verbatim: str) -> list[int]:
    """Turn a claim list as written into claim numbers.

    Raises ClaimListError for anything that is not a claim list, a range
    that runs backward, or one spanning more than 150 claims.
    """
    text = str(verbatim or "").strip()
    text = re.sub(r"^claims?\s+", "", text, flags=re.I)
    text = re.sub(rf"\s*{_DASH}\s*", "-", text)
    pieces = [p.strip() for p in re.split(r"\s*,\s*(?:and\s+|or\s+)?|\s+and\s+|\s+or\s+|\s*&\s*", text)]
    numbers: set[int] = set()
    for piece in pieces:
        if not piece:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", piece)
        if not match:
            raise ClaimListError(f"not a claim list: {verbatim!r}")
        first = int(match.group(1))
        last = int(match.group(2) or first)
        if last < first:
            raise ClaimListError(f"range runs backward: {piece!r} in {verbatim!r}")
        if last - first + 1 > MAX_RANGE:
            raise ClaimListError(f"range spans more than {MAX_RANGE} claims: {piece!r}")
        if first < 1:
            raise ClaimListError(f"claim 0 in {verbatim!r}")
        numbers.update(range(first, last + 1))
    if not numbers:
        raise ClaimListError(f"no claims in {verbatim!r}")
    return sorted(numbers)


# ---------------------------------------------------------------------------
# Patents

_FULL_NUMBER_RE = re.compile(
    r"\b(?:U\.\s?S\.\s+)?Pat(?:ent)?\.?\s+Nos?\.?\s*(?P<number>RE\s?\d{2},?\d{3}|D\s?\d{3},?\d{3}|\d{1,2},?\d{3},?\d{3})",
    re.I,
)
# "the '294 patent", curly or straight; OCR sometimes drops the apostrophe,
# so "the 294 patent" is accepted too (only after "the", to stay specific).
_SHORT_FORM_RE = re.compile(r"(?:[‘'’`]|\b[Tt]he\s+)(?P<tail>\d{3})\s+[Pp]atent")


def patent_digits(number: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(number or "")).upper()


@dataclass(frozen=True)
class PatentMention:
    patent: str | None  # the record's own spelling ("8,350,294"), or None if not on its list
    written: str
    start: int
    end: int


def patent_mentions(sentence: str, patents: Iterable[str]) -> list[PatentMention]:
    """Every patent a sentence names, full ("U.S. Patent No. 8,350,294") or
    short ("the '294 patent"), mapped to the record's list. A short form two
    of the record's patents share is left unresolved rather than guessed.
    """
    patents = list(patents)
    by_digits = {patent_digits(p): p for p in patents}
    by_tail: dict[str, list[str]] = {}
    for p in patents:
        by_tail.setdefault(patent_digits(p)[-3:], []).append(p)

    found = []
    for match in _FULL_NUMBER_RE.finditer(sentence):
        number = match.group("number")
        found.append(PatentMention(by_digits.get(patent_digits(number)), number, match.start(), match.end()))
    for match in _SHORT_FORM_RE.finditer(sentence):
        options = by_tail.get(match.group("tail"), [])
        found.append(
            PatentMention(options[0] if len(options) == 1 else None, match.group(0), match.start(), match.end())
        )
    return sorted(found, key=lambda m: m.start)


def patent_for(ref: ClaimRef, sentence: str, patents: Iterable[str]) -> str | None:
    """The patent a claim reference belongs to, from its own sentence.

    "claims 1-3 of the '294 patent; claim 1 of the '508 patent": each
    reference takes the first patent named after it and before the next
    reference; failing that, the nearest one before it. A sentence naming a
    single patent gives it to every reference. None means the sentence does
    not say, and the model (or a person) has to.
    """
    mentions = [m for m in patent_mentions(sentence, patents)]
    if not mentions:
        return None
    resolved = {m.patent for m in mentions}
    if len(resolved) == 1 and None not in resolved:
        return mentions[0].patent

    refs = find_claim_refs(sentence)
    later = [r.start for r in refs if r.start > ref.start]
    limit = min(later) if later else len(sentence)
    after = [m for m in mentions if ref.end <= m.start < limit]
    if after:
        return after[0].patent
    before = [m for m in mentions if m.end <= ref.start]
    return before[-1].patent if before else None
