"""Process 2 -- targeted update.

Takes one or more investigation/docket numbers and re-pulls just those cases
from the EDIS API. No RSS feed, no discovery of anything new unless you name
it: whatever you list is exactly what gets called.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .records import SyncResult
from .runner import Logger, edis_session, load_ids_lookup, sync_many
from .store import Store


@dataclass
class UpdateReport:
    requested: list[str] = field(default_factory=list)
    results: list[SyncResult] = field(default_factory=list)

    @property
    def updated(self) -> list[SyncResult]:
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> list[SyncResult]:
        return [r for r in self.results if not r.ok]

    @property
    def downloaded(self) -> int:
        return sum(r.downloaded for r in self.results)


def resolve_targets(store: Store, numbers: Iterable[str]) -> tuple[list[str], list[str]]:
    """Map the numbers a user typed onto stored keys.

    Returns the resolved targets plus the numbers we have never seen. An
    untracked number is still a valid target -- EDIS may well know it -- so
    the caller decides whether to try it or refuse.
    """
    targets: list[str] = []
    untracked: list[str] = []
    seen: set[str] = set()

    for number in numbers:
        number = str(number).strip()
        if not number:
            continue
        key = store.find_key(number)
        if key is None:
            untracked.append(number)
        target = key or number
        if target not in seen:
            seen.add(target)
            targets.append(target)

    return targets, untracked


def run(
    store: Store,
    token: str,
    numbers: Iterable[str],
    *,
    download: bool = True,
    use_ids: bool = True,
    known_only: bool = False,
    log: Logger = print,
) -> UpdateReport:
    targets, untracked = resolve_targets(store, numbers)
    report = UpdateReport(requested=targets)

    if untracked:
        if known_only:
            log(f"  ! not tracked, skipping: {', '.join(untracked)}")
            targets = [t for t in targets if t not in set(untracked)]
            report.requested = targets
        else:
            log(f"  {len(untracked)} number(s) not tracked yet; will try EDIS anyway: {', '.join(untracked)}")

    if not targets:
        log("Nothing to update.")
        return report

    ids_lookup = load_ids_lookup(use_ids, log)
    with edis_session(token) as client:
        report.results = sync_many(
            client, store, targets, ids_lookup=ids_lookup, download=download, log=log
        )

    store.save_investigations()
    store.record_run(
        "update",
        numbers=targets,
        updated=len(report.updated),
        attachments_downloaded=report.downloaded,
    )
    store.save_state()
    return report
