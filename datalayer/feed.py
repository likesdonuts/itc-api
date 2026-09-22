"""The EDIS complaint RSS feed.

The feed is no longer how cases are discovered -- the IDS snapshot lists every
investigation, including pre-institution dockets. It is still worth reading
for two things:

  * it names a brand-new complaint up to a day before IDS's next rebuild, so
    the case shows up on the site immediately (see cases.rss_placeholder)
  * it carries the EDIS document IDs for a complaint that has no EDIS
    investigation record yet, which is the only route to those PDFs

So this writes `data/rss_log.json` and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from .client import fetch_rss
from .config import RSS_URL
from .store import Store

Logger = Callable[[str], None]


@dataclass
class FeedReport:
    items: int = 0
    new_documents: int = 0
    ok: bool = True


def update_rss_log(
    rss_log: dict[str, Any], items: list[Any], log: Logger = print
) -> tuple[dict[str, Any], int]:
    """Fold newly fetched RSS items into the persistent per-docket log.

    Returns the updated log and a count of previously-unseen (docket, doc_id)
    pairs, so the caller can report what is new this run.
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


def run(store: Store, *, rss_url: str = RSS_URL, log: Logger = print) -> FeedReport:
    report = FeedReport()
    log("Checking the EDIS complaint RSS feed...")
    try:
        items = fetch_rss(rss_url)
    except httpx.HTTPError as exc:
        report.ok = False
        log(f"  ! RSS feed fetch failed ({exc}); keeping the previously logged dockets.")
        return report

    store.rss_log, report.new_documents = update_rss_log(store.rss_log, items, log)
    store.save_rss_log()
    report.items = len(items)
    log(f"  {report.items} item(s) in the feed, {report.new_documents} new document(s) logged.")
    return report
