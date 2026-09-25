"""Output checks: the model's raw events -> stored events.

Every model event must pass all of these, or it is kept with status
`needs_review` (and a note saying why) and changes no claim's status:

- the quote is a substring of its sentence;
- the claim list appears in the sentence and expands (by code -- claim
  numbers never come from the model);
- the patent is on the record's list (the model may say "unknown", which is
  then resolved from the sentence or its context by rule when it can be; a
  patent the record does not have is flagged, never swapped for another);
- the respondents are on the record's list, or ["ALL"].

`mention_only` events are counted but not stored: they change nothing and
would bury the events that do.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .candidates import Candidate
from .text import ClaimListError, expand_claims, find_claim_refs, patent_for, patent_mentions


def _norm(text: Any) -> str:
    text = str(text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.replace("–", "-").replace("—", "-").split()).lower()


def _loose(text: Any) -> str:
    """Letters and digits only. OCR garbles punctuation and spacing
    ("findingno", "\"511patent"), so a quote is compared on its words: still
    exact about what it says, and in what order.
    """
    return re.sub(r"[^a-z0-9]", "", _norm(text))


def contains(sentence: Any, part: Any) -> bool:
    return bool(_loose(part)) and _loose(part) in _loose(sentence)


def is_final_id(document_type: str | None, title: str | None) -> bool:
    """The Final ID itself, or the ALJ's notice announcing it (a "Notice"
    titled "Initial Determination on Violation of Section 337 ...")."""
    # The title has to open with it: "Initial Determination Granting Staff's
    # Motion to Declassify ... Its Brief on Violation" is not the Final ID.
    return document_type == "ID/RD - Final on Violation" or bool(
        re.match(r"^\W*(?:\[[^\]]*\]\s*)?initial\s+determination\s+on\s+violation\b", str(title or ""), re.I)
    )


def stage_for(kind: str, document_type: str | None, action: str, title: str | None = None) -> str:
    """Which column of the matrix an event from this kind of document is about."""
    if kind == "complaint":
        return "asserted"
    if kind == "notice_of_institution" or action in ("instituted", "added"):
        return "instituted"
    if action in ("withdrawn", "terminated_settlement"):
        # Claims leave before the hearing, whoever states it: the ALJ's ID,
        # or the Commission's notice declining to review it (the effective
        # date, per the spec).
        return "hearing"
    if kind in ("commission_notice", "commission_opinion", "final_determination"):
        return "commission"
    if kind == "initial_determination" and is_final_id(document_type, title):
        return "final_id"
    # ALJ orders and initial determinations before the Final ID: withdrawals,
    # settlements and summary determinations, i.e. what reaches the hearing.
    return "hearing"


def event_id(doc_id: str, sentence: str, raw: dict[str, Any]) -> str:
    """Stable across rebuilds -- built from the sentence's text, not its
    number -- so a person's correction keeps finding its event."""
    parts = [
        doc_id,
        hashlib.sha1(sentence.encode("utf-8")).hexdigest()[:12],
        raw.get("action"),
        raw.get("patent_number"),
        _norm(raw.get("claims_verbatim")),
        "|".join(sorted(raw.get("respondents") or [])),
    ]
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]


