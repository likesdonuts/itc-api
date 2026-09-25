"""Events -> the claims matrix: one row per claim, one column per stage.

Only an event that passed its checks and was ruled by the tribunal changes a
claim's status (in a complaint, only `asserted` does). An event marked
`needs_review` still puts its claim on the page, flagged, but changes
nothing. Once a claim leaves the case -- withdrawn, settled, or found invalid
or not infringed on summary determination -- its later columns stay empty,
which is what the page's connector line shows.

In the all-respondents view, an event limited to some respondents (a
settlement with one of them) changes a claim only if it covers every
respondent on record; the per-respondent view applies it to that one.
"""

from __future__ import annotations

import re
from typing import Any

# (key, column heading, what the column's count means)
STAGES = (
    ("asserted", "Complaint", "asserted"),
    ("instituted", "Institution", "instituted"),
    ("hearing", "To hearing", "went to hearing"),
    ("final_id", "Final ID", "found infringed"),
    ("commission", "Commission", "violation found"),
    ("appeal", "Federal Circuit", "on appeal"),
)
STAGE_KEYS = tuple(key for key, _, _ in STAGES)

# The status a column counts as "remaining" (its header count).
COUNTED = {
    "asserted": {"asserted"},
    "instituted": {"instituted"},
    "hearing": {"in_case"},
    "final_id": {"infringed"},
    "commission": {"violation"},
    "appeal": {"on_appeal"},
}
# Statuses that take a claim out of the case at the hearing stage.
EXITS = {"withdrawn": "withdrawn", "terminated_settlement": "settled",
         "found_invalid": "invalid", "found_not_infringed": "not_infringed"}
# Actions that can move a claim's chip; the rest (found_not_invalid,
# technical_prong) are kept as provenance only.
MOVES = {"asserted", "instituted", "added", "withdrawn", "terminated_settlement", "found_infringed",
         "found_not_infringed", "found_invalid", "not_reviewed", "violation", "no_violation", "default"}
# Outcome precedence at the Final ID and Commission: invalidity beats
# non-infringement beats infringement for whether there is a violation.
_RANK = {"invalid": 3, "not_infringed": 2, "no_violation": 2, "infringed": 1, "violation": 1}


def changes_status(event: dict[str, Any]) -> bool:
    """Only rulings change a claim -- and in a complaint, only `asserted`
    (which a complaint states as the complainant's own allegation)."""
    if event.get("status") == "needs_review":
        return False
    if event.get("action") == "asserted":
        return event.get("stage") == "asserted"
    return event.get("speaker") == "tribunal_ruling"


def _applies_to(event: dict[str, Any], respondent: str | None, everyone: set[str]) -> bool:
    who = set(event.get("respondents") or ["ALL"])
    if "ALL" in who:
        return True
    if respondent is None:
        return bool(everyone) and everyone <= who
    return respondent in who


def _outcome(cells: dict[str, Any], stage: str, status: str, event_id: str) -> None:
    current = cells.get(stage)
    if current is None or _RANK.get(status, 0) >= _RANK.get(current["status"], 0):
        cells[stage] = {"status": status, "event": event_id}


def _apply(row: dict[str, Any], event: dict[str, Any]) -> None:
    """One status-changing event, on one claim's row."""
    action, stage, cells = event.get("action"), event.get("stage"), row["cells"]
    if action == "asserted":
        cells["asserted"] = {"status": "asserted", "event": event["id"]}
    elif action == "instituted":
        # Never puts a claim back: a later document restating the notice of
        # institution is not a new institution.
        cells.setdefault("instituted", {"status": "instituted", "event": event["id"]})
    elif action == "added":
        cells["instituted"] = {"status": "instituted", "event": event["id"]}
        row["left"] = None  # an added claim is back in the case
    elif row["left"]:
        return  # already out of the case; later rulings don't bring it back
    elif stage == "hearing" and action in EXITS:
        cells["hearing"] = {"status": EXITS[action], "event": event["id"]}
        row["left"] = "hearing"
    elif stage in ("hearing", "final_id", "commission") and action in ("withdrawn", "terminated_settlement"):
        cells[stage] = {"status": EXITS[action], "event": event["id"]}
        row["left"] = stage
    elif stage == "final_id":
        status = {"found_infringed": "infringed", "found_not_infringed": "not_infringed",
                  "found_invalid": "invalid"}.get(action)
        if status:
            _outcome(cells, "final_id", status, event["id"])
    elif action == "not_reviewed" and "final_id" not in cells and re.search(r"(?i)terminat", event.get("sentence") or ""):
        # The Commission declining to review an ID that terminated these
        # claims before any Final ID: they leave the case, as of this notice.
        cells["hearing"] = {"status": "withdrawn", "event": event["id"]}
        row["left"] = "hearing"
    elif stage == "commission":
        if action == "not_reviewed" and "final_id" in cells:
            adopted = cells["final_id"]["status"]
            _outcome(cells, "commission", "violation" if adopted == "infringed" else adopted, event["id"])
        else:
            status = {"found_infringed": "violation", "found_not_infringed": "not_infringed",
                      "found_invalid": "invalid"}.get(action)
            if status:
                _outcome(cells, "commission", status, event["id"])


