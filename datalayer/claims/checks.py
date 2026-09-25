"""Checks over a whole record's events, run on every build.

Output checks (validate.py) look at one event at a time. These look at them
together:

Replay validator. The status-changing events are replayed, per claim, in
effective-date order, and contradictions are flagged:

- a claim found infringed (or not, or invalid) after it was terminated;
- a finding for a claim that never went to hearing (never instituted);
- a claim reappearing -- a status change after it left -- without an
  `added` event.

Cross-document corroboration. Commission notices restate the Final ID's
outcome ("the FID found no violation with respect to claims 1 and 12-14 of
the '511 patent"). Where a restatement agrees with the Final ID's own
finding, both are marked corroborated; where it disagrees, both go to
review.

A flag puts the event in `needs_review` with a note in `case_notes`, so it
stops changing the claim, exactly like a failed output check. Both are
recomputed from scratch on every build.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from . import matrix
from .timeline import effective

FINDINGS = {"found_infringed", "found_not_infringed", "found_invalid"}
EXITS = {"withdrawn", "terminated_settlement"}
# Whether an outcome means a violation (+) or not (-), for comparing a Final
# ID's finding with a Commission notice's restatement of it.
_SIGN = {"found_infringed": "+", "found_not_infringed": "-", "found_invalid": "-"}


def _flag(event: dict[str, Any], note: str) -> None:
    event.setdefault("case_notes", [])
    if note not in event["case_notes"]:
        event["case_notes"].append(note)
    event["status"] = "needs_review"


def _label(event: dict[str, Any]) -> str:
    return f"{(event.get('source') or {}).get('id')}, {effective(event)}"


def _scope(event: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(event.get("respondents") or ["ALL"]))


def replay(events: list[dict[str, Any]]) -> int:
    """Flag contradictions in each claim's history. Returns how many."""
    history: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event.get("case_wide") or not matrix.changes_status(event):
            continue
        for claim in event.get("claims") or []:
            history[(event.get("patent"), claim)].append(event)

    flagged = 0
    for (patent, claim), timeline in history.items():
        timeline.sort(key=lambda e: (effective(e), matrix.STAGE_KEYS.index(e["stage"]) if e.get("stage") in matrix.STAGE_KEYS else 9))
        instituted = False
        left: dict[tuple[str, ...], dict[str, Any]] = {}  # respondent scope -> the exit
        for event in timeline:
            action = event.get("action")
            if action in ("instituted", "added"):
                instituted = True
                if action == "added":
                    left.clear()
                continue
            if action == "asserted":
                continue
            scope = _scope(event)
            # An exit for everyone covers every scope; one for some
            # respondents covers only events limited to those respondents.
            exit_ = left.get(("ALL",)) or left.get(scope)
            if exit_ is not None and action not in EXITS:
                _flag(event, f"claim {claim} was terminated earlier ({_label(exit_)}) and has no 'added' event since")
                flagged += 1
                continue
            if action in FINDINGS and event.get("stage") in ("final_id", "commission") and not instituted:
                _flag(event, f"a finding for claim {claim}, which was never instituted")
                flagged += 1
                continue
            if action in EXITS or (action in FINDINGS and event.get("stage") == "hearing"):
                left.setdefault(scope, event)
    return flagged


def corroborate(events: list[dict[str, Any]]) -> tuple[int, int]:
    """Match the Final ID's findings with the Commission's restatements of
    them. Returns (corroborated, disputed) event counts."""
    rulings = [e for e in events if e.get("stage") == "final_id" and e.get("speaker") == "tribunal_ruling"
               and e.get("action") in FINDINGS and e.get("status") == "ok"]
    recitals = [e for e in events if e.get("stage") == "commission" and e.get("speaker") == "tribunal_recital"
                and e.get("action") in FINDINGS and e.get("status") == "ok"]
    agreed: set[str] = set()
    disputed: set[str] = set()
    for recital in recitals:
        for ruling in rulings:
            if ruling.get("patent") != recital.get("patent"):
                continue
            shared = set(ruling.get("claims") or []) & set(recital.get("claims") or [])
            if not shared:
                continue
            if _SIGN[ruling["action"]] == _SIGN[recital["action"]]:
                for a, b in ((ruling, recital), (recital, ruling)):
                    a.setdefault("corroborated_by", [])
                    if b["id"] not in a["corroborated_by"]:
                        a["corroborated_by"].append(b["id"])
                agreed.update((ruling["id"], recital["id"]))
            else:
                # Invalidity and non-infringement are both "no violation", so
                # only a violation against no violation is a disagreement --
                # and only if the Final ID said nothing else about the claims.
                claims = ", ".join(str(c) for c in sorted(shared))
                other = [r for r in rulings if r is not ruling and r.get("patent") == ruling.get("patent")
                         and shared & set(r.get("claims") or []) and _SIGN[r["action"]] == _SIGN[recital["action"]]]
                if other:
                    continue
                note = f"the Final ID and a Commission notice disagree about claim(s) {claims} ({_label(recital)})"
                _flag(ruling, note)
                _flag(recital, note)
                disputed.update((ruling["id"], recital["id"]))
    return len(agreed - disputed), len(disputed)


def run(events: list[dict[str, Any]]) -> dict[str, int]:
    """Reset last build's case-level results, then replay and corroborate."""
    for event in events:
        event.pop("case_notes", None)
        event.pop("corroborated_by", None)
    flagged = replay(events)
    agreed, disputed = corroborate(events)
    return {"replay_flags": flagged, "corroborated": agreed, "disputed": disputed}
