"""Process 3 -- who represents whom, read from what has been filed.

Neither source says it outright. IDS lists the parties and nothing about
their lawyers; EDIS lists every filing with who filed it, for whom, and from
which firm:

    filed_by       Jasjit S. Vidwan
    on_behalf_of   Ouraring Inc. and Oura Health Oy
    firm           Mayer Brown LLP

So this reads the documents index the EDIS process already wrote, matches
each filing's "on behalf of" text to the case's IDS parties, and groups the
result into *representations*: one firm acting for a set of parties in one
case. That shape is what lets several complainants share a firm, lets two
respondents have different ones, and lets one party have two.

Three things in the filings are read, in increasing detail:

1. every filing's metadata -- the firm, its parties, and the filing attorney
2. Notice of Appearance titles, which follow a fixed form naming the firm,
   the parties and the lead counsel; supplemental notices add attorneys and
   withdrawal notices remove them
3. Notice of Appearance PDFs, where downloaded (`docs --appearances`): the
   signature block lists the whole team

Non-parties -- companies subpoenaed into a case, which IDS never lists -- are
recognized from filing titles ("... on Behalf of Non-Party Apple, Inc.") and
matched like any party, and the reason they appeared is read from their
notice of limited appearance.

A notice of appearance says who a firm acts for, so where a firm has filed
one, its parties come from its notices alone. Otherwise they come from its
filings, leaving out any filed jointly by both sides (a joint stipulation
filed by the complainant's firm "on behalf of" everyone would otherwise make
it counsel for the respondents too).

It writes data/counsel.json and nothing else, needs no network and no token,
and is rebuilt in full every run, like the case records.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

import dates

from .store import Store

Logger = Callable[[str], None]

APPEARANCE_TYPE = "Notice of Appearance"

# Filings by the Commission itself (orders, notices, OUII designations) name
# no party's counsel.
_COMMISSION_FIRMS = {"usitc", "usinternationaltradecommission", "unitedstatesinternationaltradecommission"}

# How close a stretch of "on behalf of" text must be to a party's name, after
# normalizing both, to count as naming it. High enough that "Samsung
# Electronics America" does not pass for "Samsung Electronics Co., Ltd.", low
# enough to forgive "Samsung Electronic Co., Ltd." and "Ergo Baby"/"Ergobaby".
PARTY_MATCH = 0.9
# The same for two spellings of one attorney ("Bas de Blanc"/"Bas de Blank").
ATTORNEY_MATCH = 0.9

# Words that end a company's name rather than being part of it; a piece of a
# comma-split party list made only of these belongs to the piece before it.
_ENTITY_WORDS = {
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "co", "corp",
    "corporation", "company", "gmbh", "ag", "sa", "sas", "srl", "spa", "bv",
    "nv", "oy", "ab", "as", "plc", "pte", "pty", "kk", "pc", "pllc", "pa",
    "lllp", "sarl", "se", "us", "usa",
}
_FIRM_SUFFIX_RE = re.compile(
    r"\b(LLP|L\.L\.P\.|LLC|L\.L\.C\.|P\.C\.|PC|PLLC|P\.L\.L\.C\.|P\.A\.|Ltd\.?|LPA)\s*(\(US\))?\s*$",
    re.I,
)


# --------------------------------------------------------------------------
# Names


def _fold(text: Any) -> str:
    """Lower case, accents off ("Ōura" -> "oura"), "&" as "and"."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.casefold().replace("&", " and ")


def _words(text: Any) -> list[str]:
    """Words of a name with the punctuation gone, so "Co., Ltd." is "co ltd"
    and "B.V." is "bv".
    """
    return re.sub(r"[^\w\s]", "", _fold(text).replace(",", " ")).split()


def name_key(text: Any) -> str:
    """One spelling for comparing names: "Jasjit S.Vidwan" == "Jasjit S. Vidwan"."""
    return "".join(_words(text))


def firm_key(text: Any) -> str:
    """One spelling per firm: "Finnegan, Henderson, Farabow, Garrett & Dunner,
    L.L.P." and "Finnegan Henderson Farabow Garrett and Dunner LLP" agree.
    """
    words = [word for word in _words(text) if word != "and"]
    while len(words) > 1 and words[-1] in _ENTITY_WORDS:
        words.pop()
    return "".join(words)


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


_PRESIDENT_AND_FELLOWS_RE = re.compile(r"\bPresident\s+and\s+Fellows\b", re.I)


def company_key(text: Any) -> str:
    """A company's name without its corporate form, so "Google Inc." and
    "Google LLC" agree -- but "MediaTek USA Inc." stays apart from "MediaTek
    Inc.", which firm_key would not keep apart.
    """
    words = [word for word in _words(text) if word != "and"]
    while len(words) > 1 and words[-1] in _ENTITY_WORDS - {"us", "usa"}:
        words.pop()
    return "".join(words)


