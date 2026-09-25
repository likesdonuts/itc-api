"""Persistence for everything the data layer collects.

The JSON files under data/ are the contract between the two layers: data
processes write them, the UI layer only ever reads them. Keeping all the
file handling here means neither side has to know about paths or formats.

Each file has exactly one writer, which is what keeps the two data processes
from treading on each other:

    investigations.json   the IDS ingest (cases, stages, parties)
    documents_index/      the EDIS documents process: one <case>.json per
                          case, each that case's document list
    documents_state.json  the EDIS documents process (its own bookkeeping)
    counsel.json          the counsel process (who represents whom, per case)
    sync_log.csv          the IDS ingest, one appended row per run
    state.json            every process, one entry each
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR, DOCS_DIR

INVESTIGATIONS_FILE = "investigations.json"
# One file per case: with every case's list on disk, a single file would be
# well past the 100 MB GitHub accepts, and one day's refresh of a few open
# cases would rewrite all of it.
DOCUMENTS_INDEX_DIR = "documents_index"
# The single file it used to be; read if it is still there, and replaced by
# the directory on the next save.
DOCUMENTS_INDEX_FILE = "documents_index.json"
DOCUMENTS_STATE_FILE = "documents_state.json"
COUNSEL_FILE = "counsel.json"
STATE_FILE = "state.json"

DIGITS_RE = re.compile(r"\d+")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def write_text_atomic(path: Path, text: str) -> None:
    """Write via a temporary file so a reader never sees a half-written file.

    The local server rewrites data and pages while a browser may be loading
    them, and an interrupted run should not truncate the store either.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def dump_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def save_json(path: Path, value: Any) -> None:
    write_text_atomic(path, dump_json(value))


