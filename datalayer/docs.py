"""Process 2 -- documents from EDIS, and nothing else.

You name one or more investigation numbers, this asks EDIS for their document
lists and (optionally) downloads the public PDFs. It writes
`data/documents_index.json`, `data/documents_state.json` and files under
`data/documents/` -- never `data/investigations.json`.

That boundary is deliberate: investigation information and parties come from
the IDS feed (see cases.py), which is a different source with different
spellings and a different idea of what a case is. Letting the API write case
fields as a side effect of fetching documents is how the two would start
overwriting each other, so this process simply has no way to do it.

For a complaint EDIS has no investigation record for yet, the document IDs
logged from the RSS feed are used instead, which is the only way to reach the
PDFs of a case that has not been instituted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import httpx

import dates

from .client import EdisAuthError, EdisClient, EdisError
from .runner import edis_session
from .store import Store, lookup_candidates

Logger = Callable[[str], None]

UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    return UNSAFE_FILENAME_RE.sub("_", name).strip("_") or "file"


@dataclass
class DocsResult:
    key: str
    document_count: int = 0
    downloaded: int = 0
    source: str = "edis"
    note: str | None = None
    ok: bool = True

    @classmethod
    def skipped(cls, key: str, note: str) -> "DocsResult":
        return cls(key=key, note=note, ok=False)


@dataclass
class DocsReport:
    requested: list[str] = field(default_factory=list)
    results: list[DocsResult] = field(default_factory=list)

    @property
    def fetched(self) -> list[DocsResult]:
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> list[DocsResult]:
        return [r for r in self.results if not r.ok]

    @property
    def downloaded(self) -> int:
        return sum(r.downloaded for r in self.results)


def download_document_attachments(
    client: EdisClient,
    docs_dir: Path,
    key: str,
    doc_id: str,
    security_level: str | None,
    log: Logger = print,
) -> tuple[list[dict[str, str]], int]:
    if security_level and security_level.lower() != "public":
        return [], 0

    try:
        attachments = client.list_attachments(doc_id)
    except EdisAuthError:
        raise
    except EdisError as exc:
        log(f"    ! could not list attachments for document {doc_id}: {exc}")
        return [], 0

    case_dir = docs_dir / key
    results: list[dict[str, str]] = []
    downloaded = 0

    for att in attachments:
        att_id = att.get("id")
        original_name = att.get("originalFileName") or f"{att_id}.pdf"
        local_name = f"{doc_id}_{att_id}_{safe_filename(str(original_name))}"
        local_path = case_dir / local_name

        if not local_path.exists():
            try:
                client.download_attachment(str(doc_id), str(att_id), local_path)
                downloaded += 1
            except EdisAuthError:
                raise
            except EdisError as exc:
                log(f"    ! could not download attachment {att_id} of document {doc_id}: {exc}")
                continue

        if local_path.exists():
            results.append(
                {
                    "href": f"../../data/documents/{key}/{local_name}",
                    "label": str(original_name),
                }
            )

    return results, downloaded


def fetch_document_rows(client: EdisClient, key: str) -> list[dict[str, Any]]:
    """EDIS wants the number in its own spelling, so try the forms one case
    number can take ("337-TA-1478", "337-1478", "1478") until one answers.
    """
    for candidate in lookup_candidates(key):
        try:
            rows = client.list_documents(candidate)
        except EdisAuthError:
            raise
        except EdisError:
            continue
        if rows:
            return rows
    return []


def _edis_documents(
    client: EdisClient,
    docs_dir: Path,
    key: str,
    rows: list[dict[str, Any]],
    *,
    download: bool,
    log: Logger,
) -> tuple[list[dict[str, Any]], int]:
    documents: list[dict[str, Any]] = []
    downloaded_total = 0

    for row in rows:
        doc_id = row.get("id")
        attachments: list[dict[str, str]] = []
        downloaded = 0
        if doc_id and download:
            attachments, downloaded = download_document_attachments(
                client, docs_dir, key, str(doc_id), row.get("securityLevel"), log
            )
        downloaded_total += downloaded
        documents.append(
            {
                "id": doc_id,
                "document_type": row.get("documentType"),
                "title": row.get("documentTitle"),
                "security_level": row.get("securityLevel"),
                "filed_by": row.get("filedBy"),
                "on_behalf_of": row.get("onBehalfOf"),
                "firm_organization": row.get("firmOrganization"),
                "document_date": dates.to_iso(row.get("documentDate")),
                "official_received_date": dates.to_iso(row.get("officialReceivedDate")),
                "attachments": attachments,
            }
        )

    return documents, downloaded_total


def _rss_documents(
    client: EdisClient,
    docs_dir: Path,
    key: str,
    logged: dict[str, Any],
    *,
    download: bool,
    log: Logger,
) -> tuple[list[dict[str, Any]], int]:
    documents: list[dict[str, Any]] = []
    downloaded_total = 0

    for doc_id, meta in sorted(logged.items()):
        attachments: list[dict[str, str]] = []
        downloaded = 0
        if download:
            attachments, downloaded = download_document_attachments(
                client, docs_dir, key, doc_id, "Public", log
            )
        downloaded_total += downloaded
        doc_type = meta.get("doc_type") or "Document"
        filed = dates.to_iso(meta.get("pub_date_iso") or meta.get("pub_date"))
        documents.append(
            {
                "id": doc_id,
                "document_type": doc_type,
                "title": doc_type,
                "security_level": None,
                "filed_by": None,
                "on_behalf_of": None,
                "firm_organization": None,
                "document_date": filed,
                "official_received_date": filed,
                "attachments": attachments,
            }
        )

    return documents, downloaded_total


def fetch_case(
    client: EdisClient,
    store: Store,
    key: str,
    *,
    download: bool = True,
    log: Logger = print,
) -> DocsResult:
    """Refresh one case's documents. Touches nothing else about the case."""
    rows = fetch_document_rows(client, key)

    if rows:
        documents, downloaded = _edis_documents(
            client, store.docs_dir, key, rows, download=download, log=log
        )
        source = "edis"
    else:
        logged = store.rss_documents(key)
        if not logged:
            return DocsResult.skipped(key, "EDIS lists no documents for this number")
        documents, downloaded = _rss_documents(
            client, store.docs_dir, key, logged, download=download, log=log
        )
        source = "rss"

    store.put_documents(key, documents, source=source, attachments_downloaded=downloaded)
    return DocsResult(
        key=key, document_count=len(documents), downloaded=downloaded, source=source
    )


