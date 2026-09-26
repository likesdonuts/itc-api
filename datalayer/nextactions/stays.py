"""Stays, and respondents no longer in the case (phase 3).

Both are read from the titles of what the ALJ and the Commission issued,
which say it plainly:

    "Granting Joint Motion to Stay the Procedural Schedule"          whole case
    "Staying the Investigation for Fourteen Days"                    whole case, ends
    "Granting Joint Motion to Extend Stay of Procedural Deadlines
     until September 30, 2026"                                        whole case, ends
    "Extending Stay of Procedural Schedule as to HP"                 one respondent
    "Initial Determination Terminating the Investigation as to X"    X out, pending review
    "Commission Determination Not to Review an Initial
     Determination Terminating the Investigation as to X"            X out, final
    "Initial Determination Finding Respondent X in Default"          X in default

A whole-case stay is taken as over once an order lifts it, once its own end
date has passed, or once a later order sets the procedural schedule again
(courts rarely say "the stay is lifted" when they reschedule). An order that
denies a stay is not one. A stay for some respondents leaves the schedule
running for the rest; it is shown, not applied to the dates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

ISSUED_TYPES = ("Order", "Order, Commission", "Notice", "ID/RD - Other Than Final on Violation")

_ORDER_PREFIX = r"^\W*(?:order\s+no\.?\s*\d+\s*:?\s*)?(?:\(\d+\)\s*)?"
_STAY_RE = re.compile(r"\bstay(?:s|ing|ed)?\b", re.I)
_DENIED_RE = re.compile(_ORDER_PREFIX + r"denying\b", re.I)
_LIFT_RE = re.compile(
    r"\b(?:lift(?:s|ing)?|dissolv\w*|terminat\w*|end(?:s|ing)?|vacat\w*)\s+(?:the\s+)?stay\b", re.I
)
_PARTIAL_RE = re.compile(
    r"\bstay\w*\b.*?\b(?:as\s+to|with\s+respect\s+to)\s+(?P<who>.+?)(?:\s+pending\b|\s*;|\s*\[|\s*\(\d|$)", re.I
)
_UNTIL_RE = re.compile(r"\buntil\s+(?P<date>[A-Z][a-z]+\.?\s+\d{1,2},?\s+\d{4})")
_FOR_DAYS_RE = re.compile(r"\bfor\s+(?P<n>[\w-]+)\s+days\b", re.I)
_NUMBERS = {
    "seven": 7, "ten": 10, "fourteen": 14, "fifteen": 15, "twenty": 20, "twenty-one": 21, "thirty": 30,
    "forty-five": 45, "sixty": 60, "ninety": 90,
}
# A later order that sets dates again means the stay has run its course.
_RESCHEDULE_RE = re.compile(
    r"(?:setting|amending|modifying|revis\w*|adopt\w*|resum\w*)\s+(?:the\s+)?(?:amended\s+|revised\s+)?procedural\s+schedule"
    r"|procedural\s+schedule\W*$",
    re.I,
)

_TERMINATE_RE = re.compile(
    r"terminat\w*\s+(?:the\s+)?investigation\s+(?:in\s+part\s+)?(?:as\s+to|with\s+respect\s+to)\s+"
    r"(?P<who>.+?)(?:\s+(?:based|on\s+the\s+basis|pursuant|due|by\s+reason)\b"
    r"|\s+and\s+(?:limiting|stay\w*|amend\w*|modif\w*|granting|denying|setting|extending)\b|\s*;|\s*\[|\s*\(\d|$)",
    re.I,
)
# What the Commission's notices usually say instead of names.
_VAGUE_RE = re.compile(r"^(?:a|an|one|two|three|four|five|several|certain|all|the)?\s*"
                       r"(?:(?:remaining|defaulting)\s+)?respondents?(?:\s+groups?)?$", re.I)
_DEFAULT_RE = re.compile(r"finding\s+(?P<who>.+?)\s+in\s+default\b", re.I)
# Terminations "as to" these are claims or patents, not parties.
_NOT_A_PARTY_RE = re.compile(r"\bclaims?\b|\bpatents?\b|'\d{3}\b|\bU\.?S\.?\s+Pat", re.I)
_GENERIC = {"certain", "respondent", "respondents", "the", "and", "inc", "ltd", "llc", "corp", "co", "company",
            "corporation", "limited", "group", "technology", "technologies", "international", "america"}


def _day(doc: dict[str, Any]) -> str:
    return str(doc.get("document_date") or doc.get("official_received_date") or "")[:10]


def _source(doc: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(doc.get("id") or ""), "title": doc.get("title"), "date": _day(doc)}


def _issued(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (d for d in documents or [] if d.get("document_type") in ISSUED_TYPES and _day(d)
         and not str(d.get("title") or "").startswith("F.R.")),
        key=_day,
    )


def _clean_who(text: str) -> str:
    text = re.sub(r"^(?:the\s+)?(?:certain\s+)?respondents?\s+", "", text.strip(" .,:"), flags=re.I)
    return text.strip(" .,:")


@dataclass
class Stay:
    day: str
    title: str
    source: dict[str, Any]
    who: str | None  # None for the whole case
    until: str | None


def _until(title: str, day: str) -> str | None:
    match = _UNTIL_RE.search(title)
    if match:
        for fmt in ("%B %d, %Y", "%B %d %Y", "%b. %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(match.group("date").replace(",,", ","), fmt).date().isoformat()
            except ValueError:
                continue
    match = _FOR_DAYS_RE.search(title)
    if match:
        word = match.group("n").lower()
        days = int(word) if word.isdigit() else _NUMBERS.get(word)
        if days:
            return (date.fromisoformat(day) + timedelta(days=days)).isoformat()
    return None


def stays(documents: list[dict[str, Any]]) -> list[Stay]:
    found = []
    for doc in _issued(documents):
        title = str(doc.get("title") or "")
        if not _STAY_RE.search(title) or _DENIED_RE.search(title) or _LIFT_RE.search(title):
            continue
        partial = _PARTIAL_RE.search(title)
        who = _clean_who(partial.group("who")) if partial else None
        found.append(Stay(day=_day(doc), title=title, source=_source(doc), who=who or None, until=_until(title, _day(doc))))
    return found


def assess(documents: list[dict[str, Any]], today: date) -> dict[str, Any]:
    """{"stay": the whole-case stay in effect or None, "partial": stays for some
    respondents still in effect, "ended": a whole-case stay that has ended}."""
    issued = _issued(documents)
    lifts = [_day(d) for d in issued if _LIFT_RE.search(str(d.get("title") or ""))]
    reschedules = [
        _day(d) for d in issued
        if _RESCHEDULE_RE.search(str(d.get("title") or "")) and not _STAY_RE.search(str(d.get("title") or ""))
    ]
    out: dict[str, Any] = {"stay": None, "partial": [], "ended": None}
    whole = [s for s in stays(documents) if not s.who]
    if whole:
        latest = whole[-1]
        ended_by = None
        if any(day >= latest.day for day in lifts):
            ended_by = "lifted"
        elif latest.until and latest.until < today.isoformat():
            ended_by = f"ran to {latest.until}"
        elif any(day > latest.day for day in reschedules):
            ended_by = "a later order set the schedule again"
        # The stay began at the first stay order since the last one that ended.
        run = [s for s in whole if not any(latest.day >= day > s.day for day in lifts)]
        since = run[0].day if run else latest.day
        record = {"since": since, "until": latest.until, "source": latest.source, "extended": len(run) > 1}
        if ended_by:
            out["ended"] = record | {"ended_by": ended_by}
        else:
            out["stay"] = record
    for stay in stays(documents):
        if not stay.who:
            continue
        if any(day >= stay.day for day in lifts) or (stay.until and stay.until < today.isoformat()):
            continue
        # An extension replaces the earlier stay for the same respondents.
        out["partial"] = [p for p in out["partial"] if p["who"] != stay.who]
        out["partial"].append({"who": stay.who, "since": stay.day, "until": stay.until, "source": stay.source})
    return out


def out_of_case(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Respondents terminated from the case or found in default, oldest first.

    The ALJ's initial determination names them; it becomes final when the
    Commission declines to review it, in a notice that usually says only
    "Certain Respondents". So each named entry is marked final when such a
    notice follows it within 90 days, and vague notices add no entries.
    """
    found: dict[str, dict[str, Any]] = {}
    reviews: list[str] = []
    for doc in _issued(documents):
        title = str(doc.get("title") or "")
        if _DENIED_RE.search(title):
            continue
        commission = doc.get("document_type") == "Notice" and "not to review" in title.lower()
        for pattern, how in ((_TERMINATE_RE, "terminated"), (_DEFAULT_RE, "in default")):
            match = pattern.search(title)
            if not match:
                continue
            if commission:
                reviews.append(_day(doc))
            who = _clean_who(match.group("who"))
            if not who or _VAGUE_RE.match(who) or _NOT_A_PARTY_RE.search(who) or len(who) > 160:
                continue
            key = re.sub(r"[^a-z0-9]", "", who.lower())
            found.setdefault(key, {"who": who, "how": how, "date": _day(doc), "final": commission,
                                   "source": _source(doc)})
            if commission:
                found[key]["final"] = True
    for item in found.values():
        if not item["final"]:
            issued = date.fromisoformat(item["date"])
            item["final"] = any(
                0 <= (date.fromisoformat(day) - issued).days <= 90 for day in reviews
            )
    return sorted(found.values(), key=lambda item: item["date"])


def names_party(label: str, parties: list[str]) -> bool:
    """Whether a scheduled event is about one of these parties by name (a
    distinctive word of it in the event's label)."""
    words = set(re.findall(r"[a-z0-9]+", label.lower()))
    for party in parties:
        distinctive = [w for w in re.findall(r"[a-z0-9]+", party.lower()) if len(w) >= 4 and w not in _GENERIC]
        if distinctive and distinctive[0] in words:
            return True
    return False
