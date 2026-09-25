"""Text for PDF pages that have none: scanned public versions.

The public versions of Final IDs are often scanned -- 337-TA-1384's is 58
pages of images with a text header, 337-TA-1366's 166 -- so reading them
takes OCR. It runs locally (RapidOCR on onnxruntime, no model cost), only on
pages whose own text layer is empty, and the result is cached with the rest
of the document's text, so each scanned page is read once.

OCR is optional: without the libraries installed, a scanned page simply
contributes no text, and the build says so.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

Logger = Callable[[str], None]

# A page with less text than this is treated as scanned.
MIN_TEXT_CHARS = 100

_engine = None


def available() -> bool:
    try:
        import numpy  # noqa: F401
        import pypdfium2  # noqa: F401
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def _ocr_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def read_page_image(path: Path, index: int, *, scale: float = 2.0) -> str:
    """OCR one page: render it, read the lines, join them in reading order."""
    import numpy as np
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    try:
        image = np.array(document[index].render(scale=scale).to_pil().convert("RGB"))
    finally:
        document.close()
    result, _ = _ocr_engine()(image)
    return "\n".join(line[1] for line in result or [])


def pdf_text(
    path: Path,
    *,
    max_pages: int | None = None,
    ocr: bool = True,
    scale: float = 2.0,
    log: Logger = print,
) -> str:
    """A PDF's text, page by page: the text layer where there is one, OCR
    where a page has none (up to `max_pages` pages of the document).
    """
    from pypdf import PdfReader

    pages = PdfReader(str(path)).pages
    limit = len(pages) if max_pages is None else min(len(pages), max_pages)
    scanned = {i for i in range(limit) if len((pages[i].extract_text() or "").strip()) < MIN_TEXT_CHARS}
    use_ocr = ocr and bool(scanned) and available()
    if scanned and ocr and not use_ocr:
        log(f"    ! {path.name}: {len(scanned)} scanned page(s) skipped (OCR is not installed)")
    if use_ocr:
        log(f"    OCR: {path.name}, {len(scanned)} scanned page(s) of {len(pages)} (about 5 seconds a page)")

    texts = []
    done = 0
    for i in range(limit):
        if use_ocr and i in scanned:
            texts.append(read_page_image(path, i, scale=scale))
            done += 1
            if done % 20 == 0:
                log(f"      OCR {done} of {len(scanned)} pages")
        else:
            texts.append(pages[i].extract_text() or "")
    return "\n".join(texts)
