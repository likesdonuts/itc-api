"""Procedural schedules, read from the ALJs' orders (phase 2).

For each live investigation (before the ALJ, before the Commission, or not
yet instituted):

1. Pick the scheduling documents the ALJ and the Commission issued -- not the
   parties' proposals, motions or statements -- and of those only the ones
   that still matter: the latest full procedural schedule and everything
   after it (amendments, target-date orders and notices). An earlier schedule
   an order replaced wholesale adds nothing but a chance to show a stale date.
2. Download their PDFs if they are not on disk (EDIS).
3. Read the text: each page's own text, and local OCR for pages without one
   (a schedule's table is often pasted in as an image); cached.
4. Ask Claude Haiku for every dated event, each with the date as written and
   a verbatim quote, in a strict tool call. A date whose quote is not in the
   text, or that does not parse, is dropped and recorded as rejected.
5. Cache each document's answer by its id (data/next_actions/orders/, tracked:
   paid for), so each order is read once.

`schedule_events` then layers them: the latest full schedule as the base,
each later order's dates replacing the same event's (matched by name), and
new events added.

Costs go to next_actions_config.json's own log and budget, not the claims
analysis's.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rapidfuzz import fuzz

from ..config import DATA_DIR
from ..store import Store, load_json, save_json

Logger = Callable[[str], None]

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "next_actions_config.json"
ORDERS_DIR = Path("next_actions") / "orders"
TEXT_DIR = Path("next_actions") / "text"
COST_ROW_PREFIX = "next-actions"
PROMPT_VERSION = 2

# -- which documents ---------------------------------------------------------

ISSUED_TYPES = ("Order", "ID/RD - Other Than Final on Violation", "Notice", "Order, Commission")
_SCHEDULE_RE = re.compile(
    r"procedural\s+schedule|target\s+date|ground\s+rules|schedule\s+for\s+filing|written\s+submissions", re.I
)
# Orders that do not set or change a date.
_NOT_SETTING_RE = re.compile(
    r"^\W*(?:order\s+no\.?\s*\d+\s*:?\s*)?(?:seeking|requesting|regarding\s+the\s+parties|denying)\b"
    r"|\bseeking\s+(?:a\s+)?(?:joint\s+)?(?:submission|statement)|\bjoint\s+statement\b|returned\s+mail"
    r"|^\W*(?:order\s+no\.?\s*\d+\s*:?\s*)?(?:joint\s+)?proposed\s+procedural\s+schedule",
    re.I,
)
_FULL_SCHEDULE_RE = re.compile(
    r"(?:setting|sets|issuing|adopting)\s+(?:the\s+|a\s+)?(?:revised\s+|amended\s+)?procedural\s+schedule"
    r"|^\W*(?:order\s+no\.?\s*\d+\s*:?\s*)?adopted\s+procedural\s+schedule"
    r"|^\W*(?:order\s+no\.?\s*\d+\s*:?\s*)?(?:revised\s+|amended\s+)?procedural\s+schedule\W*$"
    r"|order\s+setting\s+(?:the\s+)?procedural\s+schedule",
    re.I,
)
_TARGET_RE = re.compile(r"target\s+date", re.I)


def _day(doc: dict[str, Any]) -> str:
    return str(doc.get("document_date") or doc.get("official_received_date") or "")[:10]


def is_schedule_document(doc: dict[str, Any]) -> bool:
    title = str(doc.get("title") or "")
    return (
        doc.get("document_type") in ISSUED_TYPES
        and bool(_SCHEDULE_RE.search(title))
        and not _NOT_SETTING_RE.search(title)
        and not title.startswith("F.R.")
        and str(doc.get("security_level") or "public").lower() == "public"
    )


def _target_only(doc: dict[str, Any]) -> bool:
    title = str(doc.get("title") or "")
    return bool(_TARGET_RE.search(title)) and not re.search(r"procedural\s+schedule|schedule\s+for\s+filing|written\s+submissions", title, re.I)


def select(documents: list[dict[str, Any]], *, since: str | None = None) -> list[dict[str, Any]]:
    """The scheduling documents worth reading, oldest first: the latest full
    procedural schedule and what came after it, with the orders and notices
    that only move the target date cut to the latest one (the case record
    carries the target date too). `since` (the final ID's date, for a case
    before the Commission) keeps only what the Commission has issued since:
    the ALJ's schedule is behind it by then.
    """
    found = sorted((d for d in documents or [] if is_schedule_document(d) and _day(d)), key=_day)
    if since:
        found = [d for d in found if _day(d) >= since]
    else:
        full = [d for d in found if _FULL_SCHEDULE_RE.search(str(d.get("title") or ""))]
        if full:
            base = _day(full[-1])
            found = [d for d in found if _day(d) >= base or _target_only(d)]
    targets = [d for d in found if _target_only(d)]
    return [d for d in found if not _target_only(d) or d is targets[-1]]


# -- PDFs and text -----------------------------------------------------------


def pdf_paths(docs_dir: Path, key: str, doc: dict[str, Any]) -> list[Path]:
    case_dir = Path(docs_dir) / key
    out = []
    for attachment in doc.get("attachments") or []:
        path = case_dir / Path(str(attachment.get("href") or "")).name
        if path.suffix.lower() == ".pdf" and path.exists():
            out.append(path)
    return out


def fetch_missing(store: Store, token: str, wanted: dict[str, list[dict[str, Any]]], *, log: Logger = print) -> int:
    """Download the PDFs of the selected documents that are not on disk.
    Returns how many files were downloaded; the document lists are saved.
    """
    from ..docs import download_document_attachments
    from ..runner import edis_session

    missing = {k: [d for d in docs if not pdf_paths(store.docs_dir, k, d)] for k, docs in wanted.items()}
    missing = {k: v for k, v in missing.items() if v}
    if not missing:
        return 0
    total = 0
    with edis_session(token) as client:
        for key, docs in missing.items():
            for doc in docs:
                attachments, downloaded = download_document_attachments(
                    client, store.docs_dir, key, str(doc.get("id")), doc.get("security_level"), log
                )
                if attachments:
                    doc["attachments"] = attachments
                total += downloaded
    store.save_documents()
    log(f"  Scheduling orders: {total} PDF(s) downloaded for {len(missing)} case(s).")
    return total


def document_text(store: Store, key: str, doc: dict[str, Any], *, ocr_max_pages: int = 30, log: Logger = print) -> str:
    """Every attachment's text, each page's own text or its OCR; cached."""
    from ..claims import ocr

    cache = Path(store.data_dir) / TEXT_DIR / f"{doc.get('id')}.txt"
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    parts = []
    for path in pdf_paths(store.docs_dir, key, doc):
        try:
            parts.append(ocr.pdf_text(path, max_pages=ocr_max_pages, log=log))
        except Exception as exc:  # one unreadable file should not stop the rest
            log(f"    ! could not read {path.name}: {exc}")
    text = "\n\n".join(parts)
    if text.strip():
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
    return text


# -- the model ---------------------------------------------------------------

CATEGORIES = (
    "discovery", "expert", "claim_construction", "motions", "prehearing", "hearing", "briefing",
    "initial_determination", "target_date", "other",
)
ROLES = ("full_schedule", "amendment", "target_date", "other")

SYSTEM = """You read orders and notices from U.S. International Trade Commission Section 337 investigations and record the procedural schedule they set.

