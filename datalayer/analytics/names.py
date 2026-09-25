"""Deterministic name handling: normalizing, splitting, parsing.

Everything here is a pure function of the text, so the same spelling always
gives the same key and a merge can be explained by pointing at the rule.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

# --------------------------------------------------------------------------
# Common


def fold(text: Any) -> str:
    """Lower case, accents off ("Séké" -> "seke"), "&" as "and", and the
    "&;" HTML-escaping leftover EDIS sometimes has ("Kirkland &; Ellis").
    """
    text = unicodedata.normalize("NFKD", str(text or "")).replace("&;", "&")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.casefold().replace("&", " and ").replace("’", "'")


def words(text: Any) -> list[str]:
    """The words of a name with punctuation gone: "Co., Ltd." -> co ltd,
    "B.V." -> bv, "O'Melveny" -> omelveny, "P. L. L.C." -> pllc.
    """
    text = _SPACED_INITIALS_RE.sub(".", fold(text))
    return re.sub(r"[^\w\s]", "", re.sub(r"[,/|]", " ", text)).split()


# "P. L. L.C.": initials written with spaces between them.
_SPACED_INITIALS_RE = re.compile(r"(?<=\b[a-z])\.\s+(?=[a-z]\.)")


# --------------------------------------------------------------------------
# Firms

# Legal forms law firms end in, as words (after punctuation is removed).
FIRM_FORMS = {
    "llp", "llc", "pc", "pllc", "pa", "plc", "ltd", "lpa", "lp", "pl", "chtd", "chartered",
    "professional", "corporation", "corporatio", "association", "inc",
}
# "A Professional Corporation" and "US LLP" end some names.
_FIRM_TAIL_WORDS = FIRM_FORMS | {"a", "us", "usa", "llpa", "ll"}
_FIRM_LEAD_RE = re.compile(
    r"^(?:the\s+)?(?:law\s+offices?\s+of|law\s+firm\s+of|amicus\s+curiae)\s+|^the\s+", re.I
)
# "(US)", "(DC)", "(CA)": an office, not another firm.
_PARENTHETICAL_RE = re.compile(r"\s*\([^)]*\)")
# Something that ends a law firm's name, as written.
FIRM_SUFFIX_RE = re.compile(
    r"\b(?:L\.?\s?L\.?\s?P|L\.?\s?L\.?\s?C|P\.?\s?L\.?\s?L\.?\s?C|P\.\s?C|PC|P\.\s?A|PLC|Chtd)\.?(?=[\s,;]|$)",
    re.I,
)


def firm_core(name: Any) -> list[str]:
    """The words that identify a firm: no leading "The"/"Law Offices of", no
    office in parentheses, no legal form, no "and".

    "Finnegan, Henderson, Farabow, Garrett & Dunner, L.L.P." and
    "finnegan henderson farabow garrett and dunner" both give
    [finnegan, henderson, farabow, garrett, dunner].
    """
    text = _PARENTHETICAL_RE.sub(" ", str(name or ""))
    text = _FIRM_LEAD_RE.sub("", " ".join(text.split()))
    tokens = [w for w in words(text) if w != "and"]
    while len(tokens) > 1 and tokens[-1] in _FIRM_TAIL_WORDS:
        tokens.pop()
    return tokens


def firm_key(name: Any) -> str:
    return " ".join(firm_core(name))


# Splitting a firm field that names several firms.
_FIRM_SEPARATOR_RE = re.compile(r"\s*;\s*|\s+/\s+")
_AFTER_SUFFIX_RE = re.compile(
    FIRM_SUFFIX_RE.pattern + r"\.?(?:\s*\([^)]*\))?,?\s+(?P<joiner>and\s+|&\s+)?(?=[A-Z])", re.I
)
# "Fox PLLCand Latham": a suffix run into the next word.
_RUN_ON_SUFFIX_RE = re.compile(r"\b(L\.?L\.?P|P\.?L\.?L\.?C|L\.?L\.?C|P\.?C)\.?(and)\s", re.I)
FILLER = {"et", "al", "and"}


@dataclass
class FirmSplit:
    parts: list[str]
    rule: str | None = None  # why it was split, if it was
    review: bool = False  # looks like several firms, but no clean split


def split_firm_field(raw: str, known: "KnownFirms | None" = None) -> FirmSplit:
    """One firm field -> the firms in it.

    1. ";" and " / " always separate firms.
    2. A legal suffix followed by more text ("A LLP and B LLP", "A LLP, B
       PC") ends one firm and starts the next -- but only where the text
       after it is itself a firm (ends in a suffix, or is a known firm), so
       "Smith, Jones & Lee LLP" is never split at its commas.
    3. Old filings run several firms together with no separator at all
       ("fenwick and west finnegan henderson ... morrison and foerster");
       those are split only when every word is covered by firms seen
       elsewhere (`known`), and only when the field has no legal suffix of
       its own -- "Wilmer Cutler Pickering Hale and Dorr LLP" is one firm.
    Anything that still looks like several firms is flagged for review.
    """
    raw = " ".join(str(raw or "").replace("&;", "&").split())
    raw = _RUN_ON_SUFFIX_RE.sub(r"\1 \2 ", raw)
    pieces = [p for p in _FIRM_SEPARATOR_RE.split(raw) if p.strip()]
    rule = "separator" if len(pieces) > 1 else None

    split: list[str] = []
    for piece in pieces:
        parts = _split_after_suffix(piece, known)
        if len(parts) > 1:
            rule = rule or "suffix"
        split.extend(parts)

    if len(split) == 1 and known is not None and not FIRM_SUFFIX_RE.search(split[0]):
        segmented = known.segment(firm_core(split[0]))
        if segmented:
            return FirmSplit(parts=segmented, rule="known firms")
    if len(split) == 1 and _looks_multiple(split[0]):
        return FirmSplit(parts=split, review=True)
    return FirmSplit(parts=[p.strip(" ,") for p in split], rule=rule)


def _split_after_suffix(text: str, known: "KnownFirms | None", *, listing: bool = False) -> list[str]:
    """`listing`: already inside "A LLP, B LLP, and C", where the last item
    after "and" is a firm even without a suffix ("..., and WilmerHale").
    """
    for match in _AFTER_SUFFIX_RE.finditer(text):
        left, right = text[: match.end()].strip(" ,&"), text[match.end():].strip(" ,")
        left = re.sub(r"\s+(?:and|&)$", "", left).strip(" ,")
        last_item = (listing or "," in text[: match.start()] or match.group("joiner")) and match.group(
            "joiner"
        ) and "," not in right and len(right.split()) <= 5
        if right and (
            FIRM_SUFFIX_RE.search(right) or (known and known.is_known(firm_core(right))) or last_item
        ):
            return [left, *_split_after_suffix(right, known, listing=True)]
    return [text]


def _looks_multiple(text: str) -> bool:
    """A firm field that probably names several firms: "et al", or two legal
    suffixes.
    """
    return bool(re.search(r"\bet\s+al\b", text, re.I)) or len(FIRM_SUFFIX_RE.findall(text)) > 1


class KnownFirms:
    """Firm cores seen on their own, for splitting run-together fields.

    A core only counts as known when some spelling of it ends in a legal
    suffix or it acts in several cases -- a one-off oddity is not a firm to
    split other text by.
    """

    def __init__(self, cores: Iterable[tuple[str, ...]]) -> None:
        self.cores = {tuple(c) for c in cores if len(c) >= 1}
        # Two-word-or-longer prefixes of a known core, when only one core has it
        # ("finnegan henderson" for Finnegan, Henderson, Farabow, ...).
        owners: dict[tuple[str, ...], set[tuple[str, ...]]] = {}
        for core in self.cores:
            for size in range(2, len(core)):
                owners.setdefault(core[:size], set()).add(core)
        self.prefixes = {prefix: next(iter(o)) for prefix, o in owners.items() if len(o) == 1}

    def is_known(self, core: list[str]) -> bool:
        return tuple(core) in self.cores

    def segment(self, core: list[str]) -> list[str] | None:
        """Cover `core` with two or more known firms (or unique prefixes of
        them, two words or longer), longest first, skipping "and" and "et al"
        between them. Returns the firms' cores, or None when it cannot: a
        word no known firm covers, or only one firm.
        """
        tokens = tuple(core)
        found: list[str] = []
        index = 0
        while index < len(tokens):
            if tokens[index] in FILLER:
                index += 1
                continue
            for end in range(len(tokens), index + 1, -1):
                piece = tokens[index:end]
                if piece == tokens:
                    continue  # the whole thing is not a piece of itself
                if piece in self.cores:
                    found.append(" ".join(piece))
                    break
                if piece in self.prefixes:
                    found.append(" ".join(self.prefixes[piece]))
                    break
            else:
                return None
            index = end
        return found if len(found) > 1 else None


# --------------------------------------------------------------------------
# Companies

# Corporate forms, as they appear at the end of a name (after punctuation is
# removed), each with its canonical spelling.
_FORM_WORDS = {
    "inc": "inc", "incorporated": "inc", "corp": "corp", "corporation": "corp",
    "co": "co", "company": "co", "llc": "llc", "ltd": "ltd", "limited": "ltd",
    "lp": "lp", "llp": "llp", "plc": "plc", "gmbh": "gmbh", "ag": "ag", "kg": "kg",
    "sa": "sa", "sas": "sas", "sarl": "sarl", "srl": "srl", "spa": "spa", "bv": "bv",
    "nv": "nv", "oy": "oy", "oyj": "oyj", "ab": "ab", "as": "as", "asa": "asa",
    "aps": "aps", "pte": "pte", "pty": "pty", "kk": "kk", "se": "se", "sro": "sro",
    "spol": "spol", "sl": "sl", "sau": "sau", "bhd": "bhd", "sdn": "sdn", "pvt": "pvt",
    "tic": "tic", "san": "san", "ve": "ve", "cv": "cv", "de": "de",
}
# "de" and "cv" only as part of "S.A. de C.V.".
_FORM_ONLY_AFTER = {"de", "cv", "ve", "san", "tic"}
_TRAILING_NUMBER_RE = re.compile(r"\s*\(\d+\)\s*$")

ALIAS_FORMER_RE = re.compile(
    r"\s*[,(]?\s*\b(?:f/k/a|fka|formerly\s+known\s+as|formerly)\b\.?\s*", re.I
)
ALIAS_NOW_RE = re.compile(r"\s*[,(]?\s*\b(?:n/k/a|nka|now\s+known\s+as)\b\.?\s*", re.I)
ALIAS_TRADE_RE = re.compile(
    r"\s*[,(]?\s*\b(?:d/b/a|dba|doing\s+business\s+as|a/k/a|aka|t/a|trading\s+as)\b\.?\s*", re.I
)


@dataclass
class CompanyName:
    primary: str
    former: list[str]  # names it used to go by: merge with entities so named
    trade: list[str]  # names it trades under: shown and searched, never merged on


def parse_company(name: Any) -> CompanyName:
    """ "HydraFacial LLC f/k/a Edge Systems LLC" -> HydraFacial LLC, formerly
    Edge Systems LLC. "Sam's East, Inc. (d/b/a Sam's Club)" -> trade name
    Sam's Club, which Sam's West shares -- so trade names never merge
    companies; former names do.
    """
    text = " ".join(_TRAILING_NUMBER_RE.sub("", str(name or "")).split())
    former: list[str] = []
    trade: list[str] = []

    def cut(pattern: re.Pattern[str], text: str) -> tuple[str, list[str]]:
        pieces = pattern.split(text)
        return pieces[0], [p.strip(" ,()") for p in pieces[1:] if p.strip(" ,()")]

    text, now = cut(ALIAS_NOW_RE, text)
    if now:  # "X n/k/a Y": Y is the name now, X the former one
        former.append(text.strip(" ,()"))
        text = now[0]
    text, trades = cut(ALIAS_TRADE_RE, text)
    text, formers = cut(ALIAS_FORMER_RE, text)
    for alias in trades:
        alias, more_former = cut(ALIAS_FORMER_RE, alias)
        trade.extend(a.strip() for a in re.split(r"\s*/\s*|\s+and\s+(?=d/b/a)", alias) if a.strip())
        former.extend(more_former)
    former.extend(formers)
    return CompanyName(primary=text.strip(" ,()"), former=former, trade=trade)


def company_form(name: Any) -> tuple[list[str], str]:
    """(core words, canonical corporate form). "Samsung Electronics, Co.,
    Ltd." -> ([samsung, electronics], "co ltd"); "LG Electronics" ->
    ([lg, electronics], "").
    """
    tokens = words(_TRAILING_NUMBER_RE.sub("", str(name or "")))
    form: list[str] = []
    while len(tokens) > 1:
        if tokens[-1] in _FORM_WORDS:
            form.insert(0, _FORM_WORDS[tokens.pop()])
        elif tokens[-1] == "and" and form and len(tokens) > 2 and tokens[-2] in _FORM_WORDS:
            tokens.pop()  # "GmbH & Co. KG"
        else:
            break
    # "de" / "cv" etc. are forms only next to another form ("S.A. de C.V.").
    while form and form[0] in _FORM_ONLY_AFTER and len(form) == 1:
        tokens.append(form.pop(0))
    core = [t for t in tokens if t != "and"] or tokens
    return core, " ".join(form)


def company_key(name: Any) -> str:
    core, form = company_form(name)
    return " ".join(core) + (f" [{form}]" if form else "")


# Words that say nothing about which company it is: places, and the parts of
# names every other company has. Used to find the distinctive words.
GENERIC_COMPANY_WORDS = {
    "the", "of", "and", "shenzhen", "guangzhou", "dongguan", "ningbo", "hangzhou", "xiamen",
    "zhejiang", "jiangsu", "beijing", "shanghai", "suzhou", "foshan", "zhongshan", "yiwu",
    "hong", "kong", "hk", "china", "chinese", "taiwan", "japan", "korea", "usa", "us", "america",
    "american", "americas", "north", "international", "intl", "global", "group", "holding",
    "holdings", "technology", "technologies", "tech", "electronic", "electronics", "industrial",
    "industries", "industry", "trade", "trading", "manufacturing", "products", "product",
    "enterprises", "enterprise", "import", "export", "imports", "exports", "commerce", "commercial",
    "e", "science", "sciences", "co", "city", "new", "development", "materials", "systems",
    "solutions", "services", "e-commerce", "ecommerce", "store", "shop", "online", "network",
    "networks", "usa", "uk", "de", "europe", "european", "asia", "pacific", "grupo", "groupe", "sz",
    # Chinese cities and provinces, which lead many company names.
    "zhuhai", "guangdong", "changzhou", "wenzhou", "wuxi", "qingdao", "fujian", "jinhua", "taizhou",
    "nanjing", "tianjin", "chongqing", "chengdu", "wuhan", "xian", "hefei", "jiaxing", "huizhou",
    "jiangmen", "shantou", "quanzhou", "fuzhou", "jinan", "zhengzhou", "changsha", "nanchang",
    "kunshan", "cixi", "yuyao", "shaoxing", "huzhou", "dalian", "shenyang", "harbin", "anhui",
    "hubei", "hunan", "henan", "hebei", "shandong", "sichuan", "yunnan", "guangxi", "jiangxi",
    "shaanxi", "shanxi", "liaoning", "jilin", "zhangzhou", "putian", "yongkang", "danyang",
    "nantong", "yangzhou", "changshu", "jiangyin", "zhangjiagang", "linyi", "weifang", "yantai",
    "zibo", "xuzhou", "shenzen", "shengzhen", "guang", "dong", "hangzhou",
}
# First words too common to name a family by.
_COMMON_FIRST_WORDS = {
    "advanced", "best", "smart", "golden", "green", "blue", "royal", "star", "power", "super",
    "digital", "pro", "first", "general", "national", "united", "universal", "car", "home",
    "great", "top", "sun", "grand", "prime", "premier", "eagle", "delta", "alpha", "omega",
    "one", "max", "ultra", "mega", "micro", "medical", "precision", "creative", "modern",
    "classic", "direct", "easy", "fast", "good", "happy", "lucky", "perfect", "quality", "wonder",
}


def distinctive(core: Iterable[str]) -> list[str]:
    return [w for w in core if w not in GENERIC_COMPANY_WORDS and not w.isdigit()]


def family_key(core: list[str]) -> str | None:
    """The brand a company's name leads with ("samsung" for Samsung
    Electronics America and Samsung SDI), for the optional family view.
    None when the first distinctive word is too common to group by.
    """
    special = distinctive(core)
    if not special:
        return None
    first = special[0]
    if first in _COMMON_FIRST_WORDS or len(first) < 2:
        return None
    return first


# --------------------------------------------------------------------------
# People

_PERSON_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "esq", "phd", "md"}
_HONORIFICS = {"dr", "mr", "mrs", "ms", "prof", "hon"}


@dataclass(frozen=True)
class PersonName:
    first: str
    middles: tuple[str, ...]
    last: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.first, self.last)

    def full(self) -> str:
        return " ".join([self.first, *self.middles, self.last])


def parse_person(name: Any) -> PersonName | None:
    """ "James A. Fussell, III" -> james / (a,) / fussell. "Domingo Manuel
    LLagostera" -> domingo / (manuel,) / llagostera. Particles stay with the
    surname ("Bas de Blank" -> bas / () / de blank).
    """
    tokens = [t for t in words(name) if t not in _PERSON_SUFFIXES]
    while tokens and tokens[0] in _HONORIFICS:
        tokens.pop(0)
    if len(tokens) < 2:
        return None
    last = [tokens.pop()]
    while len(tokens) > 1 and tokens[-1] in {"de", "da", "di", "del", "van", "von", "la", "le", "du", "der", "st"}:
        last.insert(0, tokens.pop())
    return PersonName(first=tokens[0], middles=tuple(tokens[1:]), last=" ".join(last))


def compatible_part(a: str, b: str) -> bool:
    """ "t" and "tanner" agree; "t" and "s" do not."""
    if a == b:
        return True
    return (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b))


def compatible_people(a: PersonName, b: PersonName) -> bool:
    """Whether two names could be one person: same surname, first names that
    agree (or one is the other's initial), and middle names that agree
    wherever both give one.
    """
    if a.last != b.last or not compatible_part(a.first, b.first):
        return False
    for x, y in zip(a.middles, b.middles):
        if not compatible_part(x, y):
            return False
    return True