def split_parties(text: Any) -> list[str]:
    """A party list as EDIS writes it, one name per item.

    "Samsung Electronics Co., Ltd., Samsung Electronics America, Inc., and
    Oura Health Oy" -> three names: split at commas and "and", then put the
    "Ltd." and "Inc." pieces back on the name they end.
    """
    # Harvard's corporate name has an "and" in it that is not a list.
    text = _PRESIDENT_AND_FELLOWS_RE.sub("President\0Fellows", str(text or ""))
    pieces = [p.strip().replace("\0", " and ") for p in re.split(r",\s*(?:and\s+)?|\s+and\s+", text) if p.strip()]
    names: list[str] = []
    for piece in pieces:
        words = _words(piece)
        if names and words and all(word in _ENTITY_WORDS for word in words):
            names[-1] = f"{names[-1]}, {piece}"
        else:
            names.append(piece)
    return names


def match_parties(text: Any, participants: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The IDS parties an "on behalf of" text names.

    Each party's name is looked for as a run of words in the text, compared
    with the spaces taken out, so neither the splitting of the text nor small
    spelling differences between the two sources decide the answer.
    """
    participants = list(participants)
    names = tuple(str(party.get("name") or "") for party in participants)
    return [participants[index] for index in _matching_names(str(text or ""), names)]


# A case's filings repeat the same few "on behalf of" texts hundreds of
# times, so each (text, party list) pair is worked out once.
@lru_cache(maxsize=65536)
def _matching_names(text: str, names: tuple[str, ...]) -> tuple[int, ...]:
    words = _words(text)
    return tuple(index for index, name in enumerate(names) if _names_in(words, _words(name)))


def _names_in(words: list[str], name_words: list[str]) -> bool:
    """Whether some run of `words` is within PARTY_MATCH of the name."""
    target = "".join(name_words)
    size = len(name_words)
    if not target:
        return False
    for width in (size - 1, size, size + 1):
        if width < 1:
            continue
        for start in range(0, max(len(words) - width + 1, 0)):
            candidate = "".join(words[start : start + width])
            # The ratio can be at most 2*shorter/total, and quick_ratio is an
            # upper bound too; both only skip windows that cannot pass.
            if 2 * min(len(target), len(candidate)) < PARTY_MATCH * (len(target) + len(candidate)):
                continue
            matcher = SequenceMatcher(None, target, candidate)
            if matcher.quick_ratio() >= PARTY_MATCH and matcher.ratio() >= PARTY_MATCH:
                return True
    return False


def _match_with_non_parties(text: Any, participants: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """match_parties, plus a non-party named under another corporate form
    ("Google Inc." for "Google LLC"), which is how they were merged.
    """
    participants = list(participants)
    found = match_parties(text, participants)
    if found:
        return found
    key = company_key(_clean_party_name(str(text or "")))
    return [p for p in participants if p.get("role") == NON_PARTY_ROLE and key and company_key(p["name"]) == key][:1]


def _is_commission(doc: dict[str, Any]) -> bool:
    if firm_key(doc.get("firm_organization")) in _COMMISSION_FIRMS:
        return True
    return str(doc.get("on_behalf_of") or "").strip().lower().startswith(
        ("office of", "administrative law judge", "commission")
    )


# --------------------------------------------------------------------------
# Notice of Appearance titles


@dataclass
class TitleFacts:
    firms: list[str] = field(default_factory=list)
    lead: str | None = None
    withdrawn: list[str] = field(default_factory=list)


_APPEARANCE_RE = re.compile(r"^Notice of Appearance of (?P<firms>.+?) on Behalf of ", re.I)
_SUPPLEMENTAL_RE = re.compile(
    r"^Supplemental Notice of Appearance; Additional Attorneys? (?:from|of) (?P<firms>.+?) on Behalf of ",
    re.I,
)
_WITHDRAWAL_RE = re.compile(
    r"^Notice of Withdrawal of Appearance of (?P<names>.+?) (?:from|of) (?P<firm>.+?) on Behalf of ",
    re.I,
)
_LEAD_RE = re.compile(r"Designation of (?P<lead>.+?) as Lead (?:Counsel|Attorney)", re.I)


def _split_firms(text: str) -> list[str]:
    """ "Morrison & Foerster LLP and Goldman Ismail ... LLP" is two firms, but
    "Wilmer Cutler Pickering Hale and Dorr LLP" is one: split at "and" only
    where the words before it end the way a firm's name does.
    """
    firms: list[str] = []
    rest = text.strip()
    while True:
        for match in re.finditer(r"\s+and\s+", rest):
            left = rest[: match.start()]
            if _FIRM_SUFFIX_RE.search(left):
                firms.append(left.strip(" ,"))
                rest = rest[match.end() :]
                break
        else:
            break
    firms.append(rest.strip(" ,"))
    return [firm for firm in firms if firm]


def _split_people(text: str) -> list[str]:
    return [p.strip() for p in re.split(r",\s*(?:and\s+)?|\s+and\s+", text) if p.strip()]


def parse_title(title: Any) -> TitleFacts:
    """What a notice of appearance or withdrawal says in its title alone."""
    title = " ".join(str(title or "").split())
    facts = TitleFacts()
    match = _APPEARANCE_RE.match(title) or _SUPPLEMENTAL_RE.match(title)
    if match:
        facts.firms = _split_firms(match.group("firms"))
    withdrawal = _WITHDRAWAL_RE.match(title)
    if withdrawal:
        facts.firms = [withdrawal.group("firm").strip(" ,")]
        facts.withdrawn = _split_people(withdrawal.group("names"))
    lead = _LEAD_RE.search(title)
    if lead:
        facts.lead = lead.group("lead").strip()
    return facts


# --------------------------------------------------------------------------
# Non-parties
#
# IDS lists complainants, respondents and intervenors, but not the companies
# that appear only because someone subpoenaed them. EDIS names them in the
# title instead: "Notice of Limited Appearance of Cooley LLP on Behalf of
# Non-Party Apple, Inc.", "Non-Party ABC Coke's Unopposed Motion ...".

NON_PARTY_ROLE = "Non-Party"

# A filing speaks *for* a non-party only when the non-party is its filer:
# its own notice of appearance, or a paper that opens "Non-Party X's ...".
# Everything else -- an order granting a non-party's motion, a party's
# response to one, "Discovery of Non-Party Confidential Material" -- only
# mentions one, and its "on behalf of" is somebody else.
_NON_PARTY_FILER_RE = re.compile(
    r"^Non-?Part(?:y|ies)\s"
    r"|^(?:Supplemental\s+)?Notice of (?:Limited\s+)?Appearance\b.*\bon Behalf of Non-?Part(?:y|ies)\b",
    re.I,
)
_LIMITED_PURPOSE_RE = re.compile(
    r"for the limited purposes? of (?P<purpose>.+?)"
    r"(?:\s+in the above[- ]captioned (?:investigation|matter)|\s+in this investigation|:)",
    re.I,
)
# When the sentence ends some other way, the first full stop before a
# capitalized word -- not one inside "Inc. and" or "Co. Ltd." ideally, which
# is why it is only the fallback.
_LIMITED_PURPOSE_SENTENCE_RE = re.compile(
    r"for the limited purposes? of (?P<purpose>.+?)(?-i:\.\s+[A-Z][a-z])", re.I
)
_SERVED_BY_RE = re.compile(
    r"\b(?:served|issued)\b.*?\b(?:by|on behalf of)\s+(?:the\s+)?"
    r"(?P<by>Complainants?|Respondents?|Commission Investigative Staff|OUII)\b",
    re.I,
)
# Notices that give their reason without the "limited purposes" formula:
# "... counsel for Non-Party Terns ... who has been served with a Subpoena
# Duces Tecum ...", "... to address issues related to a 'Subpoena Duces
# Tecum ...' issued on behalf of Respondents ...".
_SENTENCE_END_RE = re.compile(r"(?<=[a-z0-9)”\"])\.\s+(?=[A-Z])")
_SUBPOENA_CLAUSE_RE = re.compile(
    r"\b(?:(?:has|have|had) been served with|to address (?:issues|matters) related to|"
    r"in (?:response|connection) (?:to|with)|with respect to|regarding|concerning|"
    r"respond(?:ing)? to|comply(?:ing)? with)\s+(?P<purpose>.*\bsubpoena.*)",
    re.I,
)
_INTERVENOR_RE = re.compile(r"\bcounsel (?:for|to) (?P<proposed>Proposed )?Intervenor\b", re.I)
_SERVED_ON_RE = re.compile(r"\bserved\s+on\s+(?P<date>[A-Z][a-z]+\.?\s+\d{1,2},\s+\d{4})")


# Labels EDIS sometimes leaves on the front of a name: "Non-Party Rakuten
# Symphony USA LLC", "Dr. William Wilcox".
_NAME_LABEL_RE = re.compile(r"^(?:(?:Interested\s+)?Non-?Part(?:y|ies)\s+|(?:Dr|Mr|Mrs|Ms|Prof)\.?\s+)+", re.I)


def _clean_party_name(name: str) -> str:
    return _NAME_LABEL_RE.sub("", " ".join(name.split())).strip(" ,;")


def _is_redacted(name: str) -> bool:
    """ "[ ]" -- a confidential filer whose name EDIS blanks out."""
    return "[" in name or not re.search(r"[A-Za-z]{2}", name)


def _looks_whole(name: str) -> bool:
    """Whether a piece of a split list is a name on its own: a company ending
    in its corporate form ("Google LLC") or a person ("Mobashar Yazdani").
    "Alliance of U.S. Startups" and "Hickman" are not.
    """
    words = _words(name)
    return bool(words) and (words[-1] in _ENTITY_WORDS or is_person_name(name))


def non_party_names(doc: dict[str, Any]) -> list[str]:
    """The non-parties a filing was filed by, or none.

    The name comes from EDIS's own "on behalf of" field ("Apple Inc."),
    since commas inside a name ("Hickman, Williams & Company") make the
    title's wording unsafe to split. "Non-Parties X and Y" is split like any
    party list; a joint filing under "Non-Party" ("MediaTek Inc. and MediaTek
    USA Inc.") only when every piece is a whole name by itself, so "Alliance
    of U.S. Startups and Inventors for Jobs" stays one.
    """
    title = " ".join(str(doc.get("title") or "").split())
    who = _clean_party_name(str(doc.get("on_behalf_of") or ""))
    if not who or _is_commission(doc) or not _NON_PARTY_FILER_RE.search(title):
        return []
    pieces = [_clean_party_name(piece) for piece in split_parties(who)]
    joint = re.search(r"\bNon-?Parties\b", title, re.I) or (
        len(pieces) > 1 and all(_looks_whole(piece) for piece in pieces)
    )
    return [name for name in (pieces if joint else [who]) if name and not _is_redacted(name)]


def _served_on(text: str) -> str | None:
    match = _SERVED_ON_RE.search(text)
    if not match:
        return None
    for fmt in ("%B %d, %Y", "%b. %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(match.group("date"), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def limited_purpose(text: str) -> dict[str, Any] | None:
    """Why a non-party appeared, from its notice of limited appearance.

    The notices state it in one standard sentence -- "... as counsel for
    non-party Apple Inc. for the limited purposes of responding to the
    subpoena duces tecum ... served on Apple by Complainants InterDigital ...
    in the above-captioned investigation" -- from which this keeps the
    sentence, and, for a subpoena, who served it and when.
    """
    body = " ".join(text.split())
    match = _LIMITED_PURPOSE_RE.search(body) or _LIMITED_PURPOSE_SENTENCE_RE.search(body)
    if match:
        purpose = match.group("purpose").strip(" ,")
    else:
        purpose = _subpoena_purpose(body)
    if not purpose:
        intervenor = _INTERVENOR_RE.search(body)
        if intervenor:
            return {"purpose": "proposed intervenor" if intervenor.group("proposed") else "intervenor", "intervenor": True}
        return None
    reason: dict[str, Any] = {"purpose": purpose}
    served_by = _SERVED_BY_RE.search(purpose)
    if served_by:
        by = served_by.group("by")
        reason["served_by"] = by if by.isupper() else by.rstrip("s").title() + "s"
    served_on = _served_on(purpose)
    if served_on:
        reason["served_on"] = served_on
    if "subpoena" in purpose.lower():
        reason["subpoena"] = True
    return reason


def _subpoena_purpose(body: str) -> str | None:
    """The subpoena a notice says it is about, from the first sentence of the
    notice that mentions one, when it does not use the "limited purposes"
    formula.
    """
    start = re.search(r"notice is hereby given|please (?:take )?note|enter(?:s)? (?:their|an?) appearance", body, re.I)
    for sentence in _SENTENCE_END_RE.split(body[start.start() if start else 0 :])[:4]:
        clause = _SUBPOENA_CLAUSE_RE.search(sentence)
        if clause:
            return clause.group("purpose").strip(" ,.")
    return None


def non_party_status(party: dict[str, Any]) -> str:
    """Where a non-party's reason for being in the case stands:

    "read"            its notice says why
    "no_reason"       its notice was read and does not say
    "not_downloaded"  it filed a notice whose PDF is not on disk yet
    "no_notice"       it never filed one; its own filings say what it did
    """
    if party.get("purpose"):
        return "read"
    notice = party.get("notice")
    if notice:
        return "no_reason" if notice.get("files") else "not_downloaded"
    return "no_notice"


def _non_party_summary(reason: dict[str, Any], filings: list[dict[str, Any]]) -> str:
    """One line on why a non-party is in the case, for the case page.

    From its notice when that was read ("Responding to subpoenas served by
    Respondents"; the page adds the date served, `served_on`, in its own
    format); otherwise from what its own filings are about; otherwise, what
    is known about its notice.
    """
    if reason.get("intervenor"):
        return "Proposed intervenor" if reason.get("purpose") == "proposed intervenor" else "Intervenor"
    if reason.get("purpose"):
        if reason.get("subpoena"):
            summary = "Responding to subpoenas"
            if reason.get("served_by"):
                summary += f" served by {reason['served_by']}"
            return summary
        return f"Limited appearance: {reason['purpose']}"
    titles = " ".join(str(f.get("title") or "") for f in filings).lower()
    if "quash" in titles:
        return "Moved to quash or limit a subpoena"
    if "subpoena" in titles:
        return "Responding to a subpoena"
    if "public interest" in titles:
        return "Filed comments on the public interest"
    status = non_party_status(reason)
    if status == "not_downloaded":
        return "Limited appearance; the notice's PDF has not been downloaded yet"
    if status == "no_reason":
        return "Appeared through counsel; the notice does not say why"
    return "Appears through its own filings"


def is_appearance(doc: dict[str, Any]) -> bool:
    title = str(doc.get("title") or "").lower()
    return doc.get("document_type") == APPEARANCE_TYPE or "notice of appearance" in title


def is_withdrawal(doc: dict[str, Any]) -> bool:
    return "withdrawal of appearance" in str(doc.get("title") or "").lower()


# --------------------------------------------------------------------------
# Signature blocks (Notice of Appearance PDFs)


@dataclass
class Signature:
    """The people and firms in a filing's signature block, in order."""

    attorneys: list[tuple[str, str | None]] = field(default_factory=list)  # (name, firm line)
    emails: list[str] = field(default_factory=list)


_EMAIL_RE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_NAME_SUFFIX_RE = re.compile(r",?\s+(Jr\.?|Sr\.?|II|III|IV|Esq\.?)$")
_PARTICLES = {"de", "da", "di", "del", "della", "der", "van", "von", "la", "le", "du", "st.", "bin", "al"}
_ROMAN = {"II", "III", "IV"}
# A line with any of these is an address, a heading or a phone line, never a person.
_NOT_A_NAME = {
    "counsel", "attorney", "attorneys", "respectfully", "submitted", "via", "email", "dated",
    "telephone", "tel", "fax", "facsimile", "suite", "floor", "street", "st", "avenue", "ave",
    "drive", "road", "center", "centre", "plaza", "tower", "building", "boulevard", "blvd",
    "place", "square", "lane", "way", "park", "court", "circle", "highway", "commission",
    "investigation", "honorable", "certain", "llp", "llc", "pc", "pllc", "office", "paralegal",
    "secretary", "judge", "washington", "york", "chicago", "boston", "angeles", "francisco",
}
_BLOCK_END_RE = re.compile(r"^(counsel|attorneys?) (for|to)\b|^certificate of service", re.I)


def is_person_name(line: str) -> bool:
    """Whether a line of a signature block is someone's name.

    Names in these blocks are one per line, in title case, two to five words,
    with initials and particles ("Bas de Blank", "James A. Fussell, III").
    Everything else there -- firms, streets, cities, phones, emails -- has a
    digit, a symbol, a comma, capitals throughout, or a telltale word.
    """
    line = _NAME_SUFFIX_RE.sub("", line.strip())
    if not line or re.search(r"[\d@:&/()\[\],;\"“”]", line):
        return False
    tokens = line.split()
    if not 2 <= len(tokens) <= 5:
        return False
    for token in tokens:
        bare = token.strip(".").replace("’", "'")
        if token.lower() in _PARTICLES:
            continue
        if not bare or not all(ch.isalpha() or ch in ".'-" for ch in bare):
            return False
        if not bare[0].isupper():
            return False
        if len(bare) > 1 and bare.isupper() and bare not in _ROMAN:
            return False
        if bare.lower().strip("'") in _NOT_A_NAME:
            return False
    return True


def _clean_name(line: str) -> str:
    return " ".join(line.replace("/s/", "").split()).strip(" ,")


def parse_signature(text: str, known_firms: Iterable[str] = ()) -> Signature:
    """The first signature block of a filing: from the "/s/" line to the
    "Counsel for ..." line under it.

    The names in it come before the firm and office they belong to, so each
    name goes to the next firm line below it -- which is what keeps two firms
    signing one notice apart.
    """
    lines = [" ".join(line.split()) for line in text.splitlines()]
    start = next((i for i, line in enumerate(lines) if line.startswith("/s/")), None)
    if start is None:
        return Signature()

    firm_keys = {firm_key(firm): firm for firm in known_firms if firm}
    signature = Signature()
    pending: list[str] = []
    last_firm: str | None = None

    def is_firm(line: str) -> bool:
        key = firm_key(line)
        if not key or not (key in firm_keys or _FIRM_SUFFIX_RE.search(line)):
            return False
        # Some firms sign each partner as their own professional corporation
        # ("Paul F. Brinkman, P.C."); that is a person, not another firm.
        return not is_person_name(line.split(",")[0])

    for index, line in enumerate(lines[start : start + 120]):
        if index and _BLOCK_END_RE.search(line):
            break
        for email in _EMAIL_RE.findall(line):
            if email not in signature.emails:
                signature.emails.append(email)
        candidate = _clean_name(line) if index == 0 else line
        if _FIRM_SUFFIX_RE.search(candidate) and not is_firm(candidate):
            candidate = candidate.split(",")[0]
        if index and is_firm(line):
            last_firm = line.strip(" ,")
            signature.attorneys.extend((name, last_firm) for name in pending)
            pending = []
        elif is_person_name(candidate):
            name = _NAME_SUFFIX_RE.sub(lambda m: f", {m.group(1)}", _clean_name(candidate))
            if name_key(name) not in {name_key(n) for n in pending} | {
                name_key(n) for n, _ in signature.attorneys
            }:
                pending.append(name)
    signature.attorneys.extend((name, last_firm) for name in pending)
    return signature


def pdf_text(path: Path) -> str:
    from pypdf import PdfReader  # only this process needs it

    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


# A notice is a page or two plus a certificate of service; its reason and
# signature block are near the front.
NOTICE_OCR_PAGES = 4
OCR_CACHE_DIR = "counsel_text"


def notice_reader(cache_dir: Path, *, log: Logger = print) -> Callable[[Path], str]:
    """A PDF reader that OCRs a scanned notice (one with next to no text
    layer), using the claims analysis's local OCR, and keeps the result in
    `cache_dir` so each scanned notice is read once rather than on every
    rebuild. Without OCR installed a scanned notice reads as empty, as before.
    """
    from .claims import ocr

    def read(path: Path) -> str:
        text = pdf_text(path)
        if len(text.strip()) >= ocr.MIN_TEXT_CHARS:
            return text
        cached = Path(cache_dir) / f"{path.name}.ocr.txt"
        if cached.exists():
            return cached.read_text(encoding="utf-8")
        if not ocr.available():
            return text
        text = ocr.pdf_text(path, max_pages=NOTICE_OCR_PAGES, log=log)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(text, encoding="utf-8")
        return text

    return read


# --------------------------------------------------------------------------
# Building a case's representations


@dataclass
class _Attorney:
    spellings: Counter = field(default_factory=Counter)
    lead: bool = False
    withdrawn_on: str | None = None
    sources: set = field(default_factory=set)
    order: int = 0

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.spellings.most_common(1)[0][0], "sources": sorted(self.sources)}
        if self.lead:
            out["lead"] = True
        if self.withdrawn_on:
            out["withdrawn_on"] = self.withdrawn_on
        return out


@dataclass
class _Representation:
    key: str
    spellings: Counter = field(default_factory=Counter)
    appearance_parties: dict[str, dict[str, Any]] = field(default_factory=dict)
    filing_parties: dict[str, dict[str, Any]] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)
    attorneys: list[_Attorney] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    appearances: list[dict[str, Any]] = field(default_factory=list)
    filings: int = 0
    first_filed: str | None = None
    last_filed: str | None = None

    def attorney(self, name: str, source: str) -> _Attorney:
        """The attorney by this name, merging near-identical spellings."""
        key = name_key(name)
        for attorney in self.attorneys:
            if any(_similar(key, name_key(s)) >= ATTORNEY_MATCH for s in attorney.spellings):
                break
        else:
            attorney = _Attorney(order=len(self.attorneys))
            self.attorneys.append(attorney)
        attorney.spellings[name] += 1
        attorney.sources.add(source)
        return attorney

    def filed(self, day: str | None) -> None:
        self.filings += 1
        if day:
            self.first_filed = min(filter(None, (self.first_filed, day)))
            self.last_filed = max(filter(None, (self.last_filed, day)))

    def parties(self) -> list[dict[str, Any]]:
        return list((self.appearance_parties or self.filing_parties).values())

    def to_dict(self) -> dict[str, Any]:
        attorneys = sorted(
            self.attorneys,
            key=lambda a: (not a.lead, a.withdrawn_on is not None, a.order),
        )
        parties = self.parties()
        out: dict[str, Any] = {
            "firm": self.spellings.most_common(1)[0][0],
            "firm_key": self.key,
            "roles": sorted({p["role"] for p in parties if p.get("role")}),
            "parties": parties,
            "attorneys": [a.to_dict() for a in attorneys],
            "emails": self.emails,
            "appearances": self.appearances,
            "filings": self.filings,
            "first_filed": self.first_filed,
            "last_filed": self.last_filed,
        }
        if not parties and self.unmatched:
            out["on_behalf_of"] = self.unmatched
        return out


