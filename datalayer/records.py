"""Turn EDIS/RSS payloads into the stored record shape.

Both data-layer processes (new case discovery and targeted update) come
through `sync_case`, so a case looks the same on disk no matter which
process wrote it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .client import EdisAuthError, EdisClient, EdisError
from .store import Store, lookup_candidates

Logger = Callable[[str], None]

UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    return UNSAFE_FILENAME_RE.sub("_", name).strip("_") or "file"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SyncResult:
    key: str
    status: str
    document_count: int = 0
    downloaded: int = 0
    created: bool = False
    renamed_from: str | None = None
    note: str | None = None
    ok: bool = True

    @classmethod
    def skipped(cls, key: str, note: str) -> "SyncResult":
        return cls(key=key, status="skipped", note=note, ok=False)


def fetch_investigation_rows(client: EdisClient, number: str) -> list[dict[str, Any]]:
    """EDIS's /investigation lookup only answers for instituted cases, and it
    wants the number in its own spelling. Try the forms a user might give us
    ("337-TA-1478", "337-1478", "1478") until one resolves; an empty result
    means the complaint has not been instituted yet.
    """
    for candidate in lookup_candidates(number):
        try:
            rows = client.get_investigation(candidate)
        except EdisAuthError:
            raise
        except EdisError:
            continue
        if rows:
            return rows
    return []


def download_document_attachments(
    client: EdisClient,
    docs_dir: Path,
    investigation_key: str,
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

    inv_doc_dir = docs_dir / investigation_key
    results: list[dict[str, str]] = []
    downloaded = 0

    for att in attachments:
        att_id = att.get("id")
        original_name = att.get("originalFileName") or f"{att_id}.pdf"
        local_name = f"{doc_id}_{att_id}_{safe_filename(str(original_name))}"
        local_path = inv_doc_dir / local_name

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
            rel_href = f"../../data/documents/{investigation_key}/{local_name}"
            results.append({"href": rel_href, "label": str(original_name)})

    return results, downloaded


def _pick(*values: Any) -> Any:
    for value in values:
        if value:
            return value
    return None


def instituted_record(edis_row: dict[str, Any], ids_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "investigation_number": edis_row.get("investigationNumber") or edis_row.get("docketNumber"),
        "title": _pick(edis_row.get("investigationTitle"), ids_meta.get("Full Title")),
        "docket_number": edis_row.get("docketNumber"),
        "investigation_type": _pick(edis_row.get("investigationType"), ids_meta.get("Investigation Type")),
        "investigation_status": _pick(edis_row.get("investigationStatus"), ids_meta.get("Investigation Status")),
        "investigation_phase": _pick(edis_row.get("investigationPhase"), ids_meta.get("Phase Number")),
        "date_initiated": ids_meta.get("Start Date"),
        "last_refreshed": _now(),
    }


def pending_record(docket_number: str, rss_documents: dict[str, Any]) -> dict[str, Any]:
    """No /investigation record yet -- this complaint hasn't been instituted.
    Everything we know comes from the RSS feed plus direct attachment lookups
    keyed by the document IDs the feed gave us.
    """
    pub_dates = [d.get("pub_date_iso") or d.get("pub_date") for d in rss_documents.values()]
    pub_dates = [d for d in pub_dates if d]
    return {
        "investigation_number": docket_number,
        "title": f"Complaint {docket_number} (not yet instituted)",
        "docket_number": docket_number,
        "investigation_type": "Sec 337",
        "investigation_status": "Pending Institution",
        "investigation_phase": "Pre-Institution",
        "date_initiated": min(pub_dates) if pub_dates else None,
        "last_refreshed": _now(),
    }


def collect_edis_documents(
    client: EdisClient,
    docs_dir: Path,
    key: str,
    raw_documents: list[dict[str, Any]],
    *,
    download: bool = True,
    log: Logger = print,
) -> tuple[list[dict[str, Any]], int]:
    documents: list[dict[str, Any]] = []
    downloaded_total = 0

    for doc in raw_documents:
        doc_id = doc.get("id")
        attachments: list[dict[str, str]] = []
        downloaded = 0
        if doc_id and download:
            attachments, downloaded = download_document_attachments(
                client, docs_dir, key, str(doc_id), doc.get("securityLevel"), log
            )
        downloaded_total += downloaded
        documents.append(
            {
                "id": doc_id,
                "document_type": doc.get("documentType"),
                "title": doc.get("documentTitle"),
                "security_level": doc.get("securityLevel"),
                "filed_by": doc.get("filedBy"),
                "on_behalf_of": doc.get("onBehalfOf"),
                "firm_organization": doc.get("firmOrganization"),
                "document_date": doc.get("documentDate"),
                "official_received_date": doc.get("officialReceivedDate"),
                "attachments": attachments,
            }
        )

    return documents, downloaded_total


def collect_rss_documents(
    client: EdisClient,
    docs_dir: Path,
    key: str,
    rss_documents: dict[str, Any],
    *,
    download: bool = True,
    log: Logger = print,
) -> tuple[list[dict[str, Any]], int]:
    documents: list[dict[str, Any]] = []
    downloaded_total = 0

    for doc_id, meta in sorted(rss_documents.items()):
        attachments: list[dict[str, str]] = []
        downloaded = 0
        if download:
            attachments, downloaded = download_document_attachments(
                client, docs_dir, key, doc_id, "Public", log
            )
        downloaded_total += downloaded
        doc_type = meta.get("doc_type") or "Document"
        documents.append(
            {
                "id": doc_id,
                "document_type": doc_type,
                "title": doc_type,
                "security_level": None,
                "filed_by": None,
                "on_behalf_of": None,
                "firm_organization": None,
                "document_date": meta.get("pub_date_iso") or meta.get("pub_date"),
                "official_received_date": meta.get("pub_date_iso") or meta.get("pub_date"),
                "attachments": attachments,
            }
        )

    return documents, downloaded_total


def _earliest_complaint_date(raw_documents: list[dict[str, Any]]) -> str | None:
    dates = [
        d.get("officialReceivedDate") or d.get("documentDate")
        for d in raw_documents
        if "complaint" in (d.get("documentType") or "").lower()
    ]
    dates = [d for d in dates if d]
    return min(dates) if dates else None


def sync_case(
    client: EdisClient,
    store: Store,
    number: str,
    *,
    ids_lookup: dict[str, dict[str, Any]] | None = None,
    download: bool = True,
    log: Logger = print,
) -> SyncResult:
    """Pull one case from EDIS and write it into the store.

    Falls back to the RSS-derived pre-institution view when EDIS has no
    /investigation record for the number yet.
    """
    ids_lookup = ids_lookup or {}
    existing_key = store.find_key(number)
    lookup_number = existing_key or number

    try:
        edis_rows = fetch_investigation_rows(client, lookup_number)
    except EdisAuthError:
        raise
    except EdisError as exc:
        return SyncResult.skipped(lookup_number, f"lookup failed: {exc}")

    if edis_rows:
        edis_row = edis_rows[0]
        key = edis_row.get("investigationNumber") or edis_row.get("docketNumber") or lookup_number
        renamed_from = existing_key if existing_key and existing_key != key else None
        if renamed_from:
            store.rename(renamed_from, key)

        record = instituted_record(edis_row, ids_lookup.get(key, {}))
        raw_documents = client.list_documents(key)
        if not record["date_initiated"]:
            approx = _earliest_complaint_date(raw_documents)
            if approx:
                record["date_initiated"] = f"{approx}  (approx., from earliest complaint filing)"
        documents, downloaded = collect_edis_documents(
            client, store.docs_dir, key, raw_documents, download=download, log=log
        )
    else:
        key = existing_key or number
        rss_documents = store.rss_documents(key)
        if not rss_documents:
            return SyncResult.skipped(key, "no EDIS record and no RSS document history")
        renamed_from = None
        record = pending_record(key, rss_documents)
        documents, downloaded = collect_rss_documents(
            client, store.docs_dir, key, rss_documents, download=download, log=log
        )

    created = key not in store.investigations
    store.put(key, record, documents)

    return SyncResult(
        key=key,
        status=record.get("investigation_status") or "Unknown",
        document_count=len(documents),
        downloaded=downloaded,
        created=created,
        renamed_from=renamed_from,
    )