Record every event the document gives a date or a deadline for: discovery and its cut-offs, expert reports and depositions, claim construction (Markman) briefs and hearing, motions deadlines, pre-hearing statements and conferences, the evidentiary hearing, post-hearing briefs, the final initial determination, the target date, and anything else scheduled.

Rules:
- Only what this document sets or changes. A date the document merely recites from an earlier order ("the hearing previously set for ...") is recorded only if this document keeps it in force as part of the schedule it sets.
- event: a short name as the schedule calls it, e.g. "Close of fact discovery", "Evidentiary hearing begins".
- date: YYYY-MM-DD when the document gives a calendar date; "" when the deadline is relative. For an event over several days (an evidentiary hearing listed as several dates), the first day.
- end_date: for an event over several days, its last day (YYYY-MM-DD); "" otherwise.
- relative: for a relative deadline, the rule as written, e.g. "14 days after service of the Markman order"; "" otherwise.
- quote: an exact, short substring of the document that contains the event and its date or rule, copied character for character (OCR errors included).
- role: "full_schedule" if the document sets a complete procedural schedule; "amendment" if it changes some dates of an existing one; "target_date" if it only sets or extends the target date; "other" otherwise.
- If the document sets no dates, return role "other" and no events."""

TOOL_NAME = "record_schedule"
TOOL = {
    "name": TOOL_NAME,
    "description": "Record the document's role and every scheduled event in it. Call exactly once.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["role", "events"],
        "properties": {
            "role": {"type": "string", "enum": list(ROLES)},
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["event", "date", "end_date", "relative", "category", "quote"],
                    "properties": {
                        "event": {"type": "string"},
                        "date": {"type": "string", "description": "YYYY-MM-DD, or empty for a relative deadline."},
                        "end_date": {"type": "string", "description": "For an event over several days, its last day (YYYY-MM-DD); else empty."},
                        "relative": {"type": "string", "description": "The rule as written for a relative deadline, else empty."},
                        "category": {"type": "string", "enum": list(CATEGORIES)},
                        "quote": {"type": "string", "description": "An exact substring of the document."},
                    },
                },
            },
        },
    },
}

MAX_TEXT_CHARS = 60000


@dataclass
class Config:
    model: str
    budget_usd: float
    costs_csv: Path
    max_output_tokens: int = 6000
    ocr_max_pages: int = 30


def load_config(path: Path | None = None) -> Config:
    raw = json.loads((path or CONFIG_PATH).read_text(encoding="utf-8"))
    costs = Path(raw.get("costs_csv") or "data/next_actions_costs.csv")
    return Config(
        model=raw.get("model") or "claude-haiku-4-5-20251001",
        budget_usd=float(raw.get("budget_usd") or 20.0),
        costs_csv=costs if costs.is_absolute() else ROOT / costs,
        max_output_tokens=int(raw.get("max_output_tokens") or 6000),
        ocr_max_pages=int(raw.get("ocr_max_pages") or 30),
    )


def _loose(text: str) -> str:
    # OCR reads the O of "October" as a zero now and then.
    text = re.sub(r"\b0(?=[a-z])", "o", text.lower())
    return re.sub(r"[^a-z0-9]", "", text)


_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_STOP = {"the", "and", "for", "with", "all", "any", "each", "that", "this", "file", "serve", "service", "date",
         "deadline", "due", "of", "to", "on", "by", "in", "or", "a", "an"}


def _date_forms(day: date) -> list[str]:
    """The ways a schedule writes a date, loosened like the text."""
    month = _MONTHS[day.month - 1]
    forms = {
        f"{month}{day.day}{day.year}", f"{month[:3]}{day.day}{day.year}", f"{month[:4]}{day.day}{day.year}",
        f"{day.day}{month}{day.year}", f"{day.month}{day.day}{day.year}", f"{day.month:02d}{day.day:02d}{day.year}",
        f"{day.month}{day.day}{day.year % 100:02d}", f"{day.month:02d}{day.day:02d}{day.year % 100:02d}",
    }
    return sorted(forms, key=len, reverse=True)


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 3 and w not in _STOP]


def _supported(event: dict[str, Any], text: str, loose_text: str) -> bool:
    """Whether the document says this: the quote itself, or -- for a table,
    whose cells come out of a PDF interleaved -- the date written somewhere
    with most of the event's words close to it."""
    quote = str(event.get("quote") or "")
    # A quote is evidence only when it is long enough not to be anywhere by chance.
    if len(_loose(quote)) >= 15 and (quote in text or _loose(quote) in loose_text):
        return True
    words = _words(f"{event.get('event') or ''} {event.get('relative') or ''}")
    if not words:
        return False
    if event.get("date"):
        try:
            forms = _date_forms(date.fromisoformat(event["date"]))
        except ValueError:
            return False
        spots = [m.start() for form in forms for m in re.finditer(re.escape(form), loose_text)]
        for spot in spots:
            window = loose_text[max(0, spot - 700): spot + 300]
            if sum(1 for w in words if w in window) >= max(1, round(0.6 * len(words))):
                return True
        return False
    return sum(1 for w in words if w in loose_text) >= max(1, round(0.6 * len(words)))


