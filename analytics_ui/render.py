"""Write the analytics app: site_analytics/index.html and data.js.

data.js assigns the bundle to window.ANALYTICS rather than being fetched as
JSON, so the page still reads (without its Rebuild button) when opened
straight from disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from datalayer.config import DATA_DIR
from datalayer.store import write_text_atomic

from . import bundle, page

Logger = Callable[[str], None]

ROOT = Path(__file__).resolve().parents[1]
SITE_DIR = ROOT / "site_analytics"


def render(data_dir: Path = DATA_DIR, site_dir: Path = SITE_DIR, *, log: Logger = print) -> Path:
    data = bundle.build(Path(data_dir))
    site_dir = Path(site_dir)
    write_text_atomic(site_dir / "data.js", "window.ANALYTICS = " + json.dumps(data, separators=(",", ":")) + ";\n")
    write_text_atomic(site_dir / "index.html", page.html())
    log(
        f"Analytics app: {len(data['firms'])} firms, {len(data['attorneys'])} attorneys, "
        f"{len(data['companies'])} companies -> {site_dir / 'index.html'}"
    )
    return site_dir / "index.html"
