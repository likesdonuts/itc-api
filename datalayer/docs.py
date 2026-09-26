"""Process 2 -- documents from EDIS, and nothing else.

You name one or more investigation numbers, this asks EDIS for their document
lists and (optionally) downloads the public PDFs. It writes
`data/documents_index/` (one file per case), `data/documents_state.json` and files under
`data/documents/` -- never `data/investigations.json`.

That boundary is deliberate: investigation information and parties come from
the IDS feed (see cases.py), which is a different source with different
spellings and a different idea of what a case is. Letting the API write case
fields as a side effect of fetching documents is how the two would start
overwriting each other, so this process simply has no way to do it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import httpx

import dates

from .client import EdisAuthError, EdisClient, EdisError
from .runner import edis_session
from .store import Store, lookup_candidates

Logger = Callable[[str], None]

UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

# The filings whose PDFs list a party's whole legal team (see counsel.py).
# `docs --appearances` downloads only these, which is a few small files per
# case instead of every exhibit.
APPEARANCE_TYPES = frozenset({"Notice of Appearance"})


def safe_filename(name: str) -> str:
    return UNSAFE_FILENAME_RE.sub("_", name).strip("_") or "file"


@dataclass
class DocsResult:
    key: str
    document_count: int = 0
    downloaded: int = 0
    note: str | None = None
    ok: bool = True
    full: bool = True  # the whole docket was listed, not only its new filings

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


def fetch_document_rows(
    client: EdisClient, key: str, known_ids: set[str] | None = None
) -> tuple[list[dict[str, Any]], bool]:
    """EDIS wants the number in its own spelling, so try the forms one case
    number can take ("337-TA-1478", "337-1478", "1478") until one answers.

    With `known_ids`, only the new filings are read (see
    EdisClient.list_documents). Returns the rows and whether they are the
    whole docket.
    """
    for candidate in lookup_candidates(key):
        try:
            rows = client.list_documents(candidate, known_ids=known_ids) if known_ids else client.list_documents(candidate)
        except EdisAuthError:
            raise
        except EdisError:
            continue
        if rows:
            return rows, bool(getattr(client, "last_listing_complete", True)) or not known_ids
    return [], True


def _kept_attachments(
    docs_dir: Path, key: str, doc_id: Any, previous: list[dict[str, Any]] | None
) -> list[dict[str, str]]:
    """The document's PDFs already on disk, whether or not the index knew.

    A document that is not downloaded this run keeps the links it already
    had, so a metadata-only refresh does not orphan PDFs fetched earlier. Its
    files are also looked up by name ("<document id>_<attachment id>_...")
    to relink any whose links an older refresh did drop.
    """
    kept = [
        att
        for att in previous or []
        if att.get("href") and (docs_dir / key / Path(att["href"]).name).exists()
    ]
    case_dir = docs_dir / key
    if doc_id and case_dir.is_dir():
        linked = {Path(att["href"]).name for att in kept}
        for path in sorted(case_dir.glob(f"{doc_id}_*")):
            if path.name not in linked:
                kept.append(
                    {
                        "href": f"../../data/documents/{key}/{path.name}",
                        "label": path.name.split("_", 2)[-1],
                    }
                )
    return kept


def _edis_documents(
    client: EdisClient,
    docs_dir: Path,
    key: str,
    rows: list[dict[str, Any]],
    *,
    download: bool,
    only_types: frozenset[str] | None = None,
    only_ids: frozenset[str] | None = None,
    previous: list[dict[str, Any]] | None = None,
    log: Logger,
) -> tuple[list[dict[str, Any]], int]:
    documents: list[dict[str, Any]] = []
    downloaded_total = 0
    previous_attachments = {
        str(doc.get("id")): doc.get("attachments") for doc in previous or []
    }
    # Documents only some of whose files were downloaded (the case summary
    # fetches just the file it reads): a download still completes them.
    partial = {str(doc.get("id")) for doc in previous or [] if doc.get("attachments_partial")}

    for row in rows:
        doc_id = row.get("id")
        attachments: list[dict[str, str]] = []
        downloaded = 0
        wanted = (
            download
            and (only_types is None or row.get("documentType") in only_types)
            and (only_ids is None or str(doc_id) in only_ids)
        )
        kept = _kept_attachments(docs_dir, key, doc_id, previous_attachments.get(str(doc_id)))
        # A document whose PDFs are already on disk is not asked about again:
        # asking EDIS for its attachment list every day was a third of the
        # daily sync's requests, for files that were never going to change.
        is_partial = str(doc_id) in partial
        if doc_id and wanted and (not kept or is_partial):
            attachments, downloaded = download_document_attachments(
                client, docs_dir, key, str(doc_id), row.get("securityLevel"), log
            )
            if is_partial and not attachments:  # the listing failed: keep what is on disk
                attachments = kept
            else:
                is_partial = False
        else:
            attachments = kept
        downloaded_total += downloaded
        record = {
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
        if is_partial:
            record["attachments_partial"] = True
        documents.append(record)

    return documents, downloaded_total


def fetch_case(
    client: EdisClient,
    store: Store,
    key: str,
    *,
    download: bool = True,
    only_types: frozenset[str] | None = None,
    only_ids: frozenset[str] | None = None,
    full: bool = True,
    log: Logger = print,
) -> DocsResult:
    """Refresh one case's documents. Touches nothing else about the case.

    `full=False` reads only the new filings: EDIS lists a docket newest
    first, so the pages up to the first one made up entirely of documents
    already on file are enough, and the rest are kept as they were. A full
    listing (the default, and weekly in the daily sync) also picks up edits
    to older documents and removals.
    """
    previous = store.documents.get(key)
    known = {str(d.get("id")) for d in previous or [] if d.get("id")} if not full else None
    rows, complete = fetch_document_rows(client, key, known or None)
    if not rows:
        return DocsResult.skipped(key, "EDIS lists no documents for this number")

    documents, downloaded = _edis_documents(
        client,
        store.docs_dir,
        key,
        rows,
        download=download,
        only_types=only_types,
        only_ids=only_ids,
        previous=previous,
        log=log,
    )
    if not complete:
        listed = {str(d.get("id")) for d in documents}
        documents += [d for d in previous or [] if str(d.get("id")) not in listed]
    last_full = (
        datetime.now(timezone.utc).isoformat(timespec="seconds") if complete
        else (store.documents_state.get(key) or {}).get("full_listed_at")
    )
    store.put_documents(
        key, documents, attachments_downloaded=downloaded, full_listed_at=last_full,
        listing="full" if complete else "new only",
    )
    return DocsResult(key=key, document_count=len(documents), downloaded=downloaded, full=complete)


# Reading only new filings misses edits to older documents (an attachment
# added, a document made public) and removals; a full listing this often
# catches them.
FULL_RELIST_DAYS = 7


def _full_listing_due(store: Store, key: str) -> bool:
    if not store.documents.get(key):
        return True
    state = store.documents_state.get(key) or {}
    # Before new-filings-only reading existed every listing was a full one,
    # so a case with no record of either was last listed in full when fetched.
    last = str(state.get("full_listed_at") or ("" if state.get("listing") else state.get("fetched_at")) or "")
    if not last:
        return True
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - when >= timedelta(days=FULL_RELIST_DAYS)


def fetch_many(
    client: EdisClient,
    store: Store,
    keys: Iterable[str],
    *,
    download: bool = True,
    only_types: frozenset[str] | None = None,
    only_ids: frozenset[str] | None = None,
    new_only: bool = False,
    log: Logger = print,
) -> list[DocsResult]:
    """One case failing is not the run failing; a rejected token is.

    `new_only` reads only each case's new filings, except for a case whose
    last full listing is FULL_RELIST_DAYS old (or that has none), which is
    listed in full.
    """
    results: list[DocsResult] = []
    for key in keys:
        log(f"{key}...")
        try:
            result = fetch_case(
                client, store, key, download=download, only_types=only_types, only_ids=only_ids,
                full=not new_only or _full_listing_due(store, key), log=log,
            )
        except EdisAuthError:
            raise
        except EdisError as exc:
            result = DocsResult.skipped(key, str(exc))
        except httpx.HTTPError as exc:
            result = DocsResult.skipped(key, f"network error: {exc}")

        if result.ok:
            listed = "listed" if result.full else "on file (new filings read)"
            log(
                f"  {result.document_count} document(s) {listed}, "
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
    only_types: frozenset[str] | None = None,
    only_ids: frozenset[str] | None = None,
    known_only: bool = False,
    by_hand: bool = True,
    new_only: bool = False,
    log: Logger = print,
) -> DocsReport:
    """`only_types` limits downloads to those document types and `only_ids`
    to those documents (a claims analysis's sources); every document is
    still listed.

    A fetch you ask for (`by_hand`) makes a backfilled case one of yours; a
    routine refresh (the daily sync) leaves it marked as backfilled, so it
    drops out of the daily sync once it closes (see backfill.py).
    """
    targets, unknown = resolve_targets(store, numbers)
    backfilled = {k for k in targets if (store.documents_state.get(k) or {}).get("backfill")}
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
        report.results = fetch_many(
            client, store, targets, download=download, only_types=only_types, only_ids=only_ids,
            new_only=new_only, log=log,
        )
    if not by_hand:
        for key in backfilled:
            if key in store.documents_state:
                store.documents_state[key]["backfill"] = True

    store.save_documents()
    store.record_run(
        "documents",
        numbers=targets[:50],
        fetched=len(report.fetched),
        attachments_downloaded=report.downloaded,
        with_attachments=download,
        attachment_types=sorted(only_types) if only_types else None,
    )
    store.save_state()
    return report