def fetch_many(
    client: EdisClient,
    store: Store,
    keys: Iterable[str],
    *,
    download: bool = True,
    log: Logger = print,
) -> list[DocsResult]:
    """One case failing is not the run failing; a rejected token is."""
    results: list[DocsResult] = []
    for key in keys:
        log(f"{key}...")
        try:
            result = fetch_case(client, store, key, download=download, log=log)
        except EdisAuthError:
            raise
        except EdisError as exc:
            result = DocsResult.skipped(key, str(exc))
        except httpx.HTTPError as exc:
            result = DocsResult.skipped(key, f"network error: {exc}")

        if result.ok:
            log(
                f"  {result.document_count} document(s) from {result.source}, "
                f"{result.downloaded} new attachment(s) downloaded."
            )
        else:
            log(f"  ! skipped: {result.note}")
        results.append(result)
    return results


def resolve_targets(store: Store, numbers: Iterable[str]) -> tuple[list[str], list[str]]:
    """Map the numbers a user typed onto stored case keys.

    Returns the resolved targets plus the ones nothing on disk matches. An
    unknown number is still a valid target -- EDIS may answer for it -- so the
    caller decides whether to try it or refuse.
    """
    targets: list[str] = []
    unknown: list[str] = []
    seen: set[str] = set()

    for number in numbers:
        number = str(number).strip()
        if not number:
            continue
        key = store.find_key(number)
        if key is None:
            unknown.append(number)
        target = key or number
        if target not in seen:
            seen.add(target)
            targets.append(target)

    return targets, unknown


def run(
    store: Store,
    token: str,
    numbers: Iterable[str],
    *,
    download: bool = True,
    known_only: bool = False,
    log: Logger = print,
) -> DocsReport:
    targets, unknown = resolve_targets(store, numbers)
    report = DocsReport(requested=targets)

    if unknown:
        if known_only:
            log(f"  ! not tracked, skipping: {', '.join(unknown)}")
            targets = [t for t in targets if t not in set(unknown)]
            report.requested = targets
        else:
            log(f"  {len(unknown)} number(s) not tracked yet; asking EDIS anyway: {', '.join(unknown)}")

    if not targets:
        log("Nothing to fetch.")
        return report

    with edis_session(token) as client:
        report.results = fetch_many(client, store, targets, download=download, log=log)

    store.save_documents()
    store.record_run(
        "documents",
        numbers=targets[:50],
        fetched=len(report.fetched),
        attachments_downloaded=report.downloaded,
        with_attachments=download,
    )
    store.save_state()
    return report
