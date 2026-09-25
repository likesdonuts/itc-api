"""Case-wide Commission outcomes, by rule.

The Commission's own final determination often names no claims at all:

    Having reviewed the record of the investigation, the Commission has
    found no violation of section 337. ... The investigation is terminated.
                                                  (337-TA-1384, 25 Apr 2025)

A sentence like that never reaches the model -- candidate sentences must name
claims -- and it needs no model: it applies to every claim still in the case
after the Final ID. So it is read by rule and recorded as one case-wide
event, which the matrix applies to those claims ("Derived from event
history"). A Commission ruling that does name claims still comes from the
model and takes precedence.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .text import find_claim_refs, split_sentences

_VERB = r"(?:has\s+)?(?:found|finds|determined\s+to\s+find|determines\s+to\s+find|has\s+determined\s+to\s+find)"
_NO_VIOLATION = re.compile(rf"\bCommission\b[^.;]{{0,120}}?\b{_VERB}\s+no\s+violation\s+of\s+section\s+337\b", re.I)
_VIOLATION = re.compile(
    rf"\bCommission\b[^.;]{{0,120}}?\b{_VERB}\s+(?:that\s+there\s+is\s+)?a\s+violation\s+of\s+section\s+337\b", re.I
)
FINAL_KINDS = ("final_determination", "commission_notice", "commission_opinion")


def _is_final_determination(kind: str, document: dict[str, Any]) -> bool:
    if kind not in FINAL_KINDS:
        return False
    if kind in ("final_determination", "commission_opinion"):
        return True
    return "final determination" in str(document.get("title") or "").lower()


def _event(document: dict[str, Any], kind: str, action: str, stage: str, respondents: list[str],
           quote: str, sentence: str, note: str) -> dict[str, Any]:
    doc_id = str(document.get("id"))
    who = "|".join(sorted(respondents))
    return {
        "id": hashlib.sha1(f"{doc_id}|case-wide|{action}|{who}".encode("utf-8")).hexdigest()[:12],
        "stage": stage,
        "action": action,
        "speaker": "tribunal_ruling",
        "patent": "ALL",
        "claims_verbatim": "",
        "claims": [],
        "respondents": respondents,
        "case_wide": True,
        "date": str(document.get("document_date") or document.get("official_received_date") or "")[:10],
        "quote": quote,
        "sentence": sentence,
        "method": "derived",
        "status": "ok",
        "notes": [note],
        "source": {
            "kind": "edis",
            "source_kind": kind,
            "id": doc_id,
            "title": document.get("title"),
            "document_type": document.get("document_type"),
        },
    }


# "The investigation is terminated as to Clarion Medical Technologies, Inc.,
# Luvo Medical Technologies, Inc., and Healthcare Markets, Inc. ..."
_TERMINATED_AS_TO = re.compile(
    r"\binvestigation\s+is\s+(?:hereby\s+)?terminated\s+(?:as\s+to|with\s+respect\s+to)\s+(?:respondents?\s+)?(?P<who>.+)",
    re.I,
)
# "Medical Purchasing Resource, LLC is hereby found in default." / "... that
# Bio-Infusions USA Inc., ... and MIRAmedtech SP. Z.O.O. are in default"
_IN_DEFAULT = re.compile(r"(?P<who>.+?)\b(?:is|are)\s+(?:hereby\s+)?(?:found\s+)?(?:to\s+be\s+)?in\s+default\b", re.I)
_DEFAULT_RELIEF = re.compile(r"presumed\s+to\s+be\s+true\s+as\s+to\s+the\s+Defaulting\s+Respondents", re.I)
_RULING_DOCS = ("alj_order", "initial_determination", "commission_notice", "final_determination")


def _named(segment: str, respondents: list[str]) -> list[str]:
    from ..counsel import match_parties

    people = [{"name": r, "role": "Respondent"} for r in respondents]
    return [p["name"] for p in match_parties(segment, people)]


def respondent_events(
    text: str, document: dict[str, Any], *, kind: str, respondents: list[str]
) -> list[dict[str, Any]]:
    """Terminations and defaults a document states for named respondents,
    naming no claims -- so they apply to all of those respondents' claims.

    Only rulings count: a recital ("On April 24, 2025, the Commission issued
    a notice terminating ...") restates an earlier event and is skipped, so
    the event is dated by the document that makes it.
    """
    if not text or kind not in _RULING_DOCS or not respondents:
        return []
    found: list[dict[str, Any]] = []
    for sentence in split_sentences(text):
        if find_claim_refs(sentence) or re.match(r"(?i)\s*on\s+\w+\s+\d", sentence):
            continue  # names claims (the model reads it), or recites history
        match = _TERMINATED_AS_TO.search(sentence)
        if match:
            who = _named(match.group("who"), respondents)
            if who:
                reason = " ".join([sentence, str(document.get("title") or "")]).lower()
                action = "terminated_settlement" if re.search(r"settle|consent|licens", reason) else "withdrawn"
                found.append(_event(document, kind, action, "hearing", who, match.group(0)[:300], sentence,
                                    "a termination as to these respondents: all their claims leave the case"))
            continue
        match = _IN_DEFAULT.search(sentence)
        if match and not re.search(r"(?i)\bwhy\b|\bshould\s+not\b|\bshow\s+cause\b", sentence):
            who = _named(match.group("who"), respondents)
            if who:
                found.append(_event(document, kind, "default", "hearing", who, match.group(0)[-300:], sentence,
                                    "found in default: no hearing on these respondents' claims"))
    return found


def default_relief(text: str, document: dict[str, Any], *, kind: str, defaulted: list[str]) -> list[dict[str, Any]]:
    """Relief against defaulting respondents under 19 U.S.C. 1337(g)(1),
    where the complaint's allegations are presumed true: the Commission
    column shows a violation for those respondents' claims."""
    if not text or not defaulted or kind not in FINAL_KINDS:
        return []
    for sentence in split_sentences(text):
        match = _DEFAULT_RELIEF.search(sentence)
        if match:
            return [_event(document, kind, "violation", "commission", sorted(defaulted), match.group(0), sentence,
                           "relief against defaulting respondents under 19 U.S.C. 1337(g)(1); the complaint's "
                           "allegations are presumed true")]
    return []


def commission_outcomes(text: str, document: dict[str, Any], *, kind: str) -> list[dict[str, Any]]:
    """The case-wide outcome a Commission final determination states, if any."""
    if not text or not _is_final_determination(kind, document):
        return []
    found: dict[str, dict[str, Any]] = {}
    for sentence in split_sentences(text):
        if find_claim_refs(sentence):
            continue  # names claims: the model reads it
        for action, pattern in (("no_violation", _NO_VIOLATION), ("violation", _VIOLATION)):
            match = pattern.search(sentence)
            if match and action not in found:
                doc_id = str(document.get("id"))
                found[action] = {
                    "id": hashlib.sha1(f"{doc_id}|case-wide|{action}".encode("utf-8")).hexdigest()[:12],
                    "stage": "commission",
                    "action": action,
                    "speaker": "tribunal_ruling",
                    "patent": "ALL",
                    "claims_verbatim": "",
                    "claims": [],
                    "respondents": ["ALL"],
                    "case_wide": True,
                    "date": str(document.get("document_date") or document.get("official_received_date") or "")[:10],
                    "quote": match.group(0),
                    "sentence": sentence,
                    "method": "derived",
                    "status": "ok",
                    "notes": ["applies to every claim still in the case after the Final ID"],
                    "source": {
                        "kind": "edis",
                        "source_kind": kind,
                        "id": doc_id,
                        "title": document.get("title"),
                        "document_type": document.get("document_type"),
                    },
                }
    return list(found.values())
