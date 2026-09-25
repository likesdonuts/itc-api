"""One claims-analysis build for one investigation.

    python cli.py claims 337-1366

Reads the case record, its documents index, its Federal Register notices and
its source documents' PDFs; writes data/claims/<number>.json and appends one
row to the cost log. A failed build records the error but leaves the last
good analysis in place, so the page can keep showing it next to "Retry
claims analysis".

The pipeline, per the spec:

1. by rule: the instituted claims, from the notice of institution;
2. by rule: candidate sentences from the complaint and the decisions
   (candidates.py) -- no model;
3. by model: those sentences -> events (extract.py), 15-25 per call;
4. by code: every event checked (validate.py) before it can change a claim.

An update reads only source documents it has not read before; events from
documents read earlier are kept, as are a person's corrections.

What an analysis stores, per the spec:

    built_at             when this build finished (UTC)
    seen_documents       every public EDIS document ID the build considered,
                         source or not -- the baseline for "new activity"
    processed_sources    the documents events were actually read from
    pending_sources      source documents not read yet (no public PDF on
                         disk, or the build stopped at its budget or cap)
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
from . import candidates, checks, costs, derive, extract, fedreg, secondpass, status, timeline, validate
from . import config as claims_config

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


# The EDIS copy of the notice of institution is not read: the Federal
# Register's is the same notice, parsed by rule for free.
MODEL_KINDS = ("complaint", "alj_order", "initial_determination", "commission_notice",
               "commission_opinion", "final_determination")

PdfFetcher = Callable[[Store, str, frozenset[str]], None]


def edis_pdf_fetcher(token_loader: Callable[[], str], log: Logger = print) -> PdfFetcher | None:
    """Downloads a build's missing source PDFs through the documents process,
    using the EDIS token; without a token the build reads what is on disk.
    """
    from ..config import MissingTokenError

    try:
        token = token_loader()
    except MissingTokenError:
        log("  No EDIS token in .env: reading only the source PDFs already on disk")
        return None

    def fetch(store: Store, key: str, ids: frozenset[str]) -> None:
        from .. import docs

        docs.run(store, token, [key], download=True, only_ids=ids, log=log)

    return fetch


def _source_documents(documents: list[dict[str, Any]], cfg: claims_config.ClaimsConfig) -> list[dict[str, Any]]:
    return [
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


def _read_order(source: dict[str, Any]) -> tuple[int, str]:
    # The complaint first (it says what was asserted), then everything by date.
    return (0 if source["kind"] == "complaint" else 1, str(source.get("date") or ""))


def _build(
    store: Store,
    key: str,
    case: dict[str, Any],
    cfg: claims_config.ClaimsConfig,
    fetcher: fedreg.Fetcher,
    extractor: extract.Extractor,
    previous: dict[str, Any] | None,
    fetch_pdfs: PdfFetcher | None,
    log: Logger,
) -> dict[str, Any]:
    stage = status.primary_stage(case)
    patents = _patents(stage)
    respondents = _respondents(stage)
    number = fedreg.ta_number(key)
    title = str(case.get("title") or "")

    log("  Reading the notice of institution from the Federal Register...")
    notices = fedreg.institution_notices(fetcher, key, title)
    rule_events = _institution_events(notices, patents)
    log(f"    {len(notices)} notice(s); {len(rule_events)} instituted claim group(s)")

    # Incremental: documents read before keep their events (and cost nothing).
    previous = previous or {}
    done = set(previous.get("processed_sources") or ())
    sources = _source_documents(store.documents.get(key) or [], cfg)
    source_kinds = {s["id"]: s["kind"] for s in sources}
    # A document that is no longer a source (the definition was tightened)
    # is forgotten, events and all.
    done = {d for d in done if d.startswith("FR:") or d in source_kinds}
    documents = {str(d.get("id")): d for d in store.documents.get(key) or []}
    kept_events = []
    for event in previous.get("events") or []:
        source = event.get("source") or {}
        if source.get("kind") != "edis" or source.get("id") not in done or event.get("method") == "derived":
            continue
        # The stage rules and output checks run again over every event, as
        # they are now, without asking the model again.
        kept_events.append(
            validate.recheck(
                event,
                kind=source_kinds[source["id"]],
                document=documents.get(source["id"], source),
                patents=patents,
                respondents=respondents,
            )
        )

    todo = sorted((s for s in sources if s["kind"] in MODEL_KINDS and s["id"] not in done), key=_read_order)
    log(f"  {len(sources)} public source document(s); {len(todo)} not read yet")

    docs_dir = store.docs_dir / key
    missing = frozenset(s["id"] for s in todo if not candidates.pdf_files(docs_dir, s["id"]))
    if missing and fetch_pdfs is not None:
        log(f"  Downloading {len(missing)} source document PDF(s) from EDIS...")
        fetch_pdfs(store, key, missing)
    documents = {str(d.get("id")): d for d in store.documents.get(key) or []}

    new_events: list[dict[str, Any]] = []
    warnings: list[str] = []
    pending: list[str] = []
    sentences_sent = 0
    totals = {"candidates": 0, "mention_only": 0, "needs_review": 0}
    text_cache = claims_dir(store.data_dir) / "text"

    for index, source in enumerate(todo):
        document = documents.get(source["id"], source)
        text = candidates.document_text(
            docs_dir, source["id"], kind=source["kind"], cache_dir=text_cache, cfg=cfg, log=log
        )
        if text is None:
            pending.append(source["id"])
            continue
        found = candidates.select(text, source["id"], kind=source["kind"], cfg=cfg)
        if sentences_sent + len(found) > cfg.max_sentences_per_build:
            pending.extend(s["id"] for s in todo[index:])
            warnings.append(
                f"stopped after {sentences_sent} sentences (max_sentences_per_build); "
                f"{len(todo) - index} document(s) left for the next update"
            )
            break
        if found:
            try:
                raw = extractor.extract(
                    investigation=number, title=title, patents=patents, respondents=respondents,
                    document=document, candidates=found,
                )
            except extract.BudgetExceeded as exc:
                pending.extend(s["id"] for s in todo[index:])
                warnings.append(f"stopped at the budget: {exc}")
                log(f"  ! {exc}")
                break
            events, stats = validate.to_events(
                raw, found, document=document, kind=source["kind"],
                patents=patents, respondents=respondents, model=cfg.model,
            )
            files = [p.name for p in candidates.pdf_files(docs_dir, source["id"])]
            for event in events:
                event["source"]["files"] = files  # so the page can link the PDF
            new_events.extend(events)
            sentences_sent += len(found)
            totals["candidates"] += len(found)
            totals["mention_only"] += stats["mention_only"]
            totals["needs_review"] += stats["needs_review"]
            review = f", {stats['needs_review']} to review" if stats["needs_review"] else ""
            log(f"    {source['kind']:22} {source['id']:>8}  {len(found):3} sentence(s) -> {len(events)} event(s){review}")
        done.add(source["id"])

    if pending:
        log(f"  {len(pending)} source document(s) not read (no public PDF on disk, or stopped early)")

    # Case-wide Commission outcomes, by rule, from every source read so far;
    # the Federal Register reprint of a notice adds nothing, so the earliest
    # statement of each outcome is kept.
    # Respondent-scoped exits too -- settlements and defaults stated for named
    # respondents -- where the Commission's statement (the date an ALJ's ID
    # takes effect) is kept over the ID's own.
    derived: dict[str, dict[str, Any]] = {}
    exits: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    texts: dict[str, str] = {}
    for source in sorted(sources, key=lambda s: str(s.get("date") or "")):
        if source["id"] not in done or source["kind"] == "complaint":
            continue
        text = candidates.document_text(
            docs_dir, source["id"], kind=source["kind"], cache_dir=text_cache, cfg=cfg, log=log
        ) or ""
        texts[source["id"]] = text
        document = documents.get(source["id"], source)
        files = [p.name for p in candidates.pdf_files(docs_dir, source["id"])]
        for event in derive.commission_outcomes(text, document, kind=source["kind"]):
            event["source"]["files"] = files
            derived.setdefault(event["action"], event)
        for event in derive.respondent_events(text, document, kind=source["kind"], respondents=respondents):
            event["source"]["files"] = files
            key_ = (event["action"], tuple(sorted(event["respondents"])))
            held = exits.get(key_)
            if held is None or (source["kind"] == "commission_notice" and held["source"]["source_kind"] != "commission_notice"):
                exits[key_] = event
    defaulted = sorted({r for (action, who) in exits for r in who if action == "default"})
    for source in sorted(sources, key=lambda s: str(s.get("date") or "")):
        if source["id"] in texts and "violation" not in derived:
            for event in derive.default_relief(texts[source["id"]], documents.get(source["id"], source),
                                               kind=source["kind"], defaulted=defaulted):
                event["source"]["files"] = [p.name for p in candidates.pdf_files(docs_dir, source["id"])]
                derived["violation"] = event
    for event in list(derived.values()) + list(exits.values()):
        who = "" if event["respondents"] == ["ALL"] else f" as to {', '.join(event['respondents'])}"
        log(f"    case-wide: {event['action'].replace('_', ' ')}{who} ({event['source']['id']}, {event['date']})")

    events = rule_events + kept_events + new_events + list(derived.values()) + list(exits.values())

    # The optional second pass (off by default), then effective dates, then
    # the checks over all events together: the replay validator and
    # cross-document corroboration (checks.py).
    if cfg.second_pass:
        secondpass.run(events, extractor, cfg, investigation=number, title=title, patents=patents,
                       respondents=respondents, documents=documents, log=log)
    dated = timeline.apply(events, sources=sources, documents=store.documents.get(key) or [], texts=texts)
    linked = sum(1 for e in events if "declined review" in str(e.get("effective_note") or ""))
    issued = f"Final ID issued {dated['final_id_issued']}; " if dated["final_id_issued"] else ""
    log(f"  {issued}{linked} event(s) dated by a Commission non-review notice")
    checked = checks.run(events)
    log(
        f"  Checks: {checked['replay_flags']} replay contradiction(s), "
        f"{checked['corroborated']} event(s) corroborated, {checked['disputed']} disputed"
    )
    return {
        "key": key,
        "investigation_number": number,
        "phase": PHASE,
        "title": case.get("title"),
        "built_at": _now(),
        "pipeline_version": cfg.pipeline_version,
        "ids_hash": status.ids_hash(case),
        "seen_documents": sorted(status.public_document_ids(list(documents.values()))),
        "processed_sources": sorted(done | {n.source_id for n in notices}),
        "pending_sources": sorted(set(pending)),
        "source_documents": sources,
        "patents": patents,
        "respondents": respondents,
        "events": events,
        "corrections": previous.get("corrections") or [],
        "outcome": "ok" if events else "no_claims",
        "warnings": warnings,
        "checks": checked,
        "final_id_issued": dated["final_id_issued"],
        "cost_usd": round(extractor.cost, 6),
        "model_calls": extractor.calls,
        "usage": dict(extractor.usage),
        "extraction": totals,
        "last_attempt": {"at": _now(), "error": None},
    }


def run(
    store: Store,
    key: str,
    *,
    fetcher: fedreg.Fetcher | None = None,
    config: claims_config.ClaimsConfig | None = None,
    model_client: Any = None,
    fetch_pdfs: PdfFetcher | None = None,
    reread: bool = False,
    log: Logger = print,
) -> dict[str, Any]:
    """Create or update one investigation's analysis. Raises on failure,
    after recording it and logging what the run cost -- a failed run's
    tokens were still billed.

    `model_client` is an anthropic.Anthropic (made from .env when omitted);
    `fetch_pdfs` downloads the named documents' PDFs from EDIS. `reread`
    reads every source document again, paying for it again, when the way
    documents are read has changed; corrections are kept.
    """
    cfg = config or claims_config.load()
    case = store.investigations.get(key)
    if not case:
        raise ClaimsError(f"{key} is not on disk; run the daily sync first")
    number = fedreg.ta_number(key)
    previous = load(store.data_dir, key)
    fetcher = fetcher or fedreg.HttpFetcher(claims_dir(store.data_dir) / "fr")
    extractor = extract.Extractor(cfg, model_client, spent_before=costs.total(cfg.costs_csv), log=log)

    log(f"Claims analysis for {number}{' (reading every source document again)' if reread else ''}...")
    starting_point = {"corrections": (previous or {}).get("corrections") or []} if reread else previous
    try:
        analysis = _build(store, key, case, cfg, fetcher, extractor, starting_point, fetch_pdfs, log)
    except Exception as exc:
        record = dict(previous or {"key": key, "investigation_number": number})
        record["last_attempt"] = {"at": _now(), "error": f"{type(exc).__name__}: {exc}"}
        save_json(analysis_path(store.data_dir, key), record)
        costs.append(cfg.costs_csv, number, extractor.cost)
        log(f"  ! failed after {extractor.calls} model call(s) costing ${extractor.cost:.6f}")
        raise

    save_json(analysis_path(store.data_dir, key), analysis)
    row = costs.append(cfg.costs_csv, number, analysis["cost_usd"])
    usage = analysis["usage"]
    log(
        f"  {extractor.calls} model call(s): {usage['input_tokens']:,} input, "
        f"{usage['cache_read_input_tokens']:,} cached, {usage['output_tokens']:,} output tokens"
    )
    log(f"  Saved. Cost logged: {row}")
    for warning in analysis["warnings"]:
        log(f"  ! {warning}")
    if analysis["outcome"] == "no_claims":
        log("  No claim information found in the available documents.")
    return analysis

