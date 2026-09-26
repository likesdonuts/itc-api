"""What a case summary would read and cost, before anything is paid for.

Each document the selection reads becomes one line: which file, how many of
its pages are read (config read_pages), and whether its text is already on
file from the claims analysis. The cost is:

    notes    per document, notes_model: the instructions plus the pages read,
             in; notes_output_tokens, out
    writing  once, writer_model: the instructions plus every document's
             notes, in; writer_output_tokens, out

A document whose pages have not been counted yet is costed at its
read_pages limit, so an estimate with gaps is an upper bound ("up to").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..claims import candidates, costs
from ..store import Store
from . import pages as page_counts
from .config import SummaryConfig
from .select import Item, Selection, select

# Which read_pages entry each kind of document uses.
READ_KIND = {
    "complaint": "complaint",
    "notice_of_institution": "notice_of_institution",
    "answer": "answer",
    "ruling": "ruling",
    "final_id": "final_id",
    "commission_notice": "commission_notice",
    "commission_opinion": "commission_opinion",
}


@dataclass
class Line:
    item: Item
    total_pages: int | None  # the whole filing, every attachment
    main_pages: int | None  # the file read: the complaint's body, else the longest
    read_pages: int  # what is read of it (the limit, when not counted yet)
    counted: bool
    main_id: str = ""  # the attachment id of the file read
    body: str = ""  # how the complaint's body was found: "confirmed" (page 1 read) or "assumed"
    text_on_file: bool = False  # its text was already read for the claims analysis


@dataclass
class Estimate:
    selection: Selection
    lines: list[Line] = field(default_factory=list)
    notes_usd: float = 0.0
    writer_usd: float = 0.0
    spent_usd: float = 0.0
    budget_usd: float = 0.0
    notes_model: str = ""
    writer_model: str = ""

    @property
    def total_usd(self) -> float:
        return self.notes_usd + self.writer_usd

    @property
    def uncounted(self) -> list[Line]:
        return [line for line in self.lines if not line.counted]

    @property
    def complete(self) -> bool:
        return not self.uncounted

    @property
    def pages_read(self) -> int:
        return sum(line.read_pages for line in self.lines)

    @property
    def over_budget(self) -> bool:
        return self.spent_usd + self.total_usd > self.budget_usd


def _cost(cfg: SummaryConfig, model: str, tokens_in: int, tokens_out: int) -> float:
    rates = cfg.rates(model)
    return (tokens_in * rates.input + tokens_out * rates.output) / 1_000_000


def _cached_text(cache_dir: Path, docs_dir: Path, doc_id: str) -> dict[str, Path]:
    """The document's attachments whose text is on file (read for the
    claims analysis), by attachment id."""
    found = {}
    for pdf in candidates.pdf_files(docs_dir, doc_id):
        cached = candidates.cache_path(cache_dir, pdf)
        if cached.exists():
            found[page_counts._attachment_id(pdf)] = cached
    return found


def complaint_body(
    attachments: list[dict[str, Any]], cached: dict[str, Path], min_pages: int
) -> tuple[dict[str, Any] | None, str]:
    """The attachment that is the complaint itself. Once its text is on
    file, page 1 decides (candidates.is_complaint_body); before, the first
    attachment of at least `min_pages` pages -- the cover letters come first."""
    for att in attachments:
        path = cached.get(att["id"])
        if path is None or att["pages"] < min_pages:
            continue
        if candidates.is_complaint_body(path.read_text(encoding="utf-8", errors="replace")[:4000]):
            return att, "confirmed"
    for att in attachments:
        if att["pages"] >= min_pages:
            return att, "assumed"
    return (attachments[0], "assumed") if attachments else (None, "")


def build(
    store: Store,
    key: str,
    cfg: SummaryConfig,
    *,
    counts: dict[str, Any] | None = None,
    selection: Selection | None = None,
    spent_usd: float | None = None,
) -> Estimate:
    """`counts` (pages.json) and `spent_usd` (the cost log's total) can be
    passed in when estimating many cases at once, as the render does."""
    documents = store.documents.get(key) or []
    selection = selection or select(documents, max_answer_groups=cfg.max_answer_groups, max_rulings=cfg.max_rulings)
    counts = page_counts.load(store.data_dir) if counts is None else counts
    docs_dir = store.docs_dir / key
    cache_dir = Path(store.data_dir) / "claims" / "text"

    estimate = Estimate(
        selection=selection,
        spent_usd=costs.total(cfg.costs_csv) if spent_usd is None else spent_usd,
        budget_usd=cfg.budget_usd,
        notes_model=cfg.notes_model,
        writer_model=cfg.writer_model,
    )
    for item in selection.read:
        limit = cfg.pages_for(READ_KIND.get(item.kind, item.kind))
        entry = counts.get(item.id)
        attachments = (entry or {}).get("attachments") or []
        cached = _cached_text(cache_dir, docs_dir, item.id)
        if not entry:
            line = Line(item, None, None, limit.first + limit.last, counted=False, text_on_file=bool(cached))
        else:
            total = sum(a["pages"] for a in attachments)
            body = ""
            if item.kind == "complaint":
                main, body = complaint_body(attachments, cached, cfg.complaint_body_min_pages)
            else:
                main = max(attachments, key=lambda a: a["pages"], default=None)
            main_pages = main["pages"] if main else 0
            on_file = bool(main and main["id"] in cached)
            line = Line(item, total, main_pages, limit.of(main_pages), counted=True,
                        main_id=main["id"] if main else "", body=body, text_on_file=on_file)
        estimate.lines.append(line)
        estimate.notes_usd += _cost(
            cfg, cfg.notes_model, cfg.prompt_tokens + line.read_pages * cfg.tokens_per_page, cfg.notes_output_tokens
        )
    if estimate.lines:
        estimate.writer_usd = _cost(
            cfg, cfg.writer_model,
            cfg.writer_prompt_tokens + len(estimate.lines) * cfg.notes_output_tokens,
            cfg.writer_output_tokens,
        )
    return estimate
