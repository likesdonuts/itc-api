"""The claims cost log: one CSV row per create, update or retry run.

    investigation_number,build_datetime,cost_usd
    337-TA-1366,2026-09-24T15:42:10Z,0.184213

Rows are only ever appended -- never rewritten or reordered -- and a failed
run gets a row too, since its tokens were still billed. Appends are guarded
by a lock file so two builds cannot interleave rows, and on Windows a file
briefly held by a sync client such as OneDrive is retried rather than lost.
"""

from __future__ import annotations

import csv
import io
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

HEADER = ("investigation_number", "build_datetime", "cost_usd")
RETRIES = 20
RETRY_SECONDS = 0.25


def utc_stamp(moment: datetime | None = None) -> str:
    moment = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """An exclusive lock on `<file>.lock` next to the log, held while
    appending. Creating the lock file exclusively works the same on every
    platform, and a stale lock (a crashed writer) is broken after a minute.
    """
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 30
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except (FileExistsError, PermissionError):
            # Windows reports a lock file another writer is deleting at that
            # instant as PermissionError, not FileExistsError: same meaning.
            try:
                if time.time() - lock.stat().st_mtime > 60:
                    lock.unlink(missing_ok=True)
                    continue
            except (FileNotFoundError, PermissionError):
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"{lock} is held by another build")
            time.sleep(0.05)
    try:
        yield
    finally:
        for _ in range(RETRIES):
            try:
                lock.unlink(missing_ok=True)
                break
            except PermissionError:
                time.sleep(0.05)


def append(path: Path, investigation_number: str, cost_usd: float, *, when: datetime | None = None) -> str:
    """Append one run's row, creating the file with its header if needed.
    Returns the row as written.
    """
    path = Path(path)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    row = (investigation_number, utc_stamp(when), f"{cost_usd:.6f}")
    writer.writerow(row)

    with _locked(path):
        for attempt in range(RETRIES):
            try:
                new = not path.exists() or path.stat().st_size == 0
                with path.open("a", encoding="utf-8", newline="") as handle:
                    if new:
                        csv.writer(handle, lineterminator="\n").writerow(HEADER)
                    handle.write(buffer.getvalue())
                break
            except PermissionError:
                # A sync client (OneDrive) holding the file for a moment.
                if attempt == RETRIES - 1:
                    raise
                time.sleep(RETRY_SECONDS)
    return buffer.getvalue().strip()


def total(path: Path) -> float:
    """Everything spent so far, for the budget check."""
    path = Path(path)
    if not path.exists():
        return 0.0
    with path.open(encoding="utf-8", newline="") as handle:
        return sum(float(row.get("cost_usd") or 0) for row in csv.DictReader(handle))
