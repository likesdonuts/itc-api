"""One case summary, written on demand.

    python cli.py summary 337-1366

Phase 2 covers what the case is about and the answers to it: the complaint,
the notice of institution and up to five answer groups (select.py). For each:

1. download the one file that is read, if it is not on disk (fetch.py);
2. read its pages -- first and last, per read_pages -- page by page (text.py);
3. Claude Haiku takes notes, each point with its page and a quote, and code
   checks every quote against its page (notes.py). Cached per document;
4. Claude Sonnet 5 writes the summary from the notes, citing them, and code
   checks every citation (write.py). Rewritten only when the notes change.

Before every model call, the most it could cost is checked against the
summary budget (summary_config.json); a run that would pass it stops, keeping
the notes already paid for, and says how to go on. Every run's cost is
appended to data/summary_costs.csv, failed ones too.

The result is data/summaries/<number>.json (tracked: it was paid for).
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..claims import costs
from ..store import Store, load_json, save_json
from . import config as summary_config
from . import fetch, notes, text, write
from .select import Item, select

Logger = Callable[[str], None]

PHASE_KINDS = ("complaint", "notice_of_institution", "answer")
CHARS_PER_TOKEN = 3.0  # on the low side, so the pre-call estimate errs high
MAX_PROMPT_CHARS = 180_000


class BudgetReached(RuntimeError):
    pass


class SummaryError(RuntimeError):
    pass


def summary_path(data_dir: Path, key: str) -> Path:
    return Path(data_dir) / "summaries" / f"{key}.json"


def load(data_dir: Path, key: str) -> dict[str, Any] | None:
    return load_json(summary_path(data_dir, key), None)


@dataclass
class Caller:
    """Model calls, priced as they return and checked against the budget
    before they go."""

    cfg: summary_config.SummaryConfig
    client: Any = None
    spent_before: float = 0.0
    cost: float = 0.0
    calls: int = 0
    usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})

    def worst_case(self, model: str, system: str, tool: dict[str, Any], user: str, max_tokens: int) -> float:
        tokens_in = int((len(system) + len(json.dumps(tool)) + len(user)) / CHARS_PER_TOKEN)
        rates = self.cfg.rates(model)
        return (tokens_in * rates.cache_write_5m + max_tokens * rates.output) / 1_000_000

    def call(self, model: str, system: str, tool: dict[str, Any], user: str, max_tokens: int) -> tuple[dict[str, Any], float, bool]:
        """(the tool's input, what the call cost, whether it was cut off)."""
        worst = self.worst_case(model, system, tool, user, max_tokens)
        spent = self.spent_before + self.cost
        if spent + worst > self.cfg.budget_usd:
            raise BudgetReached(
                f"stopped at the ${self.cfg.budget_usd:.2f} case-summary budget: ${spent:.2f} is spent and the "
                f"next call could cost up to ${worst:.2f}. To go on, raise budget_usd in summary_config.json "
                "and run the summary again"
            )
        if self.client is None:
            from ..claims.extract import api_client

            self.client = api_client()
        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": user}],
        )
        cost = self.cfg.cost(model, response.usage)
        self.cost += cost
        self.calls += 1
        for name in self.usage:
            self.usage[name] += int(getattr(response.usage, name, 0) or 0)
        block = next((b for b in response.content if getattr(b, "type", None) == "tool_use"), None)
        return (dict(block.input) if block else {}), cost, response.stop_reason == "max_tokens"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def phase_items(store: Store, key: str, cfg: summary_config.SummaryConfig) -> list[Item]:
    sel = select(store.documents.get(key) or [], max_answer_groups=cfg.max_answer_groups, max_rulings=cfg.max_rulings)
    return [item for item in sel.read if item.kind in PHASE_KINDS]


