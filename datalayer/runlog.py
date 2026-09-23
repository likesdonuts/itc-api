"""One appended CSV row per IDS ingest, for watching the daily download.

`data/sync_log.csv` grows by a line every time a snapshot is parsed, whether
it was downloaded just now, reused from earlier today, or re-read offline.
The point is to be able to open it in a spreadsheet and see at a glance when
a download went wrong: the row counts and the file size should barely move
from one day to the next, `feed_date` should advance, and `cases_changed`
should be a handful rather than everything or nothing.

Refused snapshots are written too, with `outcome` set to "refused" and the
reason in `note`, so a day the guard stopped is visible rather than missing.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import DATA_DIR

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime only
    from .ingest import IngestReport

SYNC_LOG_FILE = "sync_log.csv"

FIELDS = (
    "run_at",
    "mode",
    "outcome",
    "snapshot",
    "snapshot_taken_at",
    "snapshot_bytes",
    "feed_date",
    "rows_total",
    "rows_337",
    "cases_in_file",
    "stages_in_file",
    "cases_added",
    "cases_changed",
    "status_changes",
    "cases_left_feed",
    "cases_withdrawn_total",
    "cases_renumbered",
    "cases_on_site",
    "seconds",
    "note",
)


def path_for(data_dir: Path = DATA_DIR) -> Path:
    return Path(data_dir) / SYNC_LOG_FILE


def row_for(report: "IngestReport") -> dict[str, Any]:
    snapshot = report.snapshot
    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": report.mode,
        "outcome": report.outcome,
        "snapshot": snapshot.path.name if snapshot else "",
        "snapshot_taken_at": (snapshot.taken_at if snapshot else None) or "",
        "snapshot_bytes": snapshot.size if snapshot else "",
        "feed_date": report.feed_date or "",
        "rows_total": report.rows_total,
        "rows_337": report.rows,
        "cases_in_file": report.cases,
        "stages_in_file": report.stages,
        "cases_added": len(report.added),
        "cases_changed": len(report.changed),
        "status_changes": report.status_changes,
        "cases_left_feed": len(report.removed),
        "cases_withdrawn_total": len(report.withdrawn),
        "cases_renumbered": len(report.migrated),
        # Withdrawn cases are kept, so the site can hold more than the file.
        "cases_on_site": report.cases + len(report.withdrawn),
        "seconds": report.seconds,
        "note": " ".join(report.note.split()),
    }


def append(data_dir: Path, report: "IngestReport") -> Path:
    return write_row(path_for(data_dir), row_for(report))


def write_row(path: Path, row: dict[str, Any]) -> Path:
    """Append one row, starting the file with a header if it is new.

    A file whose header does not match the current columns is set aside
    rather than appended to, so adding a column later cannot quietly produce
    a CSV with two different shapes in it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and read_header(path) != list(FIELDS):
        path.replace(path.with_name(f"{path.stem}-before-{_stamp()}{path.suffix}"))

    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow(row)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def read_header(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return next(csv.reader(handle), [])
    except OSError:
        return []


def read(data_dir: Path = DATA_DIR, *, last: int | None = None) -> list[dict[str, str]]:
    """The logged runs, oldest first, or the last few of them."""
    path = path_for(data_dir)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows[-last:] if last else rows


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
