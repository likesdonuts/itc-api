"""Check the 337-complaint RSS feed, log what it reports (docket number, doc ID,
collection timestamp), then use those docket numbers to pull investigation and
document data from EDIS and regenerate the static docket site under site/.

Usage:
    python refresh.py
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from edis_client import (
    EdisAuthError,
    EdisClient,
    EdisError,
    fetch_ids_investigations,
    fetch_rss,
    load_env,
)
import templates

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DOCS_DIR = DATA_DIR / "documents"
SITE_DIR = ROOT / "site"
RSS_LOG_PATH = DATA_DIR / "rss_log.json"
INVESTIGATIONS_PATH = DATA_DIR / "investigations.json"
DOCUMENTS_INDEX_PATH = DATA_DIR / "documents_index.json"

RSS_URL = "https://edis.usitc.gov/external/rss/render.rss?criteria=CRITERIONAOIDEL:8:13:CRITERIONANOTIFY:true"

UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    return UNSAFE_FILENAME_RE.sub("_", name).strip("_") or "file"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def update_rss_log(rss_log: dict[str, Any], items: list[Any]) -> tuple[dict[str, Any], int]:
    """Fold newly fetched RSS items into the persistent per-docket log.

    Returns the updated log and a count of previously-unseen (docket, doc_id)
    pairs, so the caller can report what's new this run.
    """
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    skipped = 0

    for item in items:
        if not item.docket_number:
            skipped += 1
            print(f"  ! could not extract a docket number from RSS item: {item.title!r}")
            continue

        docket_entry = rss_log.setdefault(
            item.docket_number,
            {"first_collected_at": now, "documents": {}},
        )
        docket_entry["last_collected_at"] = now

        if not item.doc_id:
            continue

        if item.doc_id not in docket_entry["documents"]:
            new_count += 1
        docket_entry["documents"][item.doc_id] = {
            "doc_type": item.doc_type,
            "pub_date": item.pub_date,
            "pub_date_iso": item.pub_date_iso,
            "collected_at": docket_entry["documents"].get(item.doc_id, {}).get("collected_at", now),
            "link": item.link,
            "guid": item.guid,
        }

    if skipped:
        print(f"  {skipped} RSS item(s) had no recognizable docket number.")
    return rss_log, new_count


def try_get_investigation(client: EdisClient, docket_number: str) -> list[dict[str, Any]]:
    """EDIS's /investigation lookup expects a formal 337-TA-#### number once a
    complaint is instituted. Pre-institution, the RSS feed only gives us the
    raw docket number (e.g. "337-3936"), which may or may not resolve. Try the
    docket number as-is, then fall back to its bare numeric suffix.
    """
    rows = client.get_investigation(docket_number)
    if rows:
        return rows
    suffix = docket_number.rsplit("-", 1)[-1]
    if suffix != docket_number:
        try:
            return client.get_investigation(suffix)
        except EdisError:
            return []
    return []


def download_document_attachments(
    client: EdisClient,
    investigation_key: str,
    doc_id: str,
    security_level: str | None,
) -> tuple[list[dict[str, str]], int]:
    if security_level and security_level.lower() != "public":
        return [], 0

    try:
        attachments = client.list_attachments(doc_id)
    except EdisError as exc:
        print(f"    ! could not list attachments for document {doc_id}: {exc}")
        return [], 0

    inv_doc_dir = DOCS_DIR / investigation_key
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
            except EdisError as exc:
                print(f"    ! could not download attachment {att_id} of document {doc_id}: {exc}")
                continue

        if local_path.exists():
            rel_href = f"../../data/documents/{investigation_key}/{local_name}"
            results.append({"href": rel_href, "label": str(original_name)})

    return results, downloaded


def build_instituted_record(
    client: EdisClient,
    edis_rows: list[dict[str, Any]],
    ids_lookup: dict[str, dict[str, Any]],
    fallback_date_initiated: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    edis_row = edis_rows[0]
    investigation_number = edis_row.get("investigationNumber") or edis_row.get("docketNumber")
    ids_meta = ids_lookup.get(investigation_number, {})

    def pick(*values: Any) -> Any:
        for v in values:
            if v:
                return v
        return None

    record = {
        "investigation_number": investigation_number,
        "title": pick(edis_row.get("investigationTitle"), ids_meta.get("Full Title")),
        "docket_number": edis_row.get("docketNumber"),
        "investigation_type": pick(edis_row.get("investigationType"), ids_meta.get("Investigation Type")),
        "investigation_status": pick(edis_row.get("investigationStatus"), ids_meta.get("Investigation Status")),
        "investigation_phase": pick(edis_row.get("investigationPhase"), ids_meta.get("Phase Number")),
        "date_initiated": ids_meta.get("Start Date") or fallback_date_initiated,
        "last_refreshed": datetime.now(timezone.utc).isoformat(),
    }

    raw_documents = client.list_documents(investigation_number)
    documents: list[dict[str, Any]] = []
    downloaded_total = 0

    if not record["date_initiated"]:
        complaint_dates = [
            d.get("officialReceivedDate") or d.get("documentDate")
            for d in raw_documents
            if "complaint" in (d.get("documentType") or "").lower()
        ]
        complaint_dates = [d for d in complaint_dates if d]
        if complaint_dates:
            record["date_initiated"] = min(complaint_dates) + "  (approx., from earliest complaint filing)"

    for doc in raw_documents:
        doc_id = doc.get("id")
        attachments, downloaded = ([], 0)
        if doc_id:
            attachments, downloaded = download_document_attachments(
                client, investigation_number, str(doc_id), doc.get("securityLevel")
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

    return record, documents, downloaded_total


def build_pending_record(
    client: EdisClient,
    docket_number: str,
    rss_documents: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """No /investigation record yet -- this complaint hasn't been instituted.
    Everything we know comes from the RSS feed plus direct attachment lookups
    keyed by the document IDs the feed gave us.
    """
    pub_dates = [d.get("pub_date_iso") or d.get("pub_date") for d in rss_documents.values()]
    pub_dates = [d for d in pub_dates if d]
    date_initiated = min(pub_dates) if pub_dates else None

    record = {
        "investigation_number": docket_number,
        "title": f"Complaint {docket_number} (not yet instituted)",
        "docket_number": docket_number,
        "investigation_type": "Sec 337",
        "investigation_status": "Pending Institution",
        "investigation_phase": "Pre-Institution",
        "date_initiated": date_initiated,
        "last_refreshed": datetime.now(timezone.utc).isoformat(),
    }

    documents: list[dict[str, Any]] = []
    downloaded_total = 0
    for doc_id, meta in sorted(rss_documents.items()):
        attachments, downloaded = download_document_attachments(client, docket_number, doc_id, "Public")
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

    return record, documents, downloaded_total


def main() -> int:
    env = load_env(ROOT / ".env")
    token = env.get("EDIS_TOKEN")
    if not token:
        print("Missing EDIS_TOKEN. Put it in a .env file next to refresh.py:\n  EDIS_TOKEN=<your token>")
        return 1

    all_investigations: dict[str, Any] = load_json(INVESTIGATIONS_PATH, {})
    documents_by_investigation: dict[str, list[dict[str, Any]]] = load_json(DOCUMENTS_INDEX_PATH, {})
    rss_log: dict[str, Any] = load_json(RSS_LOG_PATH, {})

    print("Checking RSS feed for 337 complaint filings...")
    try:
        rss_items = fetch_rss(RSS_URL)
        rss_log, new_count = update_rss_log(rss_log, rss_items)
        save_json(RSS_LOG_PATH, rss_log)
        print(f"  {len(rss_items)} item(s) in feed, {new_count} new document(s) logged.")
    except httpx.HTTPError as exc:
        print(f"  ! RSS feed fetch failed ({exc}); continuing with previously logged dockets only.")

    print("Fetching public IDS metadata (investigation start dates, etc.)...")
    try:
        ids_lookup = fetch_ids_investigations()
    except httpx.HTTPError as exc:
        print(f"  ! IDS feed fetch failed ({exc}); 'date initiated' may be unavailable for new cases.")
        ids_lookup = {}

    docket_numbers = set(rss_log.keys()) | set(all_investigations.keys())
    if not docket_numbers:
        print("Nothing to refresh yet (empty RSS log, no investigations tracked previously).")

    total_downloaded = 0
    with EdisClient(token) as client:
        for docket_number in sorted(docket_numbers):
            print(f"Refreshing {docket_number}...")
            try:
                edis_rows = try_get_investigation(client, docket_number)
            except EdisAuthError as exc:
                print(f"AUTH ERROR: {exc}")
                return 1
            except EdisError as exc:
                print(f"  ! could not look up {docket_number}: {exc}")
                continue

            rss_documents = rss_log.get(docket_number, {}).get("documents", {})

            try:
                if edis_rows:
                    record, documents, downloaded = build_instituted_record(
                        client, edis_rows, ids_lookup, None
                    )
                    key = record["investigation_number"]
                elif rss_documents:
                    record, documents, downloaded = build_pending_record(
                        client, docket_number, rss_documents
                    )
                    key = docket_number
                else:
                    print("  ! no EDIS record and no RSS document history; skipping.")
                    continue
            except EdisAuthError as exc:
                print(f"AUTH ERROR: {exc}")
                return 1
            except EdisError as exc:
                print(f"  ! skipping {docket_number}: {exc}")
                continue

            all_investigations[key] = record
            documents_by_investigation[key] = documents
            total_downloaded += downloaded
            print(f"  status={record['investigation_status']!r}, {len(documents)} document(s), {downloaded} new attachment(s) downloaded.")

    save_json(INVESTIGATIONS_PATH, all_investigations)
    save_json(DOCUMENTS_INDEX_PATH, documents_by_investigation)

    print("Rendering site...")
    SITE_DIR.mkdir(exist_ok=True)
    (SITE_DIR / "investigations").mkdir(exist_ok=True)

    (SITE_DIR / "index.html").write_text(
        templates.render_index(list(all_investigations.values())), encoding="utf-8"
    )
    for number, record in all_investigations.items():
        slug = templates.slug_for(number)
        docs = documents_by_investigation.get(number, [])
        (SITE_DIR / "investigations" / f"{slug}.html").write_text(
            templates.render_detail(record, docs), encoding="utf-8"
        )

    print(f"\nDone. {len(all_investigations)} investigation(s) tracked, {total_downloaded} attachment(s) downloaded this run.")
    print(f"Open: {SITE_DIR / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
