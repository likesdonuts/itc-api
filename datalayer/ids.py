"""The daily IDS download.

https://ids.usitc.gov/investigations.json is the USITC's own dump of every
investigation it has ever run -- around 37 MB, rewritten once a day. This
module does nothing but get a copy of it and keep the copies tidy:

    data/ids/investigations-2026-09-23T134502Z.json.gz

The name carries the moment the copy was taken, so the file itself records
when the data arrived and a second copy on the same day cannot overwrite the
first. Snapshots are gzipped (37 MB becomes about 3 MB) and never rewritten.
Everything downstream reads a snapshot rather than the network, so parsing,
schema changes and re-rendering all work offline and always against a file
you can go back to.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from .client import IDS_URL
from .config import IDS_DIR

Logger = Callable[[str], None]

PREFIX = "investigations-"
SUFFIX = ".json.gz"

STAMP_FORMAT = "%Y-%m-%dT%H%M%SZ"
# Colons are illegal in Windows filenames, so the time runs them together.
STAMP_RE = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2})(?:T(?P<time>\d{6})Z)?$")

# Keeping a month of snapshots is enough to see what changed and to re-parse
# an older day; beyond that they are just disk.
DEFAULT_KEEP = 30

SECTION_337_CATEGORY = "337 - Unfair Imports"


class IdsError(RuntimeError):
    pass


@dataclass
class Snapshot:
    """One stored copy of the feed."""

    path: Path
    stamp: str

    @property
    def day(self) -> str:
        return self.stamp[:10]

    @property
    def taken_at(self) -> str | None:
        """When the copy was taken, from its name. None for the older names,
        which carried the day only.
        """
        match = STAMP_RE.match(self.stamp)
        if not match or not match.group("time"):
            return None
        time = match.group("time")
        return f"{match.group('day')}T{time[:2]}:{time[2:4]}:{time[4:]}+00:00"

    @property
    def size(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    def load(self) -> dict[str, Any]:
        return load(self.path)


@dataclass
class SyncResult:
    snapshot: Snapshot
    downloaded: bool
    rows: int = 0
    section_337_rows: int = 0
    feed_date: str | None = None
    pruned: list[Path] | None = None


def snapshot_stamp(path: Path) -> str:
    return path.name[len(PREFIX) : -len(SUFFIX)]


def snapshots(ids_dir: Path = IDS_DIR) -> list[Snapshot]:
    """Every stored snapshot, oldest first.

    The stamps sort chronologically as text, and a day-only name from before
    snapshots were timestamped sorts ahead of that day's timestamped ones,
    which is the right order for a file taken before the change.
    """
    directory = Path(ids_dir)
    if not directory.is_dir():
        return []
    found = sorted(directory.glob(f"{PREFIX}*{SUFFIX}"))
    keep = [p for p in found if STAMP_RE.match(snapshot_stamp(p))]
    return [Snapshot(path=path, stamp=snapshot_stamp(path)) for path in keep]


def latest(ids_dir: Path = IDS_DIR) -> Snapshot | None:
    stored = snapshots(ids_dir)
    return stored[-1] if stored else None


def snapshot_for(day: str, ids_dir: Path = IDS_DIR) -> Snapshot | None:
    """The newest snapshot taken on a given day, whenever that day it was."""
    same_day = [s for s in snapshots(ids_dir) if s.day == day]
    return same_day[-1] if same_day else None


def load(path: Path) -> dict[str, Any]:
    """Read a snapshot back. Raises IdsError if it is not readable, so a
    truncated file is reported rather than silently treated as empty.
    """
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError, EOFError) as exc:
        raise IdsError(f"could not read snapshot {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise IdsError(f"unexpected snapshot contents in {path.name}")
    return payload


def download(url: str = IDS_URL, timeout: float = 300.0) -> bytes:
    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise IdsError(f"IDS download failed: {exc}") from exc
    return response.content


def store(raw: bytes, *, now: datetime | None = None, ids_dir: Path = IDS_DIR) -> Snapshot:
    """Gzip the downloaded bytes into a snapshot stamped with the time they
    arrived, after checking they parse -- a snapshot on disk should always be
    usable.
    """
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise IdsError(f"IDS returned something that is not JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise IdsError("IDS returned JSON without the expected 'data' list")

    stamp = (now or datetime.now(timezone.utc)).strftime(STAMP_FORMAT)
    directory = Path(ids_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{PREFIX}{stamp}{SUFFIX}"
    tmp = path.with_name(f".{path.name}.tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(tmp, path)
    return Snapshot(path=path, stamp=stamp)


def prune(ids_dir: Path = IDS_DIR, keep: int = DEFAULT_KEEP) -> list[Path]:
    """Drop snapshots older than the newest `keep` days.

    Counted in days rather than files so that taking a second copy of a day
    with --force cannot push an older day off the end.
    """
    if keep <= 0:
        return []
    stored = snapshots(ids_dir)
    kept_days = sorted({snapshot.day for snapshot in stored})[-keep:]
    removed = []
    for snapshot in stored:
        if snapshot.day not in kept_days:
            snapshot.path.unlink()
            removed.append(snapshot.path)
    return removed


def rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def is_section_337(row: dict[str, Any]) -> bool:
    """Section 337 rows are tagged by category; the investigation number is
    the fallback for the handful of rows with no categories at all.
    """
    for category in row.get("Investigation Categories") or []:
        if isinstance(category, dict) and category.get("Name") == SECTION_337_CATEGORY:
            return True
    return str(row.get("Investigation Number") or "").startswith("337-")


def section_337_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in rows(payload) if is_section_337(row)]


def sync(
    *,
    ids_dir: Path = IDS_DIR,
    url: str = IDS_URL,
    force: bool = False,
    keep: int = DEFAULT_KEEP,
    now: datetime | None = None,
    log: Logger = print,
) -> SyncResult:
    """Make sure today's snapshot exists, and report what is in it.

    Called again on the same day it does nothing unless `force=True`: the
    feed is only rebuilt daily, so a second download would be the same bytes.
    """
    now = now or datetime.now(timezone.utc)
    existing = snapshot_for(now.date().isoformat(), ids_dir)

    if existing and not force:
        log(f"Today's IDS snapshot is already stored ({existing.path.name}).")
        snapshot, downloaded = existing, False
    else:
        log(f"Downloading {url} ...")
        snapshot = store(download(url), now=now, ids_dir=ids_dir)
        log(f"  stored {snapshot.path.name} ({snapshot.size / 1_000_000:.1f} MB gzipped).")
        downloaded = True

    payload = snapshot.load()
    result = SyncResult(
        snapshot=snapshot,
        downloaded=downloaded,
        rows=len(rows(payload)),
        section_337_rows=len(section_337_rows(payload)),
        feed_date=payload.get("date"),
        pruned=prune(ids_dir, keep),
    )
    log(
        f"  {result.rows} investigation row(s) in the feed, "
        f"{result.section_337_rows} of them Section 337."
    )
    if result.pruned:
        log(f"  removed {len(result.pruned)} snapshot(s) older than the last {keep} kept.")
    return result
