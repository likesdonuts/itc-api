"""UI layer: build the static site from whatever is already in data/.

This never touches the network and never needs an EDIS token, so iterating
on the HTML/CSS in templates.py is just a re-render of the stored data.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from datalayer.config import DATA_DIR, SITE_DIR
from datalayer.store import Store

from . import templates

Logger = Callable[[str], None]


@dataclass
class RenderReport:
    site_dir: Path
    index_path: Path
    pages: int


def render_site(
    store: Store | None = None,
    *,
    data_dir: Path = DATA_DIR,
    site_dir: Path = SITE_DIR,
    log: Logger = print,
) -> RenderReport:
    store = store or Store.load(data_dir)
    site_dir = Path(site_dir)
    detail_dir = site_dir / "investigations"
    detail_dir.mkdir(parents=True, exist_ok=True)

    investigations: dict[str, Any] = store.investigations
    index_path = site_dir / "index.html"
    index_path.write_text(
        templates.render_index(list(investigations.values())), encoding="utf-8"
    )

    written: set[Path] = set()
    for number, record in investigations.items():
        slug = templates.slug_for(number)
        documents = store.documents.get(number, [])
        page = detail_dir / f"{slug}.html"
        page.write_text(templates.render_detail(record, documents), encoding="utf-8")
        written.add(page)

    # A pre-institution docket gets renumbered once it's instituted; drop the
    # page left behind under the old number so the site matches the data.
    for stale in detail_dir.glob("*.html"):
        if stale not in written:
            stale.unlink()
            log(f"  removed stale page {stale.name}")

    log(f"Rendered {len(investigations)} investigation page(s) + index into {site_dir}.")
    return RenderReport(site_dir=site_dir, index_path=index_path, pages=len(investigations))
