"""Persistence for everything the data layer collects.

The JSON files under data/ are the contract between the two layers: data
processes write them, the UI layer only ever reads them. Keeping all the
file handling here means neither side has to know about paths or formats.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR, DOCS_DIR

INVESTIGATIONS_FILE = "investigations.json"
DOCUMENTS_INDEX_FILE = "documents_index.json"
RSS_LOG_FILE = "rss_log.json"
STATE_FILE = "state.json"

DIGITS_RE = re.compile(r"\d+")


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


@dataclass
class Store:
    """In-memory view of data/, loaded once and saved explicitly."""

    data_dir: Path = DATA_DIR
    docs_dir: Path = DOCS_DIR
    investigations: dict[str, Any] = field(default_factory=dict)
    documents: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    rss_log: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "Store":
        base = Path(data_dir)
        return cls(
            data_dir=base,
            docs_dir=base / "documents",
            investigations=load_json(base / INVESTIGATIONS_FILE, {}),
            documents=load_json(base / DOCUMENTS_INDEX_FILE, {}),
            rss_log=load_json(base / RSS_LOG_FILE, {}),
            state=load_json(base / STATE_FILE, {}),
        )

    def save_investigations(self) -> None:
        save_json(self.data_dir / INVESTIGATIONS_FILE, self.investigations)
        save_json(self.data_dir / DOCUMENTS_INDEX_FILE, self.documents)

    def save_rss_log(self) -> None:
        save_json(self.data_dir / RSS_LOG_FILE, self.rss_log)

    def save_state(self) -> None:
        save_json(self.data_dir / STATE_FILE, self.state)

    def save(self) -> None:
        self.save_investigations()
        self.save_rss_log()
        self.save_state()

    def find_investigation_key(self, number: str) -> str | None:
        """Resolve any spelling of a case number to the key it is stored under."""
        wanted = number_key(number)
        if not wanted:
            return None
        for key, record in self.investigations.items():
            for candidate in (key, record.get("investigation_number"), record.get("docket_number")):
                if candidate and _matches(wanted, number_key(candidate)):
                    return key
        return None

    def find_key(self, number: str) -> str | None:
        """Like `find_investigation_key`, but also matches dockets that the RSS
        feed has reported without a fetched investigation record yet.
        """
        key = self.find_investigation_key(number)
        if key:
            return key
        wanted = number_key(number)
        for key in self.rss_log:
            if _matches(wanted, number_key(key)):
                return key
        return None

    def tracked_numbers(self) -> list[str]:
        return sorted(set(self.investigations) | set(self.rss_log))

    def rss_documents(self, key: str) -> dict[str, Any]:
        return self.rss_log.get(key, {}).get("documents", {})

    def put(self, key: str, record: dict[str, Any], documents: list[dict[str, Any]]) -> None:
        self.investigations[key] = record
        self.documents[key] = documents

    def rename(self, old_key: str, new_key: str) -> None:
        """A pre-institution docket (337-3936) becomes an investigation number
        (337-1501) once the complaint is instituted. Carry the old entry and
        its downloaded PDFs over instead of leaving a stale duplicate behind.
        """
        if old_key == new_key:
            return
        self.investigations.pop(old_key, None)
        self.documents.pop(old_key, None)
        if old_key in self.rss_log:
            old_entry = self.rss_log.pop(old_key)
            new_entry = self.rss_log.get(new_key, {})
            merged = {**new_entry, **old_entry}
            merged["documents"] = {
                **new_entry.get("documents", {}),
                **old_entry.get("documents", {}),
            }
            self.rss_log[new_key] = merged

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
        for key in sorted(set(self.investigations) | set(self.rss_log)):
            record = self.investigations.get(key, {})
            rows.append(
                {
                    "key": key,
                    "investigation_number": record.get("investigation_number") or key,
                    "status": record.get("investigation_status") or "(not fetched)",
                    "documents": len(self.documents.get(key, [])),
                    "attachments": sum(
                        len(doc.get("attachments") or []) for doc in self.documents.get(key, [])
                    ),
                    "last_refreshed": record.get("last_refreshed"),
                }
            )
        return rows
