"""A document's text page by page, so every note can say which page it is on.

Only the pages a summary reads are extracted: each page's own text layer, or
local OCR where a page has none (claims/ocr.py). Pages are cached per PDF in
data/summaries/text/<file>.json and added to as more are wanted:

    {"pages": 45, "text": {"1": "...", "2": "..."}}

Page numbers are the PDF's own, from 1 -- what "#page=12" opens in a browser --
not the numbers printed on the pages.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable

from ..claims import ocr

Logger = Callable[[str], None]

TEXT_DIR = Path("summaries") / "text"


def wanted_pages(total: int, first: int, last: int) -> list[int]:
    """The first `first` and last `last` pages of a `total`-page file, from 1."""
    head = range(1, min(first, total) + 1)
    tail = range(max(total - last + 1, 1), total + 1) if last else range(0)
    return sorted(set(head) | set(tail))


def page_count(pdf: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(pdf)).pages)


def pages(
    pdf: Path,
    numbers: Iterable[int],
    *,
    data_dir: Path,
    use_ocr: bool = True,
    scale: float = 2.0,
    log: Logger = print,
) -> dict[int, str]:
    """{page number: text} for the pages asked for, from the cache where it
    has them."""
    cache_path = Path(data_dir) / TEXT_DIR / f"{pdf.stem}.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}
    stored: dict[str, str] = cache.get("text") or {}
    numbers = sorted(set(numbers))
    missing = [n for n in numbers if str(n) not in stored]
    if missing:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf))
        total = len(reader.pages)
        scanned = []
        for n in missing:
            if not 1 <= n <= total:
                continue
            text = reader.pages[n - 1].extract_text() or ""
            if len(text.strip()) < ocr.MIN_TEXT_CHARS:
                scanned.append(n)
            stored[str(n)] = text
        if scanned and use_ocr and ocr.available():
            log(f"    OCR: {pdf.name}, {len(scanned)} scanned page(s) (about 5 seconds a page)")
            for n in scanned:
                stored[str(n)] = ocr.read_page_image(pdf, n - 1, scale=scale)
        cache = {"pages": total, "text": stored}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return {n: stored[str(n)] for n in numbers if str(n) in stored}


def as_prompt(texts: dict[int, str]) -> str:
    """The pages as the model sees them, each marked with its number, and a
    note where pages were skipped."""
    parts = []
    previous = None
    for n in sorted(texts):
        if previous is not None and n != previous + 1:
            parts.append(f"[pages {previous + 1}-{n - 1} not included]")
        parts.append(f'<page n="{n}">\n{texts[n].strip()}\n</page>')
        previous = n
    return "\n".join(parts)