def _apply_case_wide(rows: list[dict[str, Any]], event: dict[str, Any]) -> None:
    """An event about every claim at once (derive.py): the Commission's
    case-wide finding, or a respondent's exit that names no claims."""
    action = event.get("action")
    for row in rows:
        cells = row["cells"]
        if row["left"]:
            continue
        if action in ("withdrawn", "terminated_settlement"):
            stage = event.get("stage") or "hearing"
            cells[stage] = {"status": EXITS[action], "event": event["id"]}
            row["left"] = stage
        elif action == "default":
            # No hearing on a defaulting respondent's claims, but they stay in
            # the case: relief can still follow.
            cells.setdefault("hearing", {"status": "default", "event": event["id"]})
        elif action == "violation" and (cells.get("hearing") or {}).get("status") == "default":
            cells.setdefault("commission", {"status": "violation", "event": event["id"]})
        elif "final_id" not in cells or "commission" in cells:
            continue  # the Commission's finding reaches claims that had a Final ID
        elif action == "no_violation":
            cells["commission"] = {"status": "no_violation", "event": event["id"]}
        elif action == "violation" and cells["final_id"]["status"] == "infringed":
            cells["commission"] = {"status": "violation", "event": event["id"]}


def build(analysis: dict[str, Any], *, respondent: str | None = None) -> dict[str, Any]:
    """The matrix for all respondents, or as it stands for one of them.

    Events are replayed in date order, case-wide ones included, so a
    respondent's settlement takes its claims out as of that date and a later
    finding for everyone does not show up in that respondent's view.
    """
    everyone = set(analysis.get("respondents") or [])
    patents: dict[str, dict[int, dict[str, Any]]] = {p: {} for p in analysis.get("patents") or []}

    def order(e: dict[str, Any]) -> tuple[str, int, int]:
        stage = STAGE_KEYS.index(e.get("stage")) if e.get("stage") in STAGE_KEYS else 9
        return (str(e.get("date") or ""), stage, 1 if e.get("case_wide") else 0)

    def varies(rows: list[dict[str, Any]], event: dict[str, Any]) -> None:
        """In the all-respondents view, an event for only some respondents
        marks its column rather than leaving it blank -- and from then on the
        claim's respondents have parted ways, so its later columns vary too."""
        stage = event.get("stage")
        if respondent is None and stage in STAGE_KEYS and changes_status(event) and event.get("action") != "asserted":
            parts_ways = event.get("action") in ("withdrawn", "terminated_settlement", "default")
            for row in rows:
                if not row["left"]:
                    row["cells"].setdefault(stage, {"status": "by_respondent", "event": event["id"]})
                    # Only an exit parts the respondents' ways. A remedy aimed
                    # at one respondent (its own cease and desist order) does
                    # not, and a finding for everyone still replaces this mark.
                    if parts_ways:
                        row["split"] = True

    def apply(rows: list[dict[str, Any]], event: dict[str, Any]) -> None:
        split = [r for r in rows if r.get("split") and event.get("stage") in ("hearing", "final_id", "commission")]
        whole = [r for r in rows if r not in split]
        if event.get("case_wide"):
            _apply_case_wide(whole, event)
        else:
            for row in whole:
                _apply(row, event)
        # A claim whose respondents have split shows the later stage as varying:
        # this outcome holds only for the respondents still in the case.
        varies(split, event)

    for event in sorted(analysis.get("events") or [], key=order):
        if event.get("case_wide"):
            every_row = [row for rows in patents.values() for row in rows.values()]
            if changes_status(event) and _applies_to(event, respondent, everyone):
                apply(every_row, event)
            else:
                varies(every_row, event)
            continue
        rows = patents.setdefault(event.get("patent") or "unknown", {})
        for claim in event.get("claims") or []:
            row = rows.setdefault(claim, {"claim": claim, "cells": {}, "events": [], "needs_review": False, "left": None})
            row["events"].append(event["id"])
            if (
                event.get("status") == "needs_review"
                and event.get("action") in MOVES
                and changes_status({**event, "status": "ok"})
            ):
                # Flagged, and it would change this claim if confirmed. A
                # flagged recital, argument, or finding that moves no chip
                # (not invalid, technical prong) changes nothing either way.
                row["needs_review"] = True
            if changes_status(event) and _applies_to(event, respondent, everyone):
                apply([row], event)
            else:
                varies([row], event)

    groups = []
    totals = {key: 0 for key in STAGE_KEYS}
    for patent, rows in patents.items():
        ordered = [rows[claim] for claim in sorted(rows)]
        for row in ordered:
            # A claim with a Final ID outcome and no earlier exit went to hearing.
            if "final_id" in row["cells"] and "hearing" not in row["cells"]:
                row["cells"]["hearing"] = {"status": "in_case", "event": row["cells"]["final_id"]["event"]}
        counts = {
            key: sum(1 for r in ordered if (r["cells"].get(key) or {}).get("status") in COUNTED[key])
            for key in STAGE_KEYS
        }
        for key in STAGE_KEYS:
            totals[key] += counts[key]
        groups.append({"patent": patent, "rows": ordered, "counts": counts})
    return {"stages": STAGES, "patents": groups, "counts": totals}