_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def load_documents_index(data_dir: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """Every case's document list, and a fingerprint of each file as read
    (so a save can skip the cases that did not change).

    Reads the old single documents_index.json when the directory is not
    there yet.
    """
    directory = Path(data_dir) / DOCUMENTS_INDEX_DIR
    if not directory.is_dir():
        return load_json(Path(data_dir) / DOCUMENTS_INDEX_FILE, {}), {}
    documents: dict[str, list[dict[str, Any]]] = {}
    fingerprints: dict[str, int] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            text = path.read_text(encoding="utf-8")
            documents[path.stem] = json.loads(text)
        except (OSError, json.JSONDecodeError):
            continue
        fingerprints[path.stem] = hash(text)
    return documents, fingerprints


def save_documents_index(
    data_dir: Path, documents: dict[str, list[dict[str, Any]]], fingerprints: dict[str, int]
) -> None:
    """Write each case whose list changed, remove the files of cases no longer
    listed (a renumbered docket), and retire the old single file.
    """
    directory = Path(data_dir) / DOCUMENTS_INDEX_DIR
    directory.mkdir(parents=True, exist_ok=True)
    for key, value in documents.items():
        if not _SAFE_KEY_RE.match(key):
            raise ValueError(f"unexpected case key for a file name: {key!r}")
        text = dump_json(value)
        if fingerprints.get(key) != hash(text) or not (directory / f"{key}.json").exists():
            write_text_atomic(directory / f"{key}.json", text)
            fingerprints[key] = hash(text)
    for gone in set(fingerprints) - set(documents):
        (directory / f"{gone}.json").unlink(missing_ok=True)
        del fingerprints[gone]
    legacy = Path(data_dir) / DOCUMENTS_INDEX_FILE
    if legacy.exists():
        legacy.unlink()


def number_key(value: str) -> str:
    """Collapse the ways one case gets written into a single comparable key.

    EDIS calls an instituted case "337-1478", the public site and most humans
    call it "337-TA-1478", and a pre-institution complaint is just a docket
    number like "337-3936". All of those reduce to their digit groups here so
    a user can type whichever form they have in front of them.
    """
    return "-".join(DIGITS_RE.findall(str(value or "")))


def _matches(wanted: str, candidate: str) -> bool:
    """Compare two normalized numbers, letting a bare serial ("1478") match
    the full number it belongs to ("337-1478").
    """
    if not wanted or not candidate:
        return False
    if wanted == candidate:
        return True
    if "-" not in wanted:
        return candidate.rsplit("-", 1)[-1] == wanted
    return False


def lookup_candidates(value: str) -> list[str]:
    """Number forms to try against EDIS /investigation, most specific first."""
    raw = str(value or "").strip()
    candidates = [raw]
    stripped = re.sub(r"(?i)\bta-\b", "", raw).replace("--", "-").strip("-")
    if stripped and stripped not in candidates:
        candidates.append(stripped)
    suffix = stripped.rsplit("-", 1)[-1] if stripped else ""
    if suffix and suffix not in candidates:
        candidates.append(suffix)
    return [c for c in candidates if c]


def _repoint_attachments(
    documents: list[dict[str, Any]], old_key: str, new_key: str
) -> list[dict[str, Any]]:
    """Attachment links carry the case number in their path, so they have to
    follow the files when a docket is renumbered.
    """
    for document in documents:
        for attachment in document.get("attachments") or []:
            href = attachment.get("href")
            if href:
                attachment["href"] = href.replace(f"/documents/{old_key}/", f"/documents/{new_key}/")
    return documents


@dataclass
class Store:
    """In-memory view of data/, loaded once and saved explicitly."""

    data_dir: Path = DATA_DIR
    docs_dir: Path = DOCS_DIR
    investigations: dict[str, Any] = field(default_factory=dict)
    documents: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    documents_state: dict[str, Any] = field(default_factory=dict)
    counsel: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    _documents_on_disk: dict[str, int] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "Store":
        base = Path(data_dir)
        documents, on_disk = load_documents_index(base)
        return cls(
            data_dir=base,
            docs_dir=base / "documents",
            investigations=load_json(base / INVESTIGATIONS_FILE, {}),
            documents=documents,
            documents_state=load_json(base / DOCUMENTS_STATE_FILE, {}),
            counsel=load_json(base / COUNSEL_FILE, {}),
            state=load_json(base / STATE_FILE, {}),
            _documents_on_disk=on_disk,
        )

    def save_cases(self) -> None:
        """Only the IDS ingest calls this."""
        save_json(self.data_dir / INVESTIGATIONS_FILE, self.investigations)

    def save_documents(self) -> None:
        """Only the EDIS documents process calls this."""
        save_documents_index(self.data_dir, self.documents, self._documents_on_disk)
        save_json(self.data_dir / DOCUMENTS_STATE_FILE, self.documents_state)

    def save_counsel(self) -> None:
        """Only the counsel process calls this."""
        save_json(self.data_dir / COUNSEL_FILE, self.counsel)

    def save_state(self) -> None:
        save_json(self.data_dir / STATE_FILE, self.state)

    def save(self) -> None:
        self.save_cases()
        self.save_documents()
        self.save_state()

    def find_key(self, number: str) -> str | None:
        """Resolve any spelling of a case number to the key it is stored under."""
        wanted = number_key(number)
        if not wanted:
            return None
        for key, record in self.investigations.items():
            for candidate in (key, record.get("investigation_number"), record.get("docket_number")):
                if candidate and _matches(wanted, number_key(candidate)):
                    return key
        return None

    def tracked_numbers(self) -> list[str]:
        return sorted(self.investigations)

    def numbers_with_documents(self) -> list[str]:
        """Cases the documents process has already been run for."""
        return sorted(key for key, docs in self.documents.items() if docs)

    def put_case(self, key: str, record: dict[str, Any]) -> None:
        self.investigations[key] = record

    def put_documents(self, key: str, documents: list[dict[str, Any]], **state: Any) -> None:
        self.documents[key] = documents
        self.documents_state[key] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "documents": len(documents),
            **state,
        }

    def rename(self, old_key: str, new_key: str) -> None:
        """A pre-institution docket (337-3936) becomes an investigation number
        (337-1501) once the complaint is instituted. Carry the old entry and
        its downloaded PDFs over instead of leaving a stale duplicate behind.
        """
        if old_key == new_key:
            return
        self.investigations.pop(old_key, None)
        old_documents = self.documents.pop(old_key, None)
        old_state = self.documents_state.pop(old_key, None)
        if old_documents and not self.documents.get(new_key):
            self.documents[new_key] = _repoint_attachments(old_documents, old_key, new_key)
            if old_state:
                self.documents_state[new_key] = old_state

        old_dir = self.docs_dir / old_key
        new_dir = self.docs_dir / new_key
        if old_dir.is_dir() and not new_dir.exists():
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_dir), str(new_dir))

    def record_run(self, process: str, **details: Any) -> None:
        runs = self.state.setdefault("runs", {})
        runs[process] = {"finished_at": datetime.now(timezone.utc).isoformat(), **details}
        self.state["last_run"] = runs[process]["finished_at"]

    def summary_rows(self) -> list[dict[str, Any]]:
        rows = []
        for key in sorted(set(self.investigations) | set(self.documents)):
            record = self.investigations.get(key, {})
            rows.append(
                {
                    "key": key,
                    "investigation_number": record.get("investigation_number") or key,
                    "status": record.get("status") or "(not in IDS)",
                    "stages": record.get("stage_count") or 0,
                    "documents": len(self.documents.get(key, [])),
                    "attachments": sum(
                        len(doc.get("attachments") or []) for doc in self.documents.get(key, [])
                    ),
                    "documents_fetched_at": (self.documents_state.get(key) or {}).get("fetched_at"),
                }
            )
        return rows
