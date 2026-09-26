"""A case's history: every dispositive event, from the documents index alone.

The titles of the ALJ's initial determinations and the Commission's notices,
orders and opinions say what happened -- "Initial Determination Terminating
the Investigation as to Respondent Genius Based on Settlement", "Commission
Determination Not to Review an Initial Determination Finding Three
Respondents in Default" -- so no PDF is read and nothing is paid for.

    institution          the notice of institution
    termination          terminations: withdrawal, settlement, consent order,
                         arbitration, or as to claims or patents
    default              findings of default
    summary_determination  IDs on summary determination
    final_id             the final initial determination
    review               the Commission deciding to review an ID
    not_reviewed         the Commission letting a dispositive ID stand (which
                         makes it final)
    determination        the Commission's own determinations: final
                         determinations, terminations, violation findings
    order                Commission orders: remand, reversal, exclusion and
                         cease and desist orders, consent orders, rescission
    opinion              Commission opinions

Left out as housekeeping: target dates and extensions, schedules,
declassification, staff changes, early-adjudication requests, and the
Federal Register's reprints ("F.R. Notice ...").

The same document filed more than once -- a confidential version, then the
public one weeks later, or a corrected one -- is one event, dated by its
earliest version (when it was issued) and linked to its latest public one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..nextactions import stays

LABELS = {
    "institution": "Instituted",
    "termination": "Termination",
    "default": "Default",
    "summary_determination": "Summary determination",
    "final_id": "Final ID",
    "remand_id": "Remand ID",
    "review": "Commission review",
    "not_reviewed": "Commission let stand",
    "determination": "Commission determination",
    "order": "Commission order",
    "opinion": "Commission opinion",
}
# The events that change who or what is in the case, or end it: a summary
# must account for every one of these.
MUST_MENTION = ("termination", "default", "final_id", "remand_id", "not_reviewed", "determination", "order")

_FR_REPRINT = re.compile(r"^\s*f\.\s*r\.\s", re.I)
_HOUSEKEEPING = re.compile(
    r"target\s+date|\bextend|deadline|\bschedul|declassif|redact|investigative\s+attorney|early\s+(?:adjudication|disposition)"
    r"|protective\s+order|errata|ground\s+rules|\bstay\b|\bstaying\b|public\s+interest\s+(?:comments|submissions)",
    re.I,
)
_INSTITUTION = re.compile(r"notice\s+of\s+institution|institution\s+of\s+(?:the\s+)?investigation", re.I)
_TERMINATION = re.compile(r"\bterminat|\bwithdraw|\bsettle|consent\s+order|\barbitration", re.I)
_DEFAULT = re.compile(r"\bdefault", re.I)
_SD = re.compile(r"summary\s+determination", re.I)
_NOT_TO_REVIEW = re.compile(r"\bnot\s+to\s+review\b", re.I)
_TO_REVIEW = re.compile(r"\bto\s+review\b", re.I)
_DISPOSITIVE = re.compile(
    r"\bterminat|\bwithdraw|\bsettle|consent\s+order|\bdefault|summary\s+determination|final\s+initial|violation"
    r"|\bremand|\barbitration|\brescind|\brescission",
    re.I,
)
_DETERMINATION = re.compile(
    r"final\s+determination|determination\s+to\s+terminate|terminat\w*\s+the\s+investigation|finding\s+(?:a\s+|no\s+)?violation"
    r"|\bremand|\breverse|\baffirm|\brescind|\brescission|issuance\s+of\s+(?:an?\s+)?(?:limited|general)",
    re.I,
)
_ORDER = re.compile(
    r"exclusion\s+order|cease\s+and\s+desist|consent\s+order|\bremand|\brevers|\brescission|\brescind|\bmodif",
    re.I,
)
_NOT_FINAL_ID = re.compile(r"^\s*recommended\s+determination|\badvisory\b", re.I)
_ID_ISSUED = re.compile(r"initial\s+determination\s+on\s+violation", re.I)
_ERRATA = re.compile(r"\berrat", re.I)
_REMAND = re.compile(r"\bremand", re.I)
_VARIANT = re.compile(
    r"\[?\bcorrected\b\]?|\bpublic\s+version\b|\bconfidential\b|\(public\)|\[dkt[^\]]*\]|\[motion\s+docket[^\]]*\]"
    r"|order\s+no\.?\s*\d+\s*:?|[^a-z0-9 ]",
    re.I,
)


@dataclass
class Event:
    kind: str
    date: str  # when it was issued: its earliest version
    doc_id: str  # the version to link: the latest public one
    title: str
    who: str = ""  # the respondents it names, where a termination or default does
    public: bool = True  # False when only a confidential version is on file
    versions: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return LABELS.get(self.kind, self.kind)


def _day(doc: dict[str, Any]) -> str:
    return str(doc.get("document_date") or doc.get("official_received_date") or "")[:10]


def _title(doc: dict[str, Any]) -> str:
    return " ".join(str(doc.get("title") or "").replace("�", "'").split())


def classify(doc: dict[str, Any]) -> str | None:
    """Which kind of event a document records, from its type and title; None
    if it is not a dispositive event."""
    title = _title(doc)
    kind = doc.get("document_type")
    if not title or _FR_REPRINT.search(title):
        return None
    if kind == "Notice" and _INSTITUTION.search(title):
        return "institution"
    # The ALJ's notice that the final ID has issued: often weeks before its
    # public version, so it dates the final ID.
    if kind == "Notice" and _ID_ISSUED.search(title) and "commission" not in title.lower():
        return None if _ERRATA.search(title) else "final_id"
    # An ID is only ever an event by its dispositive words, so one that also
    # stays or extends something ("... Terminating ... and Staying the
    # Procedural Schedule") is still that event; only the Commission's
    # notices and orders are screened for housekeeping.
    if kind in ("Notice", "Order, Commission") and _HOUSEKEEPING.search(title):
        return None
    if kind == "ID/RD - Other Than Final on Violation":
        if _SD.search(title):
            return "summary_determination"
        if _REMAND.search(title) and not _ERRATA.search(title):
            return "remand_id"
        if _DEFAULT.search(title):
            return "default"
        if _TERMINATION.search(title):
            return "termination"
        return None
    if kind == "ID/RD - Final on Violation":
        return None if _NOT_FINAL_ID.search(title) or _ERRATA.search(title) else "final_id"
    if kind == "Notice":
        if _NOT_TO_REVIEW.search(title):
            return "not_reviewed" if _DISPOSITIVE.search(title) else None
        if _TO_REVIEW.search(title) and _DISPOSITIVE.search(title):
            return "review"
        # "... Determination of No Violation of Section 337; Termination of
        # Investigation", "... to Grant Complainant's Motion to Withdraw the Complaint"
        if _DETERMINATION.search(title) or _DISPOSITIVE.search(title):
            return "determination"
        return None
    if kind == "Order, Commission":
        return "order" if _ORDER.search(title) else None
    if kind == "Opinion, Commission":
        return "opinion"
    return None


def _bare(title: str) -> str:
    return " ".join(_VARIANT.sub(" ", title.lower()).split())


def _close(a: str, b: str, days: int = 60) -> bool:
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= days
    except ValueError:
        return False


_VAGUE_WHO = re.compile(r"^(?:a|an|the)?\s*certain\b|^(?:all|the|remaining)\b", re.I)


def _same_title(a: str, b: str) -> bool:
    """The same document's versions: titles alike bar "[Corrected]" and the
    like, and small spelling slips between them ("Brands" / "Brand")."""
    a, b = _bare(a), _bare(b)
    if a == b:
        return True
    from rapidfuzz import fuzz

    return fuzz.ratio(a, b) >= 96


def events(documents: list[dict[str, Any]]) -> list[Event]:
    """Every dispositive event, oldest first."""
    gone = {
        str((o.get("source") or {}).get("id")): o.get("who") or ""
        for o in stays.out_of_case(documents or [])
        if not _VAGUE_WHO.match(o.get("who") or "")
    }
    found: list[Event] = []
    for doc in sorted(documents or [], key=lambda d: (_day(d), str(d.get("id")))):
        kind = classify(doc)
        if not kind or not _day(doc):
            continue
        public = str(doc.get("security_level") or "").lower() == "public"
        is_decision = doc.get("document_type") != "Notice"  # a final ID itself, not the notice of it
        if kind == "final_id":
            # The notice of issue, the confidential ID and its public version, weeks apart: one event.
            same = next((e for e in found if e.kind == kind and _close(e.date, _day(doc), 120)), None)
        else:
            same = next((e for e in found if e.kind == kind and _close(e.date, _day(doc))
                         and _same_title(e.title, _title(doc))), None)
        if same:
            # A later version of the same document: link the public one (for a
            # final ID, the decision over the notice of it), keep the first date.
            same.versions.append(str(doc.get("id")))
            linked_is_notice = any(str(d.get("id")) == same.doc_id and d.get("document_type") == "Notice"
                                   for d in documents)
            if public and (kind != "final_id" or is_decision or linked_is_notice):
                same.doc_id, same.title, same.public = str(doc.get("id")), _title(doc), True
            same.who = same.who or gone.get(str(doc.get("id")), "")
            continue
        found.append(Event(kind=kind, date=_day(doc), doc_id=str(doc.get("id")), title=_title(doc),
                           who=gone.get(str(doc.get("id")), ""), public=public, versions=[str(doc.get("id"))]))
    return found
