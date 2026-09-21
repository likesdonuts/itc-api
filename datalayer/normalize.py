"""Offline maintenance: rewrite stored dates into ISO 8601.

Records written before dates were normalized on the way in still hold the
shape their source used -- "01-13-2026" from IDS next to "2026/08/21
16:25:00" from EDIS. Nothing here talks to an API; it just re-reads what is
already on disk through the same parser the data layer now uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import dates

from .store import Store

Logger = Callable[[str], None]

RECORD_DATE_FIELDS = ("date_initiated", "last_refreshed")
DOCUMENT_DATE_FIELDS = ("document_date", "official_received_date")

APPROX_NOTE = "approx., from earliest complaint filing"


@dataclass
class NormalizeReport:
    changed: int = 0
    unparsed: list[str] = field(default_factory=list)


def _rewrite(container: dict[str, Any], fields: tuple[str, ...], report: NormalizeReport) -> None:
    for name in fields:
        original = container.get(name)
        if not original:
            continue
        note = dates.note_of(original)
        normalized = dates.to_iso(original)
        if normalized != original:
            container[name] = normalized
            report.changed += 1
        if dates.parse(original) is None:
            report.unparsed.append(str(original))
        if note and name == "date_initiated":
            container.setdefault("date_initiated_note", note)


def run(store: Store, *, log: Logger = print) -> NormalizeReport:
    report = NormalizeReport()

    for record in store.investigations.values():
        _rewrite(record, RECORD_DATE_FIELDS, report)
    for documents in store.documents.values():
        for document in documents:
            _rewrite(document, DOCUMENT_DATE_FIELDS, report)

    if report.changed:
        store.save_investigations()
    log(f"Normalized {report.changed} stored date(s) to ISO 8601.")
    if report.unparsed:
        sample = ", ".join(sorted(set(report.unparsed))[:5])
        log(f"  ! {len(report.unparsed)} value(s) were left as-is, unrecognized: {sample}")
    return report
