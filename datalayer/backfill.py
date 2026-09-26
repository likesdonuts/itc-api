"""The one-time backfill: document lists for every case, without PDFs.

Counsel -- and the representation analytics built on it -- can only be read
for cases whose EDIS document list is on disk, and until now that was only
the cases someone chose to fetch. This lists the rest: the metadata of every
filing (who filed it, for whom, from which firm), which is all counsel needs
apart from the full teams in appearance PDFs. No PDF is downloaded.

It is long -- roughly one EDIS request per 20 filings, over a thousand cases
-- so it is built to be interrupted:

- newest cases first, so a partial run already covers the ones that matter most
- progress saved every `checkpoint` cases, so stopping, a closed window or an
  expired token loses at most that many
- a case already listed is skipped, so running it again continues where it
  stopped; a case EDIS has nothing for is remembered and not asked again
  (unless `retry_empty`)

Backfilled cases are marked (`"backfill": true` in documents_state.json).
The daily sync refreshes them only while they are open; see
`daily_targets`. Fetching a case by hand clears the mark, making it one of
the cases you collected.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from . import docs
from .client import EdisAuthError, EdisError
from .runner import edis_session
from .store import Store

Logger = Callable[[str], None]

CHECKPOINT = 25
# A short pause between cases keeps a run of thousands of requests polite to
# EDIS, which sits behind bot management.
PAUSE_SECONDS = 0.5

# Statuses of a case that can still get new filings. The rest (Terminated,
# Inactive, withdrawn, not instituted) are history.
OPEN_STATUSES = frozenset({
    "Active",
    "Pending before the ALJ",
    "Pending before the Commission",
    "Pre-institution",
})


@dataclass
class BackfillReport:
    listed: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    documents: int = 0
    remaining: int = 0
    stopped: str | None = None  # why it ended early, if it did


def is_backfilled(store: Store, key: str) -> bool:
    return bool((store.documents_state.get(key) or {}).get("backfill"))


def is_open(store: Store, key: str) -> bool:
    record = store.investigations.get(key) or {}
    return record.get("status") in OPEN_STATUSES and not record.get("withdrawn")


# The USITC lists some investigations decades old as "Active" -- their
# remedial orders are still in force -- though nothing has been filed in
# years. A backfilled case with no filing this long is checked monthly
# rather than daily; one filing brings it back to daily.
DORMANT_AFTER_DAYS = 730
DORMANT_RECHECK_DAYS = 30


def latest_filing(store: Store, key: str) -> str | None:
    days = [str(d.get("document_date") or "")[:10] for d in store.documents.get(key) or [] if d.get("document_date")]
    return max(days) if days else None


def is_dormant(store: Store, key: str, *, today: date | None = None) -> bool:
    latest = latest_filing(store, key)
    today = today or datetime.now(timezone.utc).date()
    return not latest or latest < (today - timedelta(days=DORMANT_AFTER_DAYS)).isoformat()


def _checked_recently(store: Store, key: str, today: date) -> bool:
    fetched = str((store.documents_state.get(key) or {}).get("fetched_at") or "")[:10]
    return bool(fetched) and fetched >= (today - timedelta(days=DORMANT_RECHECK_DAYS)).isoformat()


def daily_targets(store: Store, *, today: date | None = None) -> list[str]:
    """The cases the daily sync refreshes: every case you fetched yourself,
    and a backfilled one only while it can still get new filings -- daily
    when it has had one in the last two years, monthly when it has not.
    """
    today = today or datetime.now(timezone.utc).date()
    return [
        key
        for key in store.numbers_with_documents()
        if not is_backfilled(store, key)
        or (is_open(store, key) and (not is_dormant(store, key, today=today) or not _checked_recently(store, key, today)))
    ]


def targets(store: Store, *, retry_empty: bool = False) -> list[str]:
    """Cases with no document list yet, newest first; cases with no start
    date (the oldest, mostly) last.
    """
    pending = [
        key
        for key in store.investigations
        if not store.documents.get(key)
        and (retry_empty or not (store.documents_state.get(key) or {}).get("edis_empty"))
    ]

    def newest_first(key: str) -> tuple[bool, str, str]:
        started = str((store.investigations.get(key) or {}).get("date_initiated") or "")
        return (not started, _descending(started), key)

    return sorted(pending, key=newest_first)


def _descending(text: str) -> str:
    return "".join(chr(0x10FFFF - ord(ch)) for ch in text)


def _mark_empty(store: Store, key: str) -> None:
    store.documents_state[key] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "documents": 0,
        "backfill": True,
        "edis_empty": True,
    }


def _save(store: Store, report: BackfillReport, total: int) -> None:
    store.save_documents()
    store.record_run(
        "backfill",
        listed=len(report.listed),
        empty=len(report.empty),
        failed=len(report.failed),
        documents=report.documents,
        remaining=report.remaining,
        of=total,
        stopped=report.stopped,
    )
    store.save_state()


def run(
    store: Store,
    token: str,
    *,
    limit: int | None = None,
    retry_empty: bool = False,
    checkpoint: int = CHECKPOINT,
    pause: float = PAUSE_SECONDS,
    should_stop: Callable[[], bool] = lambda: False,
    log: Logger = print,
) -> BackfillReport:
    """List the documents of every case that has no list yet, saving as it goes."""
    todo = targets(store, retry_empty=retry_empty)
    total = len(todo)
    if limit is not None:
        todo = todo[:limit]
    report = BackfillReport(remaining=total)
    if not todo:
        log("Backfill: every case already has its document list.")
        _save(store, report, total)
        return report

    log(f"Backfill: listing documents for {len(todo)} of {total} case(s) without a list (no PDFs).")
    started = time.monotonic()
    try:
        with edis_session(token) as client:
            for index, key in enumerate(todo, 1):
                if should_stop():
                    report.stopped = "stopped on request"
                    log(f"Stopping after {index - 1} case(s), as asked.")
                    break
                try:
                    result = docs.fetch_case(client, store, key, download=False, log=log)
                except EdisAuthError:
                    raise
                except (EdisError, httpx.HTTPError) as exc:
                    report.failed.append((key, str(exc)))
                    log(f"  [{index}/{len(todo)}] {key}: ! {exc}")
                else:
                    if result.ok:
                        store.documents_state[key]["backfill"] = True
                        report.listed.append(key)
                        report.documents += result.document_count
                        report.remaining -= 1
                        note = f"{result.document_count} document(s)"
                    else:
                        _mark_empty(store, key)
                        report.empty.append(key)
                        report.remaining -= 1
                        note = "nothing in EDIS"
                    log(f"  [{index}/{len(todo)}] {key}: {note}{_eta(started, index, len(todo))}")
                if index % checkpoint == 0:
                    _save(store, report, total)
                    log(f"  saved: {len(report.listed)} listed, {len(report.empty)} empty so far.")
                if pause:
                    time.sleep(pause)
    except EdisAuthError as exc:
        report.stopped = str(exc)
        log(f"  ! EDIS stopped the backfill: {exc}")
    finally:
        _save(store, report, total)

    log(
        f"Backfill: {len(report.listed)} case(s) listed ({report.documents} documents), "
        f"{len(report.empty)} with nothing in EDIS, {len(report.failed)} failed; "
        f"{report.remaining} still to do."
    )
    return report


def _eta(started: float, done: int, total: int) -> str:
    if done < 5 or done >= total:
        return ""
    left = (time.monotonic() - started) / done * (total - done)
    return f"  (about {left / 60:.0f} min left)" if left >= 60 else ""


def progress(store: Store) -> dict[str, Any]:
    """What the dashboard shows: how many cases still have no list."""
    return {"remaining": len(targets(store)), "cases": len(store.investigations)}
