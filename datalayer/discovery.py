"""Process 1 -- new case discovery.

Reads the 337-complaint RSS feed to learn which dockets exist, logs what the
feed reported, and then calls EDIS for the dockets we are not already
tracking. This is the only process that touches the RSS feed; it is also the
only one that can add a case the user has never heard of.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from .client import fetch_rss
from .config import RSS_URL
from .records import SyncResult
from .runner import Logger, edis_session, load_ids_lookup, sync_many
from .store import Store


@dataclass
class DiscoveryReport:
    feed_items: int = 0
    new_documents: int = 0
    rss_ok: bool = True
    candidates: list[str] = field(default_factory=list)
    results: list[SyncResult] = field(default_factory=list)

    @property
    def added(self) -> list[SyncResult]:
        return [r for r in self.results if r.ok and r.created]

    @property
    def failed(self) -> list[SyncResult]:
        return [r for r in self.results if not r.ok]

    @property
    def downloaded(self) -> int:
        return sum(r.downloaded for r in self.results)


def update_rss_log(
    rss_log: dict[str, Any], items: list[Any], log: Logger = print
) -> tuple[dict[str, Any], int]:
    """Fold newly fetched RSS items into the persistent per-docket log.

    Returns the updated log and a count of previously-unseen (docket, doc_id)
    pairs, so the caller can report what's new this run.
    """
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    skipped = 0

    for item in items:
        if not item.docket_number:
            skipped += 1
            log(f"  ! could not extract a docket number from RSS item: {item.title!r}")
            continue

        docket_entry = rss_log.setdefault(
            item.docket_number,
            {"first_collected_at": now, "documents": {}},
        )
        docket_entry["last_collected_at"] = now

        if not item.doc_id:
            continue

        if item.doc_id not in docket_entry["documents"]:
            new_count += 1
        docket_entry["documents"][item.doc_id] = {
            "doc_type": item.doc_type,
            "pub_date": item.pub_date,
            "pub_date_iso": item.pub_date_iso,
            "collected_at": docket_entry["documents"].get(item.doc_id, {}).get("collected_at", now),
            "link": item.link,
            "guid": item.guid,
        }

    if skipped:
        log(f"  {skipped} RSS item(s) had no recognizable docket number.")
    return rss_log, new_count


def unseen_dockets(store: Store) -> list[str]:
    """Dockets the RSS log knows about that have no investigation record yet."""
    return sorted(d for d in store.rss_log if store.find_investigation_key(d) is None)


def run(
    store: Store,
    token: str | None = None,
    *,
    rss_url: str = RSS_URL,
    limit: int | None = None,
    download: bool = True,
    use_ids: bool = True,
    dry_run: bool = False,
    log: Logger = print,
) -> DiscoveryReport:
    """Check the feed, then fetch EDIS data for every docket that is new to us.

    With `dry_run=True` the RSS log is still refreshed (so nothing is lost)
    but no EDIS calls are made -- useful for seeing what is out there before
    committing to a download.
    """
    report = DiscoveryReport()

    log("Checking RSS feed for 337 complaint filings...")
    try:
        items = fetch_rss(rss_url)
        store.rss_log, report.new_documents = update_rss_log(store.rss_log, items, log)
        store.save_rss_log()
        report.feed_items = len(items)
        log(f"  {len(items)} item(s) in feed, {report.new_documents} new document(s) logged.")
    except httpx.HTTPError as exc:
        report.rss_ok = False
        log(f"  ! RSS feed fetch failed ({exc}); working from the previously logged dockets only.")

    candidates = unseen_dockets(store)
    if limit is not None:
        candidates = candidates[:limit]
    report.candidates = candidates

    if not candidates:
        log("No undiscovered dockets; every docket in the RSS log already has a record.")
        store.record_run("discovery", feed_items=report.feed_items, new_cases=0, dry_run=dry_run)
        store.save_state()
        return report

    log(f"{len(candidates)} docket(s) without a record yet: {', '.join(candidates)}")
    if dry_run:
        log("Dry run -- stopping before any EDIS calls.")
        return report

    if not token:
        raise ValueError("an EDIS token is required unless dry_run=True")

    ids_lookup = load_ids_lookup(use_ids, log)
    with edis_session(token) as client:
        report.results = sync_many(
            client, store, candidates, ids_lookup=ids_lookup, download=download, log=log
        )

    store.save_investigations()
    store.record_run(
        "discovery",
        feed_items=report.feed_items,
        new_documents=report.new_documents,
        new_cases=len(report.added),
        attachments_downloaded=report.downloaded,
    )
    store.save_state()
    return report
