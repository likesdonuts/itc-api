"""How long each daily sync took, step by step (data/daily_sync_log.csv).

One row per run of the daily job (the app's Run daily sync, or
`python cli.py refresh`), appended when it finishes:

    started_at, finished_at, seconds, outcome
    seconds_<step>      ingest, documents, schedules, counsel, next_actions, render
    cases               cases whose documents were refreshed
    full_listings       of those, listed in full (weekly, or new to the list)
    requests_*          EDIS requests by kind: list pages, attachment lists, downloads
    pdfs_downloaded, orders_read, ocr_pages
    note                the job's closing message

A step that did not run is left empty. The file is tracked, like
sync_log.csv, so the history of how long syncs take survives.
"""

from __future__ import annotations

import csv
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import client
from .config import DATA_DIR

DAILY_LOG_FILE = "daily_sync_log.csv"
STEPS = ("ingest", "documents", "schedules", "counsel", "next_actions", "render")
FIELDS = (
    "started_at", "finished_at", "seconds", "outcome",
    *(f"seconds_{step}" for step in STEPS),
    "cases", "full_listings", "requests_list", "requests_attachments", "requests_download",
    "pdfs_downloaded", "orders_read", "ocr_pages", "note",
)


def _ocr_pages() -> int:
    from .claims import ocr

    return ocr.PAGES_READ


@dataclass
class DailyTimer:
    started: float = field(default_factory=time.monotonic)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    steps: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    _requests: dict[str, int] = field(default_factory=lambda: dict(client.REQUESTS))
    _ocr: int = field(default_factory=_ocr_pages)

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        began = time.monotonic()
        try:
            yield
        finally:
            self.steps[name] = self.steps.get(name, 0.0) + time.monotonic() - began

    def count(self, name: str, value: int) -> None:
        self.counts[name] = self.counts.get(name, 0) + int(value or 0)

    def row(self, outcome: str, note: str = "") -> dict[str, Any]:
        requests = {k: client.REQUESTS.get(k, 0) - self._requests.get(k, 0) for k in ("list", "attachments", "download")}
        row: dict[str, Any] = {
            "started_at": self.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "seconds": round(time.monotonic() - self.started, 1),
            "outcome": outcome,
            **{f"seconds_{s}": round(self.steps[s], 1) if s in self.steps else "" for s in STEPS},
            **{f"requests_{k}": v for k, v in requests.items()},
            "ocr_pages": _ocr_pages() - self._ocr,
            "note": " ".join(str(note or "").split()),
        }
        for name in ("cases", "full_listings", "pdfs_downloaded", "orders_read"):
            row[name] = self.counts.get(name, 0)
        return row


def path_for(data_dir: Path = DATA_DIR) -> Path:
    return Path(data_dir) / DAILY_LOG_FILE


def append(data_dir: Path, row: dict[str, Any]) -> Path:
    path = path_for(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    if not new:
        with path.open(encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle), [])
        if header != list(FIELDS):
            # A file with other columns is set aside rather than mixed with.
            path.replace(path.with_name(f"{path.stem}-before-{datetime.now():%Y%m%d%H%M%S}{path.suffix}"))
            new = True
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow(row)
    return path


def read(data_dir: Path = DATA_DIR, *, last: int | None = None) -> list[dict[str, str]]:
    path = path_for(data_dir)
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows[-last:] if last else rows


def summary(row: dict[str, Any]) -> str:
    """ "Took 6.2 min: documents 3.1 min (79 cases, 104 EDIS requests), ..." """
    def minutes(seconds: Any) -> str:
        value = float(seconds or 0)
        return f"{value / 60:.1f} min" if value >= 60 else f"{value:.0f} s"

    parts = [f"{step.replace('_', ' ')} {minutes(row.get(f'seconds_{step}'))}"
             for step in STEPS if str(row.get(f"seconds_{step}") or "") != ""]
    requests = sum(int(row.get(k) or 0) for k in ("requests_list", "requests_attachments", "requests_download"))
    return f"Took {minutes(row.get('seconds'))} ({', '.join(parts)}; {requests} EDIS requests)."
