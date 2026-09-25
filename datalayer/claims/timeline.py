"""Effective dates: when each event actually took hold.

The spec's rule: an ALJ order terminating claims is an initial determination,
and it takes effect when the Commission declines to review it -- so its
events are dated by the Commission's non-review notice, not the ID's issue
date. The documents make the link themselves:

    ALJ:        "ORDER NO. 12: INITIAL DETERMINATION TERMINATING THE
                 INVESTIGATION IN PART ..."                  (12 Feb 2024)
    Commission: "... has determined not to review an initial determination
                 ("ID") (Order No. 12) ..."                  (12 Mar 2024)

And a Final ID is dated by its issue, not by its public version, which is
often filed weeks later: the documents index lists the confidential version
(and the ALJ's notice of it) under the real date, even when only the public
version can be read.

Each event keeps its document's own date in `date`; this adds
`effective_date`, and a note saying where it came from.
"""

from __future__ import annotations

import re
from typing import Any

from .validate import is_final_id

_OWN_ORDER = re.compile(r"(?i)\border\s*no\.?\s*(\d{1,3})\s*[:.]")
_NOT_REVIEWED = re.compile(r"(?i)determined\s+not\s+to\s+review\b(?P<after>.{0,300})", re.S)
_TO_REVIEW = re.compile(r"(?i)determined\s+to\s+review\b(?P<after>.{0,300})", re.S)
_CITED_ORDER = re.compile(r"(?i)\bOrder\s+No\.?\s*(\d{1,3})\b")


def own_order_number(text: str) -> int | None:
    """An ALJ document's own order number, from its first lines."""
    match = _OWN_ORDER.search(" ".join((text or "")[:800].split()))
    return int(match.group(1)) if match else None


def commission_decisions(sources: list[dict[str, Any]], texts: dict[str, str]) -> dict[int, dict[str, Any]]:
    """For each ALJ order number, the Commission's decision on it: not to
    review (the ID becomes final that day) or to review. The first order
    number cited after "determined not to review" is the one decided.
    """
    decisions: dict[int, dict[str, Any]] = {}
    for source in sorted(sources, key=lambda s: str(s.get("date") or "")):
        if source.get("kind") != "commission_notice":
            continue
        flat = " ".join((texts.get(source["id"]) or "").split())
        for pattern, decision in ((_NOT_REVIEWED, "not_reviewed"), (_TO_REVIEW, "reviewed")):
            match = pattern.search(flat)
            if not match:
                continue
            cited = _CITED_ORDER.search(match.group("after"))
            if cited:
                decisions.setdefault(
                    int(cited.group(1)),
                    {"decision": decision, "date": str(source.get("date") or "")[:10], "notice": source["id"]},
                )
            break
    return decisions


def final_id_issued(documents: list[dict[str, Any]]) -> str | None:
    """The day the Final ID issued: the earliest of its versions and the
    ALJ's notice of it, confidential ones included (only their dates are used)."""
    days = sorted(
        str(d.get("document_date") or d.get("official_received_date") or "")[:10]
        for d in documents
        if is_final_id(d.get("document_type"), d.get("title"))
        and (d.get("document_date") or d.get("official_received_date"))
    )
    return days[0] if days else None


def apply(
    events: list[dict[str, Any]],
    *,
    sources: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    texts: dict[str, str],
) -> dict[str, Any]:
    """Set every event's `effective_date` (and `effective_note`). Returns
    what was found, for the build's log."""
    decisions = commission_decisions(sources, texts)
    orders = {
        s["id"]: own_order_number(texts.get(s["id"]) or "")
        for s in sources
        if s.get("kind") in ("alj_order", "initial_determination")
    }
    issued = final_id_issued(documents)

    for event in events:
        event.pop("effective_note", None)
        date = str(event.get("date") or "")[:10]
        source = event.get("source") or {}
        effective = date
        if event.get("stage") == "final_id" and issued:
            effective = issued
            if issued != date:
                event["effective_note"] = f"the Final ID issued {issued}; this public version was filed {date}"
        elif source.get("source_kind") in ("alj_order", "initial_determination") and event.get("method") != "rule":
            number = orders.get(source.get("id"))
            decided = decisions.get(number) if number else None
            if decided and decided["decision"] == "not_reviewed":
                effective = decided["date"]
                event["effective_note"] = (
                    f"Order No. {number} took effect when the Commission declined review "
                    f"(notice {decided['notice']}, {decided['date']})"
                )
            elif decided:
                event["effective_note"] = f"the Commission reviewed Order No. {number} (notice {decided['notice']})"
            elif number and event.get("action") in ("withdrawn", "terminated_settlement", "found_invalid",
                                                    "found_not_infringed", "default"):
                event["effective_note"] = f"no Commission decision on Order No. {number} found yet"
        event["effective_date"] = effective
    return {"decisions": decisions, "final_id_issued": issued}


def effective(event: dict[str, Any]) -> str:
    return str(event.get("effective_date") or event.get("date") or "")
