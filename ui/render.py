"""UI layer: build the static site from whatever is already in data/.

This never touches the network and never needs an EDIS token, so iterating on
the HTML/CSS in templates.py -- or on the field mapping in ui_schema.json --
is just a re-render of the stored data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import schema as ui_schema
from datalayer.cases import is_case_record
from datalayer.config import DATA_DIR, SCHEMA_PATH, SITE_DIR
from datalayer.store import Store, write_text_atomic

from . import templates

Logger = Callable[[str], None]


@dataclass
class RenderReport:
    site_dir: Path
    index_path: Path
    pages: int
    skipped: list[str] = field(default_factory=list)
    unused_sources: list[str] = field(default_factory=list)


def render_site(
    store: Store | None = None,
    *,
    data_dir: Path = DATA_DIR,
    site_dir: Path = SITE_DIR,
    schema_path: Path = SCHEMA_PATH,
    log: Logger = print,
) -> RenderReport:
    store = store or Store.load(data_dir)
    schema = ui_schema.load(schema_path)
    site_dir = Path(site_dir)
    detail_dir = site_dir / "investigations"
    detail_dir.mkdir(parents=True, exist_ok=True)

    cases: dict[str, Any] = {}
    skipped: list[str] = []
    for number, record in store.investigations.items():
        if is_case_record(record):
            cases[number] = record
        else:
            skipped.append(number)

    document_counts = {number: len(docs) for number, docs in store.documents.items()}
    meta = {"snapshot_day": (store.state.get("runs", {}).get("ingest") or {}).get("snapshot")}

    index_path = site_dir / "index.html"
    write_text_atomic(
        index_path,
        templates.render_index(
            list(cases.values()),
            schema,
            document_counts=document_counts,
            counsel=store.counsel,
            meta=meta,
        ),
    )

    written: set[Path] = set()
    for number, case in cases.items():
        page = detail_dir / f"{templates.slug_for(number)}.html"
        write_text_atomic(
            page,
            templates.render_detail(
                case,
                store.documents.get(number, []),
                schema,
                counsel=store.counsel.get(number),
            ),
        )
        written.add(page)

    # IDS renumbers a docket once its complaint is instituted, and drops rows
    # it has withdrawn; pages for numbers no longer in the data would linger.
    for stale in detail_dir.glob("*.html"):
        if stale not in written:
            stale.unlink()

    unused = ui_schema.unused_sources(schema, cases.values())

    log(f"Rendered {len(cases)} investigation page(s) + index into {site_dir}.")
    if skipped:
        log(
            f"  ! {len(skipped)} record(s) predate the IDS rewrite and were left off the "
            "site; run 'python cli.py sync' to rebuild them."
        )
    if unused:
        log(
            f"  ! {schema_path.name} names field(s) no case has: {', '.join(unused)}. "
            "Run 'python cli.py fields' to see what is available."
        )
    return RenderReport(
        site_dir=site_dir,
        index_path=index_path,
        pages=len(cases),
        skipped=skipped,
        unused_sources=unused,
    )
