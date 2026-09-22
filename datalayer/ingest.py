"""Process 1 -- rebuild every case record from the IDS snapshot.

This is the whole case universe: `data/investigations.json` is rewritten from
the snapshot each time, so a case that IDS renumbers, retitles or drops is
reflected without any manual cleanup. It makes no EDIS calls and needs no
token; the only network call is the snapshot download (skippable).

It is also the only writer of `data/investigations.json`. The EDIS side of
the app (see docs.py) writes documents and nothing else, which is what keeps
investigation information and parties from being overwritten by the API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import cases, ids
from .config import IDS_DIR
from .store import Store, number_key

Logger = Callable[[str], None]


@dataclass
class IngestReport:
    snapshot_day: str | None = None
    downloaded: bool = False
    feed_date: str | None = None
    rows: int = 0
    cases: int = 0
    stages: int = 0
    multi_stage: int = 0
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    placeholders: list[str] = field(default_factory=list)
    migrated: list[tuple[str, str]] = field(default_factory=list)


def _migrate_instituted_dockets(
    store: Store, new_cases: dict[str, dict[str, Any]], log: Logger
) -> list[tuple[str, str]]:
    """Carry documents over when a docket becomes an investigation number.

    A complaint is tracked as its docket ("337-3936") until it is instituted,
    at which point IDS lists it under a real number ("337-1521") that keeps
    the docket as a field. Anything already downloaded for the docket belongs
    to that case, so it moves rather than being orphaned.
    """
    migrated: list[tuple[str, str]] = []
    for key, case in new_cases.items():
        docket = str(case.get("docket_number") or "").strip()
        if not docket or key == docket:
            continue
        old_key = f"337-{docket}"
        if old_key in new_cases or old_key == key:
            continue
        if old_key not in store.documents and old_key not in store.rss_log:
            continue
        store.rename(old_key, key)
        migrated.append((old_key, key))
        log(f"  instituted: {old_key} is now {key}; its documents moved with it.")
    return migrated


def parse_snapshot(
    store: Store,
    snapshot: ids.Snapshot,
    *,
    use_rss: bool = True,
    log: Logger = print,
) -> IngestReport:
    """Turn one stored snapshot into case records, offline."""
    payload = snapshot.load()
    rows = ids.section_337_rows(payload)
    built = cases.build_cases(rows, snapshot_day=snapshot.day)

    report = IngestReport(
        snapshot_day=snapshot.day,
        feed_date=payload.get("date"),
        rows=len(rows),
        cases=len(built),
        stages=sum(case["stage_count"] for case in built.values()),
        multi_stage=sum(1 for case in built.values() if case["stage_count"] > 1),
    )

    report.migrated = _migrate_instituted_dockets(store, built, log)

    if use_rss:
        # A docket is "known" both under its own number and as the docket of
        # the investigation it was instituted as, so an instituted complaint
        # does not come back as a second, pre-institution entry.
        known = {number_key(number) for number in built}
        known |= {
            number_key(f"337-{case['docket_number']}")
            for case in built.values()
            if case.get("docket_number")
        }
        for docket, entry in store.rss_log.items():
            if number_key(docket) not in known:
                built[docket] = cases.rss_placeholder(docket, entry)
                report.placeholders.append(docket)

    previous = set(store.investigations)
    report.added = sorted(set(built) - previous)
    report.removed = sorted(previous - set(built))

    store.investigations = dict(sorted(built.items()))
    store.save_cases()
    store.save_documents()

    log(
        f"  {report.cases} investigation(s) from {report.rows} row(s); "
        f"{report.multi_stage} have more than one stage."
    )
    if report.placeholders:
        log(f"  {len(report.placeholders)} docket(s) from the RSS feed are not in IDS yet.")
    if report.added:
        log(f"  new since the last parse: {', '.join(report.added[:12])}"
            + (" ..." if len(report.added) > 12 else ""))
    if report.removed:
        log(f"  no longer in the feed: {', '.join(report.removed[:12])}"
            + (" ..." if len(report.removed) > 12 else ""))
    return report


def run(
    store: Store,
    *,
    ids_dir: Path = IDS_DIR,
    force: bool = False,
    keep: int = ids.DEFAULT_KEEP,
    offline: bool = False,
    use_rss: bool = True,
    day: str | None = None,
    log: Logger = print,
) -> IngestReport:
    """Download today's snapshot if it is not stored yet, then parse it.

    With `offline=True` the newest stored snapshot is parsed instead, which is
    how you re-parse after changing the parser without touching the network.
    """
    if offline:
        snapshot = ids.latest(ids_dir)
        if snapshot is None:
            raise ids.IdsError(
                f"no stored IDS snapshot in {ids_dir}. Run 'python cli.py sync' once first."
            )
        log(f"Parsing stored snapshot {snapshot.path.name} (offline).")
        report = parse_snapshot(store, snapshot, use_rss=use_rss, log=log)
    else:
        sync = ids.sync(ids_dir=ids_dir, force=force, keep=keep, day=day, log=log)
        report = parse_snapshot(store, sync.snapshot, use_rss=use_rss, log=log)
        report.downloaded = sync.downloaded

    store.record_run(
        "ingest",
        snapshot=report.snapshot_day,
        feed_date=report.feed_date,
        cases=report.cases,
        stages=report.stages,
        added=len(report.added),
        removed=len(report.removed),
    )
    store.save_state()
    return report