def case_participants(case: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every party IDS lists for the case, across all its stages, once each."""
    seen: dict[tuple[str, Any], dict[str, Any]] = {}
    for stage in (case or {}).get("stages") or []:
        for item in (stage.get("lists") or {}).get("participants") or []:
            name = item.get("name")
            if name and (name, item.get("role")) not in seen:
                seen[(name, item.get("role"))] = {
                    key: item[key] for key in ("name", "role", "participant_id") if item.get(key) is not None
                }
    return list(seen.values())


def _is_alias(rep: _Representation, reps: Iterable[_Representation]) -> bool:
    """A "firm" that never appeared and whose every attorney belongs to one
    that did: a misspelling of it in EDIS's firm field, or the filing vendor
    an attorney happened to file one document through.
    """
    if rep.appearances or not rep.attorneys:
        return False
    for other in reps:
        if other is rep or not other.appearances:
            continue
        others = {name_key(s) for a in other.attorneys for s in a.spellings}
        if all(any(name_key(s) in others for s in a.spellings) for a in rep.attorneys):
            return True
    return False


def _party_ref(party: dict[str, Any]) -> str:
    return f"{party.get('role')}|{party.get('name')}"


def _day(doc: dict[str, Any]) -> str | None:
    value = doc.get("document_date") or doc.get("official_received_date")
    return str(value)[:10] if value else None


def _appearance_pdfs(docs_dir: Path, doc_id: Any) -> list[Path]:
    if not doc_id or not docs_dir.is_dir():
        return []
    return sorted(docs_dir.glob(f"{doc_id}_*.pdf"))


def build_case_counsel(
    case: dict[str, Any] | None,
    documents: list[dict[str, Any]],
    *,
    docs_dir: Path,
    read_pdf: Callable[[Path], str] = pdf_text,
    log: Logger = print,
) -> dict[str, Any]:
    participants = case_participants(case)
    reps: dict[str, _Representation] = {}
    rosters = 0

    def rep_for(firm: str) -> _Representation:
        key = firm_key(firm)
        rep = reps.setdefault(key, _Representation(key=key))
        rep.spellings[firm.strip(" ,")] += 1
        return rep

    ordered = sorted(documents, key=lambda d: dates.sort_key(_day(d)))

    # Non-parties join the case's parties for matching, so their counsel is
    # found exactly as a respondent's is. A name IDS already lists as a party
    # stays that party.
    non_parties: list[dict[str, Any]] = []
    # Each non-party's own filings, which are the reason it is in the case
    # when it never filed a notice saying so.
    own_filings: dict[str, list[dict[str, Any]]] = {}
    # Every spelling each was filed under; the commonest is the one shown.
    spellings: dict[str, Counter] = {}
    for doc in ordered:
        for name in non_party_names(doc):
            if match_parties(name, participants):
                continue
            # "Google Inc." and "Google LLC" in one case are the same company
            # under an old and a new corporate form.
            known = _match_with_non_parties(name, non_parties)
            if not known:
                known = [{"name": name, "role": NON_PARTY_ROLE}]
                non_parties.extend(known)
            spellings.setdefault(known[0]["name"], Counter())[name] += 1
            own_filings.setdefault(known[0]["name"], []).append(
                {"id": str(doc.get("id") or ""), "date": _day(doc), "title": doc.get("title")}
            )
    participants = participants + non_parties
    # Why each non-party is here, from the first notice that says so.
    reasons: dict[str, dict[str, Any]] = {}
    # Firms that speak for a party, first; filings from firms that never do
    # (a trade group's public-interest comment) are not counsel of record.
    for doc in ordered:
        if _is_commission(doc) or not doc.get("firm_organization"):
            continue
        parties = _match_with_non_parties(doc.get("on_behalf_of"), participants)
        roles = {p.get("role") for p in parties}
        if len(roles) > 1:  # filed jointly by both sides
            continue
        appearance = is_appearance(doc) and not is_withdrawal(doc)
        if not parties and not appearance:
            continue
        facts = parse_title(doc.get("title")) if appearance else TitleFacts()
        for firm in facts.firms or [doc["firm_organization"]]:
            rep = rep_for(firm)
            target = rep.appearance_parties if appearance else rep.filing_parties
            for party in parties:
                target.setdefault(_party_ref(party), party)
            if not parties and doc.get("on_behalf_of") and doc["on_behalf_of"] not in rep.unmatched:
                rep.unmatched.append(doc["on_behalf_of"])

    for doc in ordered:
        if _is_commission(doc) or not doc.get("firm_organization"):
            continue
        rep = reps.get(firm_key(doc["firm_organization"]))
        if rep is None:
            continue
        doc_id = str(doc.get("id") or "")
        day = _day(doc)
        rep.filed(day)
        rep.spellings[doc["firm_organization"].strip(" ,")] += 1
        if doc.get("filed_by"):
            rep.attorney(doc["filed_by"], doc_id)

        if is_withdrawal(doc):
            facts = parse_title(doc.get("title"))
            target = reps.get(firm_key(facts.firms[0])) if facts.firms else rep
            for name in facts.withdrawn:
                (target or rep).attorney(name, doc_id).withdrawn_on = day
            continue
        if not is_appearance(doc):
            continue

        facts = parse_title(doc.get("title"))
        pdfs = [str(p.name) for p in _appearance_pdfs(docs_dir, doc_id)]
        for firm in facts.firms or [doc["firm_organization"]]:
            named = reps.get(firm_key(firm))
            if named is not None:
                named.appearances.append(
                    {"id": doc_id, "date": day, "title": doc.get("title"), "files": pdfs}
                )
        if facts.lead:
            rep.attorney(facts.lead, doc_id).lead = True

        appearing_for = _match_with_non_parties(doc.get("on_behalf_of"), non_parties)
        for party in appearing_for:
            # The notice is recorded even before its PDF is downloaded, so the
            # page can say where the reason will come from.
            reasons.setdefault(party["name"], {"notice": {"id": doc_id, "date": day, "files": pdfs}})

        for path in _appearance_pdfs(docs_dir, doc_id):
            try:
                text = read_pdf(path)
            except Exception as exc:  # one unreadable PDF should not stop the rest
                log(f"    ! could not read {path.name}: {exc}")
                continue
            reason = limited_purpose(text) if appearing_for else None
            for party in appearing_for:
                known_reason = reasons[party["name"]]
                if reason and "purpose" not in known_reason:
                    known_reason.update(reason)
                    known_reason["notice"] = {"id": doc_id, "date": day, "files": pdfs}
            known = [firm for r in reps.values() for firm in r.spellings] + facts.firms
            signature = parse_signature(text, known_firms=known)
            if not signature.attorneys:
                continue
            rosters += 1
            for name, firm_line in signature.attorneys:
                target = rep
                if firm_line and firm_key(firm_line) != rep.key:
                    # Co-counsel from another firm on the same notice acts
                    # for the same parties.
                    target = reps.get(firm_key(firm_line)) or rep_for(firm_line)
                    for party in rep.parties():
                        target.appearance_parties.setdefault(_party_ref(party), party)
                target.attorney(name, doc_id)
            for email in signature.emails:
                if email not in rep.emails:
                    rep.emails.append(email)

    kept = [rep for rep in reps.values() if (rep.attorneys or rep.parties()) and not _is_alias(rep, reps.values())]
    # Renamed in place, so the representations that list a non-party show
    # the same spelling as the non-party list does.
    first_seen = [party["name"] for party in non_parties]
    for party in non_parties:
        party["name"] = spellings[party["name"]].most_common(1)[0][0]

    representations = [rep.to_dict() for rep in kept]
    representations.sort(key=lambda r: (r["first_filed"] or "9999", r["firm"]))
    return {
        "representations": representations,
        "non_parties": [
            {
                **party,
                **reasons.get(key, {}),
                "summary": _non_party_summary(reasons.get(key, {}), own_filings.get(key, [])),
                "filings": own_filings.get(key, []),
            }
            for key, party in zip(first_seen, non_parties)
        ],
        "rosters_read": rosters,
    }


# --------------------------------------------------------------------------
# The process


@dataclass
class CounselReport:
    cases: int = 0
    representations: int = 0
    rosters_read: int = 0
    non_parties: int = 0
    non_party_cases: int = 0
    non_party_status: Counter = field(default_factory=Counter)
    unmatched: list[str] = field(default_factory=list)


_STATUS_WORDS = {
    "read": "reason read from its notice",
    "no_reason": "notice read, gives no reason",
    "not_downloaded": "notice PDF not downloaded yet",
    "no_notice": "no notice filed; described from its own filings",
}


def _non_party_lines(report: CounselReport) -> list[str]:
    """The non-parties in one line, plus a line saying what would fill in
    the reasons still missing.
    """
    if not report.non_parties:
        return []
    counts = report.non_party_status
    parts = [
        f"{counts['read']} with the reason read from their notice",
        f"{counts['not_downloaded']} whose notice PDF is not downloaded yet",
        f"{counts['no_reason']} whose notice gives no reason",
        f"{counts['no_notice']} that filed no notice (described from their own filings)",
    ]
    lines = [
        f"Non-parties: {report.non_parties} in {report.non_party_cases} case(s): "
        + "; ".join(part for part in parts if not part.startswith("0 ")) + "."
    ]
    if counts["not_downloaded"]:
        lines.append(
            "  To read the missing notices: Run daily sync on the dashboard, or "
            "'python cli.py docs --existing --appearances', then rebuild counsel."
        )
    return lines


def run(store: Store, *, verbose: bool = False, log: Logger = print) -> CounselReport:
    """Rebuild data/counsel.json from the documents and cases on disk.

    Non-parties are summed up in one line; `verbose` lists each one.
    """
    report = CounselReport()
    counsel: dict[str, Any] = {}
    for key, documents in sorted(store.documents.items()):
        if not documents:
            continue
        built = build_case_counsel(
            store.investigations.get(key),
            documents,
            docs_dir=store.docs_dir / key,
            read_pdf=notice_reader(store.data_dir / OCR_CACHE_DIR / key, log=log),
            log=log,
        )
        if not built["representations"]:
            continue
        counsel[key] = built
        report.cases += 1
        report.representations += len(built["representations"])
        report.rosters_read += built["rosters_read"]
        if built["non_parties"]:
            report.non_party_cases += 1
        for party in built["non_parties"]:
            status = non_party_status(party)
            report.non_parties += 1
            report.non_party_status[status] += 1
            if verbose:
                log(f"  {key}: non-party {party['name']} ({_STATUS_WORDS[status]})")
        for rep in built["representations"]:
            if not rep["parties"]:
                report.unmatched.append(f"{key}: {rep['firm']} for {'; '.join(rep.get('on_behalf_of') or [])}")

    store.counsel = counsel
    store.save_counsel()
    store.record_run(
        "counsel",
        cases=report.cases,
        representations=report.representations,
        rosters_read=report.rosters_read,
    )
    store.save_state()
    log(
        f"Counsel: {report.representations} representation(s) across {report.cases} case(s), "
        f"{report.rosters_read} appearance roster(s) read from PDFs."
    )
    for line in _non_party_lines(report):
        log(line)
    if report.non_parties and not verbose:
        log("  ('python cli.py counsel --verbose' lists each non-party.)")
    for line in report.unmatched:
        log(f"  ? no IDS party matched {line}")
    return report
