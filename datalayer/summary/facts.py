"""Facts a summary can cite without reading anything, so without paying:

    title facts    what an order's or notice's title already says: a
                   respondent settled out, was found in default, the
                   Commission let a ruling stand, an exclusion order issued
                   (select.py's "noted" documents)
    claims facts   the claims analysis's findings, where the case has one:
                   which claims were withdrawn, found invalid or not, found
                   infringed or not, and the overall violation finding --
                   only events that passed its checks, grouped by patent

Each is numbered for the writer ("t3", "c2") like the notes, and carries its
source document so the page can link it.
"""

from __future__ import annotations

from typing import Any

from .select import Selection

TITLE_KINDS = {"termination": "Termination", "default": "Default", "not_reviewed": "Not reviewed", "remedy": "Remedial order"}

# The claims analysis's findings worth a sentence, in the order a case reaches them.
CLAIM_ACTIONS = {
    "withdrawn": "withdrawn by the complainant",
    "terminated_settlement": "terminated on settlement",
    "default": "respondent found in default",
    "found_invalid": "found invalid",
    "found_not_invalid": "found not invalid",
    "found_infringed": "found infringed",
    "found_not_infringed": "found not infringed",
    "violation": "violation of Section 337 found",
    "no_violation": "no violation of Section 337 found",
}
SPEAKERS = {"Complaint": "the complaint", "ID/RD - Final on Violation": "the final ID",
            "ID/RD - Other Than Final on Violation": "an initial determination", "Notice": "a Commission notice",
            "Opinion, Commission": "the Commission opinion", "Order, Commission": "a Commission order",
            "Order": "an ALJ order"}


def title_facts(selection: Selection, limit: int = 80) -> list[dict[str, Any]]:
    """The noted documents, oldest first (at most `limit`, the latest kept)."""
    items = sorted(selection.noted, key=lambda i: (i.day, i.id))[-limit:]
    return [
        {"doc_id": item.id, "kind": item.kind, "date": item.day, "title": item.title,
         "label": TITLE_KINDS.get(item.kind, item.kind)}
        for item in items
    ]


def _claims_text(claims: list[int]) -> str:
    """[1, 2, 3, 5] -> "claims 1-3, 5"."""
    claims = sorted(set(claims))
    if not claims:
        return ""
    runs: list[tuple[int, int]] = []
    for claim in claims:
        if runs and claim == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], claim)
        else:
            runs.append((claim, claim))
    text = ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)
    return ("claim " if len(claims) == 1 else "claims ") + text


def claims_facts(analysis: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The claims analysis's checked findings, one per action, patent, who
    and source document, in date order."""
    grouped: dict[tuple[str, str, tuple[str, ...], str], dict[str, Any]] = {}
    for event in (analysis or {}).get("events") or []:
        action = event.get("action")
        if action not in CLAIM_ACTIONS or event.get("status") != "ok":
            continue
        source = event.get("source") or {}
        who = tuple(sorted(event.get("respondents") or ["ALL"]))
        patent = str(event.get("patent") or "")
        key = (action, patent, who, str(source.get("id") or ""))
        fact = grouped.setdefault(key, {
            "action": action, "patent": patent, "who": list(who), "claims": [],
            "date": event.get("effective_date") or event.get("date") or "",
            "doc_id": str(source.get("id") or ""), "files": source.get("files") or [],
            "source_type": source.get("document_type") or "", "quote": event.get("quote") or "",
        })
        fact["claims"].extend(event.get("claims") or [])
    facts = []
    for fact in sorted(grouped.values(), key=lambda f: (str(f["date"]), f["patent"])):
        patent = f" of the '{fact['patent'][-3:]} patent" if fact["patent"] and fact["patent"] != "unknown" else ""
        claims = _claims_text(fact["claims"])
        who = "" if fact["who"] == ["ALL"] else f" (as to {', '.join(fact['who'])})"
        where = SPEAKERS.get(fact["source_type"], "a decision")
        what = f"{claims}{patent}: {CLAIM_ACTIONS[fact['action']]}" if claims else CLAIM_ACTIONS[fact["action"]]
        facts.append({**fact, "text": f"{what}{who}, per {where}"})
    return facts
