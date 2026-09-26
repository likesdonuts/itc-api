"""The cost preview, for the command line and the app's "Estimate cost"
button: count the pages of what a summary would read (EDIS's attachment
list, no downloads, no model), then say what it would read and cost.

    python cli.py summary-plan 337-1366
"""

from __future__ import annotations

from typing import Callable

from ..store import Store
from . import config as summary_config
from . import estimate as summary_estimate
from . import pages as page_counts
from .select import select

Logger = Callable[[str], None]

KIND_LABEL = {
    "complaint": "Complaint",
    "notice_of_institution": "Notice of institution",
    "answer": "Answer",
    "ruling": "Ruling",
    "final_id": "Final ID",
    "commission_notice": "Commission notice",
    "commission_opinion": "Commission opinion",
    "supplement": "Supplement",
    "termination": "Termination",
    "default": "Default",
    "not_reviewed": "Not reviewed",
    "remedy": "Remedial order",
}


def count_pages(store: Store, key: str, *, token: str | None, log: Logger = print) -> int:
    """Count the pages of every document the summary would read and has not
    been counted: from disk when all its PDFs are there, else from EDIS (one
    request per document) when there is a token."""
    cfg = summary_config.load()
    selection = select(store.documents.get(key) or [], max_answer_groups=cfg.max_answer_groups,
                       max_rulings=cfg.max_rulings)
    wanted = [item.id for item in selection.read]
    counted = page_counts.load(store.data_dir)
    todo = [doc_id for doc_id in wanted if doc_id not in counted]
    if not todo:
        return 0
    if token is None:
        return page_counts.fill(store, key, todo, log=log)
    from ..runner import edis_session

    log(f"  Counting the pages of {len(todo)} document(s) (EDIS attachment lists; nothing downloaded)...")
    with edis_session(token) as client:
        return page_counts.fill(store, key, todo, client=client, log=log)


def pages(n: int | None) -> str:
    return f"{n or 0:,} page" + ("" if n == 1 else "s")


def money(value: float) -> str:
    return f"${value:,.2f}" if value >= 0.01 else "under $0.01"


def describe(est: summary_estimate.Estimate) -> list[str]:
    """The preview as lines of text."""
    lines = []
    for line in est.lines:
        item = line.item
        if not line.counted:
            size = f"up to {line.read_pages} pages (not counted yet)"
        elif item.kind == "complaint":
            how = "found by its first page" if line.body == "confirmed" else "assumed: first long file"
            size = f"{line.read_pages} of the complaint's {line.main_pages} pages ({how}); {line.total_pages:,} in the filing"
        elif line.read_pages == line.main_pages:
            size = "all " + pages(line.main_pages) if line.main_pages != 1 else "its 1 page"
        else:
            size = f"{line.read_pages} of {pages(line.main_pages)}"
        on_file = "; text on file" if line.text_on_file else ""
        who = f" [{item.who}]" if item.who else ""
        lines.append(f"  READ   {KIND_LABEL.get(item.kind, item.kind):20} {item.day}  {item.id:>7}  {item.title[:70]}{who}")
        lines.append(f"         {size}{on_file}. {item.why}.")
    for item in est.selection.noted:
        lines.append(f"  NOTED  {KIND_LABEL.get(item.kind, item.kind):20} {item.day}  {item.id:>7}  {item.title[:70]}")
    for item in est.selection.not_read:
        who = f" [{item.who}]" if item.who else ""
        lines.append(f"  LEFT   {KIND_LABEL.get(item.kind, item.kind):20} {item.day}  {item.id:>7}  {item.title[:70]}{who}")
        lines.append(f"         {item.why}.")
    if est.selection.skipped_filings:
        n = est.selection.skipped_filings
        lines.append(f"  ({n} appendix or exhibit filing{'' if n == 1 else 's'} to the complaint, not read)")
    bound = "Up to" if not est.complete else "About"
    lines.append("")
    lines.append(
        f"{bound} {money(est.total_usd)}: notes {money(est.notes_usd)} ({est.notes_model}, "
        f"{est.pages_read:,} pages), writing {money(est.writer_usd)} ({est.writer_model})."
    )
    if not est.complete:
        lines.append(f"{len(est.uncounted)} document(s) not counted yet: costed at their page limits.")
    lines.append(f"Spent so far {money(est.spent_usd)} of the {money(est.budget_usd)} budget.")
    return lines
