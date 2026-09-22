"""Data layer: everything that talks to IDS/EDIS/RSS and owns the files in data/.

Two processes, deliberately kept separate, over two different sources:

* `ingest` -- the daily IDS snapshot. Owns every case record: numbers, titles,
  stages, dates, parties. Needs no token.
* `docs`   -- the EDIS API. Owns document lists and downloaded PDFs, and only
  those; it cannot change a case record.

`feed` reads the complaint RSS feed, which supplies document IDs for cases
EDIS has no investigation record for yet.

Nothing in here renders HTML; the UI layer reads the JSON these processes write.
"""

from __future__ import annotations

from . import cases, docs, feed, flatten, ids, ingest, normalize, runner
from .client import EdisAuthError, EdisClient, EdisError
from .config import MissingTokenError, load_token
from .runner import ProcessAborted
from .store import Store

__all__ = [
    "EdisAuthError",
    "EdisClient",
    "EdisError",
    "MissingTokenError",
    "ProcessAborted",
    "Store",
    "cases",
    "docs",
    "feed",
    "flatten",
    "ids",
    "ingest",
    "load_token",
    "normalize",
    "runner",
]
