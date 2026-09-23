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
from .store import Store

Logger = Callable[[str], None]

# A snapshot dropping more than this share of the cases already on disk is
# far likelier to be an incomplete feed than a real mass withdrawal: in the
# IDS file cases are historical and effectively never leave, so a normal day
# removes none. The floor keeps a handful of cases from tripping it while
# there is barely anything on disk.
REMOVAL_LIMIT = 0.02
REMOVAL_FLOOR = 10


class SuspectSnapshotError(ids.IdsError):
    """A snapshot that drops so many cases it is probably incomplete.

    Subclasses IdsError so the CLI reports it like any other bad snapshot.
    """


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
    withdrawn: list[str] = field(default_factory=list)
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
        if old_key not in store.documents:
            continue
        store.rename(old_key, key)
        migrated.append((old_key, key))
        log(f"  instituted: {old_key} is now {key}; its documents moved with it.")
    return migrated


def _check_removals(gone: list[str], previous: int, day: str, allow: bool) -> None:
    # Both conditions have to hold: the share catches a feed that came back
    # gutted, and the floor keeps a real handful of withdrawals from tripping
    # it while there is barely anything on disk. An empty `gone` short-circuits
    # on the floor, so `previous` is never zero by the time it is divided by.
    if allow or len(gone) <= REMOVAL_FLOOR or len(gone) / previous <= REMOVAL_LIMIT:
        return
    raise SuspectSnapshotError(
        f"snapshot {day} lists {previous - len(gone)} of the {previous} cases on disk, "
        f"dropping {len(gone)} ({len(gone) / previous:.0%}). The IDS feed is a "
        "historical file that should never lose that many in a day, so this one is "
        "most likely incomplete and nothing has been changed. Check the download, or "
        "re-run with --allow-removals if the withdrawals are real."
    )


def _carry_withdrawn(
    store: Store, built: dict[str, dict[str, Any]], day: str
) -> list[str]:
    """Keep a case the feed has stopped listing, marked and dated.

    Its page stays up saying whatever the last snapshot that listed it said,
    rather than vanishing, and the documents fetched for it stay reachable.
    The mark is not sticky: a case the feed lists again is rebuilt from the
    feed like any other, flag and all.

    Returns every case currently carried this way, not only the new ones.
    """
    carried = []
    for key, record in store.investigations.items():
        if key in built or not cases.is_case_record(record):
            continue
        record = dict(record)
        record.setdefault("last_listed_snapshot", record.get("ids_snapshot") or day)
        record["withdrawn"] = True
        built[key] = record
        carried.append(key)
    return sorted(carried)


def parse_snapshot(
    store: Store,
    snapshot: ids.Snapshot,
    *,
    allow_removals: bool = False,
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

    previous = set(store.investigations)
    # Cases already marked withdrawn are expected to be missing, so only the
    # ones the feed listed last time count as having gone this time.
    listed = {
        key
        for key, record in store.investigations.items()
        if not (isinstance(record, dict) and record.get("withdrawn"))
    }
    report.added = sorted(set(built) - previous)
    report.removed = sorted(listed - set(built))

    _check_removals(report.removed, len(listed), snapshot.day, allow_removals)
    report.withdrawn = _carry_withdrawn(store, built, snapshot.day)

    store.investigations = dict(sorted(built.items()))
    store.save_cases()
    store.save_documents()

    log(
        f"  {report.cases} investigation(s) from {report.rows} row(s); "
        f"{report.multi_stage} have more than one stage."
    )
    if report.added:
        log(f"  new since the last parse: {', '.join(report.added[:12])}"
            + (" ..." if len(report.added) > 12 else ""))
    if report.removed:
        log(
            f"  no longer in the feed: {', '.join(report.removed[:12])}"
            + (" ..." if len(report.removed) > 12 else "")
            + ". Their pages stay up, marked as withdrawn."
        )
    if len(report.withdrawn) > len(report.removed):
        log(f"  {len(report.withdrawn)} case(s) are marked withdrawn in total.")
    return report


def run(
    store: Store,
    *,
    ids_dir: Path = IDS_DIR,
    force: bool = False,
    keep: int = ids.DEFAULT_KEEP,
    offline: bool = False,
    allow_removals: bool = False,
    day: str | None = None,
    log: Logger = print,
) -> IngestReport:
    """Download today's snapshot if it is not stored yet, then parse it.

    With `offline=True` the newest stored snapshot is parsed instead, which is
    how you re-parse after changing the parser without touching the network.
    """
    parse = dict(allow_removals=allow_removals, log=log)
    if offline:
        snapshot = ids.latest(ids_dir)
        if snapshot is None:
            raise ids.IdsError(
                f"no stored IDS snapshot in {ids_dir}. Run 'python cli.py sync' once first."
            )
        log(f"Parsing stored snapshot {snapshot.path.name} (offline).")
        report = parse_snapshot(store, snapshot, **parse)
    else:
        sync = ids.sync(ids_dir=ids_dir, force=force, keep=keep, day=day, log=log)
        report = parse_snapshot(store, sync.snapshot, **parse)
        report.downloaded = sync.downloaded

    store.record_run(
        "ingest",
        snapshot=report.snapshot_day,
        feed_date=report.feed_date,
        cases=report.cases,
        stages=report.stages,
        added=len(report.added),
        withdrawn=len(report.withdrawn),
    )
    store.save_state()
    return report
