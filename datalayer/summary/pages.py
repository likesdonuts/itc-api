"""Page counts, per attachment, for the cost preview.

EDIS's attachment list gives each file's page count (one request per
document, no download); a document whose PDFs are all on disk is counted
from them instead. Either way the counts are kept in data/summaries/pages.json,
so each document is counted once:

    {"797350": {"attachments": [{"id": "2003047", "pages": 2}, ...],
                "from": "edis", "at": "2026-09-26T..."}}

Attachments are listed in id order -- the order they were filed in, which
puts a complaint's cover letters first, then the complaint, then its exhibits.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from ..store import Store, load_json, save_json

Logger = Callable[[str], None]

PAGES_FILE = Path("summaries") / "pages.json"


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / PAGES_FILE


def load(data_dir: Path) -> dict[str, Any]:
    return load_json(_path(data_dir), {}) or {}


def _sort(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(attachments, key=lambda a: (len(str(a["id"])), str(a["id"])))


def _attachment_id(pdf: Path) -> str:
    # "<document id>_<attachment id>_<original name>.pdf"
    parts = pdf.stem.split("_")
    return parts[1] if len(parts) > 1 else pdf.stem


def from_disk(docs_dir: Path, document: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Counts from the PDFs on disk, if every attachment the index lists is
    there; None otherwise (a partial download would undercount)."""
    listed = [Path(a["href"]).name for a in document.get("attachments") or [] if a.get("href")]
    if not listed or not all((Path(docs_dir) / name).exists() for name in listed):
        return None
    from pypdf import PdfReader

    counted = []
    for name in listed:
        path = Path(docs_dir) / name
        try:
            pages = len(PdfReader(str(path)).pages)
        except Exception:  # an unreadable PDF: let EDIS count it
            return None
        counted.append({"id": _attachment_id(path), "pages": pages})
    return _sort(counted)


def from_edis(client: Any, doc_id: str) -> list[dict[str, Any]]:
    counted = []
    for att in client.list_attachments(str(doc_id)):
        try:
            pages = int(att.get("pageCount") or 0)
        except (TypeError, ValueError):
            pages = 0
        counted.append({"id": str(att.get("id")), "pages": pages})
    return _sort(counted)


def fill(
    store: Store,
    key: str,
    doc_ids: Iterable[str],
    *,
    client: Any = None,
    log: Logger = print,
) -> int:
    """Count the pages of the documents not counted yet: from disk where
    they are all there, otherwise from EDIS when a client is given. Returns
    how many documents were counted."""
    cache = load(store.data_dir)
    documents = {str(d.get("id")): d for d in store.documents.get(key) or []}
    docs_dir = store.docs_dir / key
    counted = 0
    for doc_id in doc_ids:
        doc_id = str(doc_id)
        if doc_id in cache:
            continue
        attachments = from_disk(docs_dir, documents.get(doc_id) or {})
        source = "disk"
        if attachments is None:
            if client is None:
                continue
            try:
                attachments = from_edis(client, doc_id)
            except Exception as exc:  # one document should not stop the rest
                log(f"    ! could not count the pages of document {doc_id}: {exc}")
                continue
            source = "edis"
        cache[doc_id] = {
            "attachments": attachments,
            "from": source,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        counted += 1
    if counted:
        save_json(_path(store.data_dir), cache)
    return counted
