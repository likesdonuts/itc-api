"""Events -> the claims matrix: one row per claim, one column per stage.

Only an event that passed its checks and was ruled by the tribunal changes a
claim's status (in a complaint, only `asserted` does). An event marked
`needs_review` still puts its claim on the page, flagged, but changes
nothing.
"""

from __future__ import annotations

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

# The stage and cell status each status-changing action sets. Later phases
# add the rest of the action table (withdrawn, found_infringed, ...).
ACTION_CELLS = {
    "asserted": ("asserted", "asserted"),
    "instituted": ("instituted", "instituted"),
    "added": ("instituted", "instituted"),
}
# Statuses that count toward a column's "claims remaining" total.
REMAINING = {"asserted", "instituted", "in_case", "infringed", "violation", "on_appeal"}


def changes_status(event: dict[str, Any]) -> bool:
    if event.get("status") == "needs_review":
        return False
    if event.get("action") == "asserted":
        return True
    return event.get("speaker") == "tribunal_ruling"


def _applies_to(event: dict[str, Any], respondent: str | None) -> bool:
    who = event.get("respondents") or ["ALL"]
    return respondent is None or "ALL" in who or respondent in who


def build(analysis: dict[str, Any], *, respondent: str | None = None) -> dict[str, Any]:
    """The matrix for all respondents, or as it stands for one of them."""
    patents: dict[str, dict[int, dict[str, Any]]] = {p: {} for p in analysis.get("patents") or []}
    for event in sorted(analysis.get("events") or [], key=lambda e: str(e.get("date") or "")):
        patent = event.get("patent") or "unknown"
        rows = patents.setdefault(patent, {})
        for claim in event.get("claims") or []:
            row = rows.setdefault(claim, {"claim": claim, "cells": {}, "events": [], "needs_review": False})
            row["events"].append(event["id"])
            if event.get("status") == "needs_review":
                row["needs_review"] = True
            if not _applies_to(event, respondent) or not changes_status(event):
                continue
            cell = ACTION_CELLS.get(event.get("action"))
            if cell:
                stage, status = cell
                row["cells"][stage] = {"status": status, "event": event["id"]}

    groups = []
    totals = {key: 0 for key in STAGE_KEYS}
    for patent, rows in patents.items():
        ordered = [rows[claim] for claim in sorted(rows)]
        counts = {
            key: sum(1 for r in ordered if (r["cells"].get(key) or {}).get("status") in REMAINING)
            for key in STAGE_KEYS
        }
        for key in STAGE_KEYS:
            totals[key] += counts[key]
        groups.append({"patent": patent, "rows": ordered, "counts": counts})
    return {"stages": STAGES, "patents": groups, "counts": totals}
