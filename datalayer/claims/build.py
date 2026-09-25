"""One claims-analysis build for one investigation.

    python cli.py claims 337-1366

Reads the case record, its documents index and its Federal Register notices;
writes data/claims/<number>.json and appends one row to the cost log. A
failed build records the error but leaves the last good analysis in place,
so the page can keep showing it next to "Retry claims analysis".

What an analysis stores, per the spec:

    built_at             when this build finished (UTC)
    seen_documents       every public EDIS document ID the build considered,
                         source or not -- the baseline for "new activity"
    processed_sources    the documents events were actually read from
    ids_hash             the IDS record's fingerprint at build time
    pipeline_version     from claims_config.json
    events               every claim event, each with its provenance
    corrections          a person's fixes to events, kept across rebuilds
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..store import Store, load_json, save_json
from . import config as claims_config
from . import costs, fedreg, status

Logger = Callable[[str], None]

PHASE = "Violation"


class ClaimsError(RuntimeError):
    pass


def claims_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "claims"


def analysis_path(data_dir: Path, key: str) -> Path:
    return claims_dir(data_dir) / f"{key}.json"


def load(data_dir: Path, key: str) -> dict[str, Any] | None:
    return load_json(analysis_path(data_dir, key), None)


def load_all(data_dir: Path) -> dict[str, dict[str, Any]]:
    """Every stored analysis, by case key. Few cases have one, so this is cheap."""
    folder = claims_dir(data_dir)
    if not folder.is_dir():
        return {}
    found = {}
    for path in sorted(folder.glob("*.json")):
        analysis = load_json(path, None)
        if isinstance(analysis, dict):
            found[path.stem] = analysis
    return found


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def event_id(*parts: Any) -> str:
    """Stable across rebuilds, so a correction keyed to it still finds it."""
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]


def _patents(stage: dict[str, Any]) -> list[str]:
    return [
        str(item["number"])
        for item in (stage.get("lists") or {}).get("intellectual_property") or []
        if str(item.get("type") or "").lower() == "patent" and item.get("number")
    ]


def _respondents(stage: dict[str, Any]) -> list[str]:
    return [
        str(p["name"])
        for p in (stage.get("lists") or {}).get("participants") or []
        if p.get("role") == "Respondent" and p.get("name")
    ]


def _institution_events(notices: list[fedreg.Notice], patents: list[str]) -> list[dict[str, Any]]:
    events = []
    for notice in notices:
        effective = fedreg.issued_date(notice)
        for item in fedreg.instituted_claims(notice, patents):
            notes = [item.note] if item.note else []
            checks_passed = bool(item.claims) and item.patent is not None and item.quote in item.sentence
            events.append(
                {
                    "id": event_id(notice.source_id, "instituted", item.patent, item.verbatim),
                    "stage": "instituted",
                    "action": "instituted",
                    "speaker": "tribunal_ruling",
                    "patent": item.patent or "unknown",
                    "claims_verbatim": item.verbatim,
                    "claims": list(item.claims),
                    "respondents": ["ALL"],
                    "date": effective,
                    "quote": item.quote,
                    "sentence": item.sentence,
                    "method": "rule",
                    "status": "ok" if checks_passed else "needs_review",
                    "notes": notes,
                    "source": {
                        "kind": "federal_register",
                        "id": notice.source_id,
                        "title": notice.title,
                        "url": notice.html_url,
                        "published": notice.publication_date,
                    },
                }
            )
    return events


def _build(
    store: Store,
    key: str,
    case: dict[str, Any],
    cfg: claims_config.ClaimsConfig,
    fetcher: fedreg.Fetcher,
    previous: dict[str, Any] | None,
    log: Logger,
) -> dict[str, Any]:
    stage = status.primary_stage(case)
    patents = _patents(stage)
    documents = store.documents.get(key) or []

    sources = [
        {
            "id": str(d.get("id")),
            "kind": kind,
            "document_type": d.get("document_type"),
            "title": d.get("title"),
            "date": d.get("document_date") or d.get("official_received_date"),
        }
        for d in documents
        if (kind := cfg.source_kind(d))
    ]
    log(f"  {len(patents)} patent(s) in IDS; {len(sources)} public source document(s) on file")

    log("  Reading the notice of institution from the Federal Register...")
    notices = fedreg.institution_notices(fetcher, key, str(case.get("title") or ""))
    log(f"  {len(notices)} notice(s) of institution found")
    events = _institution_events(notices, patents)
    for event in events:
        log(f"    instituted: claims {event['claims_verbatim']} of {event['patent']} ({event['status']})")

    return {
        "key": key,
        "investigation_number": fedreg.ta_number(key),
        "phase": PHASE,
        "title": case.get("title"),
        "built_at": _now(),
        "pipeline_version": cfg.pipeline_version,
        "ids_hash": status.ids_hash(case),
        "seen_documents": sorted(status.public_document_ids(documents)),
        "processed_sources": sorted({n.source_id for n in notices}),
        "source_documents": sources,
        "patents": patents,
        "respondents": _respondents(stage),
        "events": events,
        "corrections": (previous or {}).get("corrections") or [],
        "outcome": "ok" if events else "no_claims",
        "cost_usd": 0.0,
        "last_attempt": {"at": _now(), "error": None},
    }


def run(
    store: Store,
    key: str,
    *,
    fetcher: fedreg.Fetcher | None = None,
    config: claims_config.ClaimsConfig | None = None,
    log: Logger = print,
) -> dict[str, Any]:
    """Build (or rebuild) one investigation's analysis. Raises on failure,
    after recording it and logging the run's cost.
    """
    cfg = config or claims_config.load()
    case = store.investigations.get(key)
    if not case:
        raise ClaimsError(f"{key} is not on disk; run the daily sync first")
    number = fedreg.ta_number(key)
    previous = load(store.data_dir, key)
    fetcher = fetcher or fedreg.HttpFetcher(claims_dir(store.data_dir) / "fr")

    log(f"Claims analysis for {number}...")
    try:
        analysis = _build(store, key, case, cfg, fetcher, previous, log)
    except Exception as exc:
        record = dict(previous or {"key": key, "investigation_number": number})
        record["last_attempt"] = {"at": _now(), "error": f"{type(exc).__name__}: {exc}"}
        save_json(analysis_path(store.data_dir, key), record)
        costs.append(cfg.costs_csv, number, 0.0)
        raise

    save_json(analysis_path(store.data_dir, key), analysis)
    row = costs.append(cfg.costs_csv, number, analysis["cost_usd"])
    log(f"  Saved. Cost logged: {row}")
    if analysis["outcome"] == "no_claims":
        log("  No claim information found in the available documents.")
    return analysis
