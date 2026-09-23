"""One record per investigation, built from the IDS snapshot.

IDS has a row per *stage*, not per investigation: 337-1478 appears once as
its "Violation" phase, again as a "Remand", again as a "Bond Return", each
with its own Investigation ID, its own dates, and its own participants. Of
1679 Section 337 rows in the feed, 1381 are distinct investigation numbers
and 171 of those numbers have between two and eight rows.

Rather than pick one row and throw the rest away (which is what keying the
feed by investigation number used to do), a case here is one record that
carries all of its stages:

    stages[]      every row for that number, oldest first
    primary       the stage the case information comes from: the Violation
                  phase, which is the investigation proper
    current       the stage that started most recently, which is what the
                  case status and phase reflect

So the site shows one page per investigation with its stages listed on it and
linked from each other, instead of near-duplicate pages fighting over the
same number.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

import dates

from .flatten import flatten_row

# The phase an investigation is instituted under; the others are proceedings
# that happen afterwards.
PRIMARY_PHASE = "Violation"

IDS_SOURCE = "ids"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stage(row: dict[str, Any]) -> dict[str, Any]:
    fields, lists = flatten_row(row)
    return {
        "stage_id": fields.get("investigation_id"),
        "phase": fields.get("investigation_phase"),
        "phase_number": fields.get("phase_number"),
        "status": fields.get("investigation_status"),
        "start_date": fields.get("start_date"),
        "end_date": fields.get("investigation_end_date"),
        "fields": fields,
        "lists": lists,
    }


def _order_key(stage: dict[str, Any]) -> tuple[str, int]:
    return (dates.sort_key(stage.get("start_date")), int(stage.get("stage_id") or 0))


def _title(stage: dict[str, Any]) -> str | None:
    """Prefer IDS's "Topic" ("Certain Vaporizer Cartridges and Components
    Thereof") over "Full Title", which repeats the number and phase.
    """
    fields = stage["fields"]
    topic = fields.get("topic")
    if topic:
        return str(topic)
    full = str(fields.get("full_title") or "").strip()
    if not full:
        return None
    return full.split("; Inv. No.")[0].split(", Inv. No.")[0].strip() or full


def build_case(number: str, rows: Iterable[dict[str, Any]], *, snapshot_day: str | None = None) -> dict[str, Any]:
    """Fold every IDS row for one investigation number into a single record."""
    stages = sorted((_stage(row) for row in rows), key=_order_key)

    primary = next((s for s in stages if s.get("phase") == PRIMARY_PHASE), stages[0])
    current = stages[-1]
    for stage in stages:
        stage["is_primary"] = stage is primary
        stage["is_current"] = stage is current

    start_dates = sorted(dates.sort_key(s.get("start_date")) for s in stages if s.get("start_date"))
    date_initiated = (
        primary.get("start_date")
        or (start_dates[0][:10] if start_dates else None)
        or primary["fields"].get("initiating_document_received_date")
    )

    return {
        "investigation_number": number,
        "title": _title(primary) or _title(current) or number,
        "docket_number": primary["fields"].get("docket_number")
        or current["fields"].get("docket_number"),
        "official_number": primary["fields"].get("official_investigation_number"),
        "status": current.get("status") or primary.get("status"),
        "phase": current.get("phase"),
        "date_initiated": date_initiated,
        "date_ended": current.get("end_date"),
        "phases": [s.get("phase") for s in stages if s.get("phase")],
        "stage_count": len(stages),
        "primary_stage": primary.get("stage_id"),
        "current_stage": current.get("stage_id"),
        "source": IDS_SOURCE,
        "ids_snapshot": snapshot_day,
        "ids_synced_at": _now(),
        "stages": stages,
    }


def group_rows(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Bucket IDS rows by investigation number.

    The feed also carries `official_investigation_number`, but it disagrees
    with the number in the row's own title for 942 of 1679 Section 337 rows
    (337-1432 calls itself 337-1393), so "Investigation Number" is the only
    identifier used here.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        number = str(row.get("Investigation Number") or "").strip()
        if number:
            grouped.setdefault(number, []).append(row)
    return grouped


def build_cases(
    rows: Iterable[dict[str, Any]], *, snapshot_day: str | None = None
) -> dict[str, dict[str, Any]]:
    grouped = group_rows(rows)
    return {
        number: build_case(number, group, snapshot_day=snapshot_day)
        for number, group in sorted(grouped.items())
    }


def primary_stage(case: dict[str, Any]) -> dict[str, Any] | None:
    stages = case.get("stages") or []
    return next((s for s in stages if s.get("is_primary")), stages[0] if stages else None)


def current_stage(case: dict[str, Any]) -> dict[str, Any] | None:
    stages = case.get("stages") or []
    return next((s for s in stages if s.get("is_current")), stages[-1] if stages else None)


def is_case_record(record: Any) -> bool:
    """Records written before the IDS rewrite have no stages and no source."""
    return isinstance(record, dict) and "stages" in record and "source" in record