def _take_notes(store: Store, key: str, case: dict[str, Any], item: Item, cfg: summary_config.SummaryConfig,
                caller: Caller, edis: Callable[[], Any], log: Logger) -> tuple[dict[str, Any] | None, str | None]:
    """(the notes, or None and why not)."""
    documents = {str(d.get("id")): d for d in store.documents.get(key) or []}
    document = documents.get(item.id, item.doc)
    needs_edis = not fetch._on_disk(store.docs_dir / key, item.id, "*")
    main = fetch.main_file(store, key, item.id, item.kind, cfg, client=edis() if needs_edis else None, log=log)
    if main is None and not needs_edis:
        # The files on disk are not the one that is read: try EDIS for it.
        main = fetch.main_file(store, key, item.id, item.kind, cfg, client=edis(), log=log)
    if main is None:
        return None, "its file could not be had from EDIS"
    limit = cfg.pages_for(item.kind)
    numbers = text.wanted_pages(main.pages or text.page_count(main.path), limit.first, limit.last)
    texts = text.pages(main.path, numbers, data_dir=store.data_dir, log=log)
    if not any(t.strip() for t in texts.values()):
        return None, "no readable text in its pages"
    prompt_pages = text.as_prompt(texts)
    truncated = len(prompt_pages) > MAX_PROMPT_CHARS
    user = notes.user_message(key=key, title=str(case.get("title") or ""), kind=item.kind, document=document,
                              who=item.who, prompt_pages=prompt_pages[:MAX_PROMPT_CHARS])
    answer, cost, cut_off = caller.call(cfg.notes_model, notes.SYSTEM, notes.TOOL, user, cfg.notes_max_output_tokens)
    record = notes.record(
        store.data_dir, doc_id=item.id, kind=item.kind, document=document, who=item.who, file=main.path.name,
        pages_read=sorted(texts), answer=answer, texts=texts, model=cfg.notes_model, version=cfg.notes_version,
        cost=cost, truncated=truncated or cut_off,
    )
    log(f"    {item.kind:22} {item.id:>7}  {len(texts)} page(s) -> {len(record['points'])} point(s)"
        + (f", {len(record['rejected'])} rejected" if record["rejected"] else "") + f"  ${cost:.4f}")
    return record, None


def _build(store: Store, key: str, case: dict[str, Any], cfg: summary_config.SummaryConfig, caller: Caller,
           token: str | None, previous: dict[str, Any] | None, log: Logger) -> dict[str, Any]:
    items = phase_items(store, key, cfg)
    if not items:
        raise SummaryError("no public complaint, notice of institution or answer is on file for this case")

    with ExitStack() as stack:
        session: list[Any] = []

        def edis() -> Any:
            """An EDIS client, opened the first time one is needed."""
            if not session:
                if not token:
                    session.append(None)
                else:
                    from ..runner import edis_session

                    session.append(stack.enter_context(edis_session(token)))
            return session[0]

        records: list[dict[str, Any]] = []
        pending: list[dict[str, str]] = []
        stopped = None
        log(f"  Reading {len(items)} document(s): notes by {cfg.notes_model}")
        for item in items:
            hit = notes.cached(store.data_dir, item.id, cfg.notes_version)
            if hit:
                records.append(hit)
                continue
            try:
                record, why_not = _take_notes(store, key, case, item, cfg, caller, edis, log)
            except BudgetReached as exc:
                stopped = str(exc)
                pending.extend({"id": i.id, "why": "not read: budget"} for i in items[items.index(item):])
                break
            if record is None:
                log(f"    ! {item.kind} {item.id} not read: {why_not}")
                pending.append({"id": item.id, "why": why_not or ""})
            else:
                records.append(record)

    if stopped:
        raise BudgetReached(stopped)
    if not any(r["kind"] != "answer" for r in records):
        raise SummaryError("neither the complaint nor the notice of institution could be read")

    sources = sorted(r["doc_id"] for r in records)
    numbered, groups = write.number_notes(records)
    reuse = (previous and previous.get("summary") and previous.get("sources") == sources
             and previous.get("notes_version") == cfg.notes_version)
    warnings: list[str] = []
    if reuse:
        summary = previous["summary"]
        warnings = list(previous.get("warnings") or [])
        log("  The notes have not changed: the summary is kept as written.")
    else:
        user = write.user_message(key=key, title=str(case.get("title") or ""), status=str(case.get("status") or ""),
                                  numbered=numbered, groups=groups)
        log(f"  Writing the summary: {cfg.writer_model}, from {len(numbered)} note(s)")
        answer, cost, cut_off = caller.call(cfg.writer_model, write.SYSTEM, write.TOOL, user, cfg.writer_max_output_tokens)
        summary, warnings = write.check(answer, numbered, groups)
        if cut_off:
            warnings.append("the writer reached its output limit; the summary may end early")
        log(f"    {sum(len(summary[s]) for s in write.SECTIONS) + len(summary['answers'])} paragraph(s)  ${cost:.4f}")

    return {
        "key": key,
        "title": case.get("title"),
        "phase": 2,
        "built_at": _now(),
        "sources": sources,
        "notes_version": cfg.notes_version,
        "writer_model": cfg.writer_model if not reuse else previous.get("writer_model"),
        "notes_model": cfg.notes_model,
        "summary": summary,
        "notes": {nid: {k: n[k] for k in ("doc_id", "kind", "page", "quote", "point", "topic")}
                  for nid, n in numbered.items()},
        "documents": [
            {k: r.get(k) for k in ("doc_id", "kind", "title", "date", "who", "file", "pages_read", "truncated")}
            | {"points": len(r.get("points") or []), "rejected": len(r.get("rejected") or [])}
            for r in records
        ],
        "pending": pending,
        "warnings": warnings,
        "cost_usd": round(caller.cost, 6),
        "total_cost_usd": round(float((previous or {}).get("total_cost_usd") or 0) + caller.cost, 6),
        "last_attempt": {"at": _now(), "error": None},
    }