def validate(events: list[dict[str, Any]], text: str, issued: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep the events the document supports, with a date that parses and is
    within a plausible range of the document's date."""
    loose_text = _loose(text)
    kept, rejected = [], []
    try:
        issued_day = date.fromisoformat(issued)
    except ValueError:
        issued_day = None
    for event in events:
        event = {k: v for k, v in event.items() if k != "rejected"}
        reason = None
        if event.get("date"):
            try:
                day = date.fromisoformat(event["date"])
                if issued_day and not (-400 <= (day - issued_day).days <= 1500):
                    reason = "date implausibly far from the document's date"
            except ValueError:
                reason = "date does not parse"
            if not reason and event.get("end_date"):
                try:
                    if date.fromisoformat(event["end_date"]) < date.fromisoformat(event["date"]):
                        event["end_date"] = ""
                except ValueError:
                    event["end_date"] = ""
        elif not event.get("relative"):
            reason = "neither a date nor a relative rule"
        if not reason and not _supported(event, text, loose_text):
            reason = "not found in the document"
        (rejected if reason else kept).append({**event, **({"rejected": reason} if reason else {})})
    return kept, rejected


def revalidate(data_dir: Path) -> tuple[int, int]:
    """Check every cached answer again with the current rules (no model call);
    returns (kept, rejected) totals."""
    kept_total = rejected_total = 0
    for path in sorted(orders_dir(data_dir).glob("*.json")):
        record = load_json(path, {})
        text_path = Path(data_dir) / TEXT_DIR / f"{record.get('doc_id')}.txt"
        if not record or not text_path.exists():
            continue
        text = text_path.read_text(encoding="utf-8")
        kept, rejected = validate(list(record.get("events") or []) + list(record.get("rejected") or []), text,
                                  record.get("date") or "")
        record["events"], record["rejected"] = kept, rejected
        save_json(path, record)
        kept_total += len(kept)
        rejected_total += len(rejected)
    return kept_total, rejected_total


@dataclass
class OrdersRun:
    read: int = 0
    events: int = 0
    rejected: int = 0
    cost: float = 0.0
    stopped: str | None = None
    by_case: dict[str, float] = field(default_factory=dict)


def orders_dir(data_dir: Path = DATA_DIR) -> Path:
    return Path(data_dir) / ORDERS_DIR


def cached(data_dir: Path, doc_id: Any) -> dict[str, Any] | None:
    found = load_json(orders_dir(data_dir) / f"{doc_id}.json", None)
    return found if found and found.get("prompt_version") == PROMPT_VERSION else None


def read_order(store: Store, key: str, doc: dict[str, Any], *, client: Any, cfg: Config, prices: Any,
               log: Logger = print) -> tuple[dict[str, Any] | None, float]:
    """Extract one document's schedule (or reuse the cached answer)."""
    hit = cached(store.data_dir, doc.get("id"))
    if hit:
        return hit, 0.0
    text = document_text(store, key, doc, ocr_max_pages=cfg.ocr_max_pages, log=log)
    if not text.strip():
        return None, 0.0
    user = (
        f"Investigation {key}. Document: {doc.get('document_type')} \"{doc.get('title')}\", issued {_day(doc)}.\n\n"
        f"<document>\n{text[:MAX_TEXT_CHARS]}\n</document>"
    )
    response = client.messages.create(
        model=cfg.model,
        max_tokens=cfg.max_output_tokens,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        tools=[TOOL],
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": user}],
    )
    cost = prices.cost(cfg.model, response.usage)
    tool = next((b for b in response.content if getattr(b, "type", None) == "tool_use"), None)
    answer = dict(tool.input) if tool else {"role": "other", "events": []}
    kept, rejected = validate(list(answer.get("events") or []), text, _day(doc))
    record = {
        "doc_id": str(doc.get("id")),
        "case": key,
        "title": doc.get("title"),
        "document_type": doc.get("document_type"),
        "date": _day(doc),
        "role": answer.get("role") or "other",
        "events": kept,
        "rejected": rejected,
        "truncated": len(text) > MAX_TEXT_CHARS or response.stop_reason == "max_tokens",
        "model": cfg.model,
        "prompt_version": PROMPT_VERSION,
        "cost_usd": round(cost, 6),
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    save_json(orders_dir(store.data_dir) / f"{doc.get('id')}.json", record)
    return record, cost


def since_for(documents: list[dict[str, Any]]) -> str | None:
    """The final ID's date once there is one: from then on only the
    Commission's schedule is ahead."""
    from ..claims.timeline import final_id_issued

    return final_id_issued(documents or [])


def selected(store: Store, key: str) -> list[dict[str, Any]]:
    documents = store.documents.get(key) or []
    return select(documents, since=since_for(documents))


def live_cases(store: Store, keys: list[str] | None = None) -> list[str]:
    from .build import build_case

    candidates = keys or sorted(store.investigations)
    return [
        k for k in candidates
        if (b := build_case(store.investigations.get(k) or {}, store.documents.get(k) or []))
        and b.stage in ("alj", "commission", "pre_institution")
    ]


def run(
    store: Store,
    *,
    token: str | None,
    keys: list[str] | None = None,
    client: Any = None,
    log: Logger = print,
) -> OrdersRun:
    """Read the new scheduling orders of the live cases (or of `keys`)."""
    from ..claims import config as claims_config
    from ..claims import costs
    from ..claims.extract import CHARS_PER_TOKEN, api_client

    cfg = load_config()
    prices = claims_config.load()
    report = OrdersRun()
    wanted = {k: selected(store, k) for k in live_cases(store, keys)}
    wanted = {k: v for k, v in wanted.items() if v}
    if token:
        fetch_missing(store, token, wanted, log=log)
    todo = {k: [d for d in docs if not cached(store.data_dir, d.get("id")) and pdf_paths(store.docs_dir, k, d)]
            for k, docs in wanted.items()}
    todo = {k: v for k, v in todo.items() if v}
    if not todo:
        return report
    spent = costs.total(cfg.costs_csv)
    log(f"  Scheduling orders: reading {sum(map(len, todo.values()))} new document(s) in {len(todo)} case(s).")
    try:
        for key, docs in todo.items():
            case_cost = 0.0
            for doc in docs:
                worst = prices.cost(cfg.model, {
                    "input_tokens": int((len(SYSTEM) + MAX_TEXT_CHARS) / CHARS_PER_TOKEN),
                    "output_tokens": cfg.max_output_tokens,
                })
                if spent + report.cost + worst > cfg.budget_usd:
                    report.stopped = (f"the ${cfg.budget_usd:.2f} Next actions budget would be exceeded "
                                      f"(next_actions_config.json; ${spent + report.cost:.2f} spent)")
                    log(f"  ! stopped: {report.stopped}")
                    return report
                if client is None:
                    client = api_client()
                record, cost = read_order(store, key, doc, client=client, cfg=cfg, prices=prices, log=log)
                report.cost += cost
                case_cost += cost
                if record:
                    report.read += 1
                    report.events += len(record["events"])
                    report.rejected += len(record["rejected"])
            if case_cost:
                costs.append(cfg.costs_csv, f"{COST_ROW_PREFIX}:{key}", case_cost)
                report.by_case[key] = case_cost
    finally:
        log(f"  Scheduling orders: {report.read} read, {report.events} dated event(s), "
            f"{report.rejected} rejected, ${report.cost:.4f}.")
    return report


# -- layering ----------------------------------------------------------------


# Words that tell two otherwise alike events apart: "initial" and "rebuttal"
# expert reports are two deadlines, whatever else their names share.
_CONTRAST = {
    "initial", "opening", "rebuttal", "reply", "responsive", "response", "responses", "supplemental", "first",
    "second", "third", "final", "tentative", "proposed", "direct", "joint", "pre", "post", "prehearing",
    "posthearing", "fact", "expert", "begins", "ends", "start", "end", "complainant", "complainants",
    "respondent", "respondents", "staff",
}
_NAME_STOP = {"the", "a", "an", "and", "of", "to", "for", "on", "in", "with", "by", "any", "all", "file", "serve",
              "parties", "party", "deadline", "date", "including", "their", "its"}


def _name_words(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in _NAME_STOP}


def _same_event(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """One event named twice ("File tentative list of witnesses a party will
    call" / "File tentative list of witnesses"; "Hearing" / "Evidentiary
    hearing"), not two that share words ("Exchange initial expert reports" /
    "Exchange rebuttal expert reports")."""
    if (a.get("category") or "other") != (b.get("category") or "other"):
        return False
    wa, wb = _name_words(a["event"]), _name_words(b["event"])
    if not wa or not wb or (wa ^ wb) & _CONTRAST:
        return wa == wb
    if wa <= wb or wb <= wa:
        return True
    return fuzz.token_set_ratio(" ".join(sorted(wa)), " ".join(sorted(wb))) >= 90


def _restates(record: dict[str, Any], base: dict[str, Any] | None) -> bool:
    """A "Modified Procedural Schedule" that sets out the whole schedule again
    replaces the one before it outright."""
    count = len(record.get("events") or [])
    if record.get("role") == "full_schedule":
        return True
    return bool(base) and count >= 8 and count >= 0.6 * len(base.get("events") or [])


def schedule_events(data_dir: Path, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The case's current procedural schedule: the latest complete schedule
    (a full one, or an order restating it), with every later order's dates
    laid over it. Each event keeps its source, and the date it replaced."""
    records = [r for d in select(documents, since=since_for(documents)) if (r := cached(data_dir, d.get("id")))]
    records.sort(key=lambda r: r.get("date") or "")
    start, base = 0, None
    for i, record in enumerate(records):
        if _restates(record, base):
            start, base = i, record
    merged: list[dict[str, Any]] = []
    for record in records[start:]:
        earlier = list(merged)  # an order is only laid over what came before it
        for event in record.get("events") or []:
            item = {
                "event": event["event"], "date": event.get("date") or None,
                "end": event.get("end_date") or None, "relative": event.get("relative") or None,
                "category": event.get("category") or "other", "quote": event.get("quote"),
                "source": {"id": record["doc_id"], "title": record.get("title"), "date": record.get("date")},
            }
            match = next((m for m in earlier if _same_event(m, item)), None)
            if match is None:
                merged.append(item)
            else:
                earlier.remove(match)
                merged[merged.index(match)] = item | (
                    {"replaces": match.get("date")} if match.get("date") != item["date"] else {}
                )
    return merged
