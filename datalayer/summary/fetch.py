"""The one file of each document that a summary reads, downloaded alone.

A complaint filing is dozens of files -- cover letters, the complaint,
exhibits -- and only the complaint is read, so only it is downloaded. Which
file that is: in filing order, the first of `complaint_body_min_pages` pages
or more whose first page reads as a complaint (claims/candidates.
is_complaint_body); at most three are tried. An answer likewise: the first
file whose first pages say it responds to the complaint -- its exhibits
(prior art, even a whole thesis) can be longer than it. For any other
document, its longest file.

A document some of whose files are left undownloaded is marked
"attachments_partial" in the documents index, so "Fetch documents" still
completes it (docs.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..claims.candidates import is_complaint_body
from ..store import Store
from . import pages as page_counts
from . import text as page_text
from .config import SummaryConfig

Logger = Callable[[str], None]

MAX_COMPLAINT_TRIES = 3


@dataclass
class MainFile:
    path: Path
    attachment_id: str
    pages: int
    how: str = ""  # a complaint's body: "confirmed" (its first page) or "assumed"


def _on_disk(docs_dir: Path, doc_id: str, att_id: str) -> Path | None:
    found = sorted(Path(docs_dir).glob(f"{doc_id}_{att_id}_*"))
    return found[0] if found else None


def _download(store: Store, key: str, doc_id: str, att_id: str, client: Any, log: Logger) -> Path | None:
    docs_dir = store.docs_dir / key
    path = _on_disk(docs_dir, doc_id, att_id)
    if path is not None:
        return path
    if client is None:
        return None
    path = docs_dir / f"{doc_id}_{att_id}_{att_id}.pdf"  # EDIS gives attachments no other name
    try:
        client.download_attachment(doc_id, att_id, path)
    except Exception as exc:  # one file should not stop the summary
        log(f"    ! could not download attachment {att_id} of document {doc_id}: {exc}")
        return None
    _record(store, key, doc_id, path)
    return path


def _record(store: Store, key: str, doc_id: str, path: Path) -> None:
    """Link the new file in the documents index, and mark the document as
    only partly downloaded unless every one of its files is now on disk."""
    counted = (page_counts.load(store.data_dir).get(doc_id) or {}).get("attachments") or []
    docs_dir = store.docs_dir / key
    for doc in store.documents.get(key) or []:
        if str(doc.get("id")) != doc_id:
            continue
        attachments = doc.setdefault("attachments", [])
        href = f"../../data/documents/{key}/{path.name}"
        if all(a.get("href") != href for a in attachments):
            attachments.append({"href": href, "label": path.name.split("_", 2)[-1]})
        complete = counted and all(_on_disk(docs_dir, doc_id, a["id"]) for a in counted)
        if complete:
            doc.pop("attachments_partial", None)
        else:
            doc["attachments_partial"] = True
    store.save_documents()


def main_file(
    store: Store,
    key: str,
    doc_id: str,
    kind: str,
    cfg: SummaryConfig,
    *,
    client: Any = None,
    log: Logger = print,
) -> MainFile | None:
    """The file of this document that is read, on disk; None if it cannot be
    had (not counted and no EDIS client, or the downloads failed)."""
    doc_id = str(doc_id)
    counts = page_counts.load(store.data_dir)
    if doc_id not in counts:
        page_counts.fill(store, key, [doc_id], client=client, log=log)
        counts = page_counts.load(store.data_dir)
    attachments = (counts.get(doc_id) or {}).get("attachments") or []
    if not attachments:
        return None

    if kind not in BODY_TESTS:
        main = max(attachments, key=lambda a: a["pages"])
        path = _download(store, key, doc_id, main["id"], client, log)
        return MainFile(path, main["id"], main["pages"]) if path else None

    # A complaint or an answer comes with exhibits, some longer than itself
    # (prior art, a thesis): in filing order, the first file whose first
    # pages read as one.
    min_pages = cfg.complaint_body_min_pages if kind == "complaint" else 3
    candidates = [a for a in attachments if a["pages"] >= min_pages] or attachments
    first_found: MainFile | None = None
    for att in candidates[:MAX_COMPLAINT_TRIES]:
        path = _download(store, key, doc_id, att["id"], client, log)
        if path is None:
            continue
        found = MainFile(path, att["id"], att["pages"], "assumed")
        first_found = first_found or found
        head = page_text.pages(path, [1, 2], data_dir=store.data_dir, log=log)
        if BODY_TESTS[kind]("\n".join(head[n] for n in sorted(head))):
            found.how = "confirmed"
            return found
    return first_found


_ANSWER_WORDS = re.compile(r"\b(?:response|answer)\b", re.I)
_ANSWER_TO = re.compile(r"complaint|notice\s+of\s+investigation", re.I)
_EXHIBIT_START = re.compile(r"^\s*(?:exhibit|appendix|attachment)\b", re.I)


def is_answer_body(head: str) -> bool:
    """The answer itself: its first pages say it responds to the complaint."""
    head = head[:4000]
    return bool(_ANSWER_WORDS.search(head) and _ANSWER_TO.search(head) and not _EXHIBIT_START.match(head))


BODY_TESTS: dict[str, Callable[[str], bool]] = {"complaint": is_complaint_body, "answer": is_answer_body}