def to_events(
    raw_events: list[dict[str, Any]],
    candidates: list[Candidate],
    *,
    document: dict[str, Any],
    kind: str,
    patents: list[str],
    respondents: list[str],
    model: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Checked events for one document, and counts of what was set aside."""
    by_id = {c.id: c for c in candidates}
    known_respondents = {r.lower(): r for r in respondents}
    events: list[dict[str, Any]] = []
    stats = {"mention_only": 0, "unknown_sentence": 0, "needs_review": 0}
    seen: set[str] = set()

    for raw in raw_events:
        candidate = by_id.get(str(raw.get("sentence_id") or ""))
        if candidate is None:
            stats["unknown_sentence"] += 1
            continue
        action = str(raw.get("action") or "")
        if action == "mention_only":
            stats["mention_only"] += 1
            continue

        sentence = candidate.sentence
        notes: list[str] = []
        quote = str(raw.get("quote") or "")
        if not contains(sentence, quote):
            notes.append("the quote is not in the sentence")

        verbatim = str(raw.get("claims_verbatim") or "").strip()
        claims: list[int] = []
        try:
            claims = expand_claims(verbatim)
        except ClaimListError as exc:
            notes.append(f"claim list: {exc}")
        if verbatim and not contains(sentence, verbatim):
            notes.append("the claim list is not written that way in the sentence")

        patent = str(raw.get("patent_number") or "unknown")
        method_note = None
        if patent != "unknown" and patent not in patents:
            # A patent the model named that the record does not have is a
            # mistake to look at, not something to quietly correct.
            notes.append(f"patent {patent!r} is not on the record's list")
        elif patent == "unknown":
            resolved = None
            for ref in find_claim_refs(sentence):
                if _loose(ref.verbatim) == _loose(verbatim):
                    resolved = patent_for(ref, sentence, patents)
                    break
            if resolved is None:
                # The context may name the patent the sentence leaves out.
                named = {m.patent for m in patent_mentions(candidate.context, patents) if m.patent}
                resolved = next(iter(named)) if len(named) == 1 else None
            if resolved:
                method_note = "patent resolved by rule from the text"
                patent = resolved
            else:
                notes.append("the patent is not identified in the sentence or its context")

        who = [str(r) for r in raw.get("respondents") or ["ALL"]]
        if who != ["ALL"]:
            mapped = []
            for name in who:
                match = known_respondents.get(name.strip().lower())
                if match is None:
                    notes.append(f"respondent {name!r} is not on the record's list")
                    mapped.append(name)
                else:
                    mapped.append(match)
            who = mapped

        identity = event_id(str(document.get("id")), sentence, {**raw, "patent_number": patent, "respondents": who})
        if identity in seen:
            continue
        seen.add(identity)
        status = "needs_review" if notes else "ok"
        if notes:
            stats["needs_review"] += 1
        events.append(
            {
                "id": identity,
                "stage": stage_for(kind, document.get("document_type"), action, document.get("title")),
                "action": action,
                "speaker": str(raw.get("speaker") or "other"),
                "patent": patent,
                "claims_verbatim": verbatim,
                "claims": claims,
                "respondents": who,
                "date": str(document.get("document_date") or document.get("official_received_date") or "")[:10],
                "quote": quote,
                "sentence": sentence,
                "context": candidate.context,
                "method": "haiku",
                "model": model,
                "status": status,
                "notes": notes + ([method_note] if method_note else []),
                "source": {
                    "kind": "edis",
                    "source_kind": kind,
                    "id": str(document.get("id")),
                    "title": document.get("title"),
                    "document_type": document.get("document_type"),
                },
            }
        )
    return events, stats


def recheck(
    event: dict[str, Any],
    *,
    kind: str,
    document: dict[str, Any],
    patents: list[str],
    respondents: list[str],
) -> dict[str, Any]:
    """Re-run the stage rule and the output checks over a stored model event
    -- the spec's "rerun ... over all events for the record" on an update --
    without asking the model again. Its id, and so any correction keyed to
    it, is unchanged.
    """
    sentence = event.get("sentence") or ""
    notes: list[str] = []
    if not contains(sentence, event.get("quote")):
        notes.append("the quote is not in the sentence")
    verbatim = event.get("claims_verbatim") or ""
    claims: list[int] = []
    try:
        claims = expand_claims(verbatim)
    except ClaimListError as exc:
        notes.append(f"claim list: {exc}")
    if verbatim and not contains(sentence, verbatim):
        notes.append("the claim list is not written that way in the sentence")
    if event.get("patent") not in patents:
        notes.append(f"patent {event.get('patent')!r} is not on the record's list")
    known = {r.lower() for r in respondents}
    for name in event.get("respondents") or ["ALL"]:
        if name != "ALL" and name.lower() not in known:
            notes.append(f"respondent {name!r} is not on the record's list")
    # Notes about how the event was made, as opposed to checks that failed.
    kept = [n for n in event.get("notes") or [] if n.startswith(("patent resolved", "re-read by"))
            or "could not resolve" in n]
    return {
        **event,
        "stage": stage_for(kind, document.get("document_type"), event.get("action") or "", document.get("title")),
        "claims": claims,
        "status": "needs_review" if notes else "ok",
        "notes": notes + kept,
        "source": {**(event.get("source") or {}), "source_kind": kind},
    }
