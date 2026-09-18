"""Shared plumbing for the data-layer processes.

Both processes do the same thing once they know which case numbers to work
on: open one authenticated EDIS session, sync each case, keep going when an
individual case fails, and stop immediately if the token is rejected.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator

import httpx

from .client import EdisAuthError, EdisClient, EdisError, fetch_ids_investigations
from .records import SyncResult, sync_case
from .store import Store

Logger = Callable[[str], None]


class ProcessAborted(RuntimeError):
    """Raised when the run cannot usefully continue (e.g. a rejected token)."""


@contextmanager
def edis_session(token: str) -> Iterator[EdisClient]:
    with EdisClient(token) as client:
        try:
            yield client
        except EdisAuthError as exc:
            raise ProcessAborted(str(exc)) from exc


def load_ids_lookup(enabled: bool = True, log: Logger = print) -> dict[str, dict[str, Any]]:
    """The public IDS feed carries the 'Start Date' EDIS itself omits."""
    if not enabled:
        return {}
    log("Fetching public IDS metadata (investigation start dates, etc.)...")
    try:
        return fetch_ids_investigations()
    except httpx.HTTPError as exc:
        log(f"  ! IDS feed fetch failed ({exc}); 'date initiated' may be unavailable.")
        return {}


def sync_many(
    client: EdisClient,
    store: Store,
    numbers: Iterable[str],
    *,
    ids_lookup: dict[str, dict[str, Any]] | None = None,
    download: bool = True,
    log: Logger = print,
) -> list[SyncResult]:
    results: list[SyncResult] = []
    for number in numbers:
        log(f"{number}...")
        try:
            result = sync_case(
                client, store, number, ids_lookup=ids_lookup, download=download, log=log
            )
        except EdisAuthError:
            raise
        except EdisError as exc:
            result = SyncResult.skipped(number, str(exc))
        except httpx.HTTPError as exc:
            result = SyncResult.skipped(number, f"network error: {exc}")

        if not result.ok:
            log(f"  ! skipped: {result.note}")
        else:
            if result.renamed_from:
                log(f"  instituted: {result.renamed_from} is now {result.key}")
            log(
                f"  status={result.status!r}, {result.document_count} document(s), "
                f"{result.downloaded} new attachment(s) downloaded."
            )
        results.append(result)
    return results
