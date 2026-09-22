"""Offline maintenance: rewrite stored document dates to ISO 8601.

EDIS hands out dates in its own format ("2026/09/18 11:39:00") and older runs
of this app stored them as they came. Case records do not need this -- they
are rebuilt from the IDS snapshot every sync, already normalized -- so this
only has documents left to fix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import dates

from .store import Store

Logger = Callable[[str], None]

DOCUMENT_DATE_FIELDS = ("document_date", "official_received_date")


@dataclass
class NormalizeReport:
    changed: int = 0
    unparsed: list[str] = field(default_factory=list)


def _rewrite(container: dict[str, Any], fields: tuple[str, ...], report: NormalizeReport) -> None:
    for name in fields:
        original = container.get(name)
        if not original:
            continue
        normalized = dates.to_iso(original)
        if normalized != original:
            container[name] = normalized
            report.changed += 1
        if dates.parse(original) is None:
            report.unparsed.append(str(original))


def run(store: Store, *, log: Logger = print) -> NormalizeReport:
    report = NormalizeReport()

    for documents in store.documents.values():
        for document in documents:
            _rewrite(document, DOCUMENT_DATE_FIELDS, report)

    if report.changed:
        store.save_documents()
    log(f"Normalized {report.changed} stored document date(s) to ISO 8601.")
    if report.unparsed:
        sample = ", ".join(sorted(set(report.unparsed))[:5])
        log(f"  ! {len(report.unparsed)} value(s) were left as-is, unrecognized: {sample}")
    return report
