"""Shared plumbing for the data-layer processes.

There is only one thing both sides of the app need in common: a way to open
an authenticated EDIS session that stops the whole run when the token is
rejected, rather than reporting the same auth failure once per case.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator

from .client import EdisAuthError, EdisClient

Logger = Callable[[str], None]


class ProcessAborted(RuntimeError):
    """Raised when the run cannot usefully continue (e.g. a rejected token)."""


@contextmanager
def edis_session(token: str) -> Iterator[EdisClient]:
    with EdisClient(token) as client:
        try:
            yield client
        except EdisAuthError as exc:
            raise ProcessAborted(str(exc)) from exc
