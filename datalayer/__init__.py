"""Data layer: everything that talks to EDIS/RSS and owns the files in data/.

Two processes, deliberately kept separate:

* `discovery` -- RSS feed + EDIS API, finds and fetches cases we don't track yet.
* `update`    -- EDIS API only, re-pulls the specific case numbers you name.

Nothing in here renders HTML; the UI layer reads the JSON these processes write.
"""

from __future__ import annotations

from . import discovery, records, runner, update
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
    "discovery",
    "load_token",
    "records",
    "runner",
    "update",
]