def plain_error(exc: Exception) -> str:
    """What went wrong, in words the page can show."""
    message = str(exc)
    if "credit balance is too low" in message:
        return ("the Anthropic account is out of credit: add credits at console.anthropic.com, "
                "Settings > Billing, then try again (the notes already taken are kept)")
    return message


def run(
    store: Store,
    key: str,
    *,
    token: str | None = None,
    config: summary_config.SummaryConfig | None = None,
    model_client: Any = None,
    log: Logger = print,
) -> dict[str, Any]:
    """Write or update one case's summary. Raises BudgetReached or another
    error after recording it and logging what the run cost."""
    cfg = config or summary_config.load()
    case = store.investigations.get(key)
    if not case:
        raise SummaryError(f"{key} is not on disk; run the daily sync first")
    number = str(case.get("investigation_number") or key)
    previous = load(store.data_dir, key)
    caller = Caller(cfg, model_client, spent_before=costs.total(cfg.costs_csv))
    log(f"Case summary for {key}...")
    try:
        result = _build(store, key, case, cfg, caller, token, previous, log)
    except Exception as exc:
        record = dict(previous or {"key": key, "title": case.get("title")})
        record["last_attempt"] = {"at": _now(), "error": plain_error(exc),
                                  "budget": isinstance(exc, BudgetReached)}
        record["total_cost_usd"] = round(float(record.get("total_cost_usd") or 0) + caller.cost, 6)
        save_json(summary_path(store.data_dir, key), record)
        if caller.calls:
            costs.append(cfg.costs_csv, number, caller.cost)
        log(f"  ! {exc}")
        log(f"  {caller.calls} model call(s) this run, costing ${caller.cost:.4f}")
        raise
    save_json(summary_path(store.data_dir, key), result)
    if caller.calls:
        costs.append(cfg.costs_csv, number, caller.cost)
    log(f"  Saved. {caller.calls} model call(s), ${caller.cost:.4f}; "
        f"${caller.spent_before + caller.cost:.2f} of the ${cfg.budget_usd:.2f} budget spent.")
    for warning in result["warnings"]:
        log(f"  ! {warning}")
    return result


def state(store: Store, key: str, record: dict[str, Any] | None, cfg: summary_config.SummaryConfig) -> dict[str, Any]:
    """What the Summary tab's button should offer: write, update (new
    documents to read), up to date, or retry."""
    if record and (record.get("last_attempt") or {}).get("error"):
        attempt = record["last_attempt"]
        return {"state": "budget" if attempt.get("budget") else "failed", "error": attempt["error"],
                "built_at": record.get("built_at")}
    if not record or not record.get("summary"):
        return {"state": "create"}
    wanted = sorted(i.id for i in phase_items(store, key, cfg))
    new = [d for d in wanted if d not in set(record.get("sources") or [])]
    if new or record.get("notes_version") != cfg.notes_version:
        return {"state": "new_documents", "new": len(new), "built_at": record.get("built_at")}
    return {"state": "up_to_date", "built_at": record.get("built_at")}
