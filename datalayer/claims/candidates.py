"""Candidate sentences: what is worth a model call, found with no model.

For each source document on disk:

1. Read its PDF text (cached under data/claims/text/). For a complaint, only
   the complaint itself -- not its exhibits, and never its claim charts,
   which name claims hundreds of times and say nothing about which are
   asserted.
2. Drop the sections under headings that describe the parties' positions.
3. Split into sentences (text.split_sentences) and keep those that name
   claims and carry ruling language -- or, in a complaint, assertion
   language. One with argument language and no ruling language goes.

Each candidate keeps the sentence before it as context, so the model can
tell a ruling from a recap of the ruling before it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import ClaimsConfig
from .text import find_claim_refs, split_sentences

_NUMBERED_HEADING = re.compile(r"^(?:[IVXLC]{1,6}\.|[A-Z]\.|\d{1,2}\.|[a-z]\)|\([a-z0-9]{1,3}\))\s+\S")
_TABLE_INTRO = re.compile(r"(?i)\bthe\s+following\s+claims\b|\(collectively,?\s+(?:the\s+)?[\"“']?asse\S*ted\s+claims")
_CLAIM_CHART = re.compile(r"\bclaim\s+charts?\b|\binfringement\s+chart\b", re.I)
_EXHIBIT_START = re.compile(r"^\s*(?:exhibit|appendix|attachment)\b", re.I)


@dataclass(frozen=True)
class Candidate:
    id: str  # "S<n>" within one model call's document
    doc_id: str
    sentence: str
    context: str

    @property
    def fingerprint(self) -> str:
        """Stable across builds, unlike the numbering."""
        return hashlib.sha1(self.sentence.encode("utf-8")).hexdigest()[:12]


def pdf_files(docs_dir: Path, doc_id: str) -> list[Path]:
    return sorted(Path(docs_dir).glob(f"{doc_id}_*.pdf"))


def cache_path(cache_dir: Path, pdf: Path) -> Path:
    """Where a PDF's text is kept. The ".ocr" marks text read with OCR for
    scanned pages, so text cached before OCR existed is not reused.
    """
    return Path(cache_dir) / f"{pdf.stem}.ocr.txt"


def pdf_text(path: Path, cache_dir: Path, read: Callable[[Path], str]) -> str:
    """A PDF's text, extracted once and kept (the PDFs never change)."""
    cached = cache_path(cache_dir, path)
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    text = read(path)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(text, encoding="utf-8")
    return text


def is_complaint_body(text: str) -> bool:
    """The complaint itself, as opposed to one of its exhibits or charts."""
    head = text[:4000]
    if _CLAIM_CHART.search(head):
        return False
    if _EXHIBIT_START.match(head.lstrip()[:40]):
        return False
    return "complaint" in head.lower()


def _is_heading(line: str) -> bool:
    return 2 <= len(line) <= 90 and not line.endswith((".", ",", ";")) and (
        bool(_NUMBERED_HEADING.match(line)) or len(line.split()) <= 8
    )


def without_position_sections(text: str, headings: Iterable[re.Pattern[str]]) -> str:
    """The text minus every section under a "parties' positions" heading.

    A section runs from such a heading to the next numbered heading, or to a
    heading that is not about positions (an "Analysis" heading ends it).
    """
    headings = list(headings)
    kept: list[str] = []
    skipping = False
    for raw in text.splitlines():
        line = raw.strip()
        if _is_heading(line):
            if any(p.search(line) for p in headings):
                skipping = True
                continue
            if skipping and (_NUMBERED_HEADING.match(line) or re.search(r"(?i)\b(analysis|discussion|conclusions?|findings)\b", line)):
                skipping = False
            if not skipping and _NUMBERED_HEADING.match(line):
                # A heading is not the start of the next sentence.
                kept.append(line + ".")
                continue
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def _has(text: str, keywords: Iterable[str]) -> bool:
    lowered = text.lower()
    for keyword in keywords:
        # A keyword written in capitals ("GRANTED") must appear in capitals.
        if keyword.isupper() and len(keyword) > 1:
            if re.search(rf"\b{re.escape(keyword)}\b", text):
                return True
        elif keyword.lower() in lowered:
            return True
    return False


def select(
    text: str,
    doc_id: str,
    *,
    kind: str,
    cfg: ClaimsConfig,
) -> list[Candidate]:
    """The sentences of one document worth sending to the model."""
    if kind == "complaint":
        body = text
        wanted = lambda s: _has(s, cfg.complaint_keywords)  # noqa: E731
    else:
        body = without_position_sections(text, cfg.position_headings)
        wanted = lambda s: _has(s, cfg.ruling_keywords)  # noqa: E731

    sentences = split_sentences(body)
    found: list[Candidate] = []
    seen: set[str] = set()
    for index, sentence in enumerate(sentences):
        if kind == "complaint" and _TABLE_INTRO.search(sentence) and not find_claim_refs(sentence):
            # "... at least the following claims (collectively, the Asserted
            # Claims) ...:" introduces a table of patents and claim numbers
            # with no "claims" before each list; send the table with it.
            sentence = " ".join([sentence, *sentences[index + 1 : index + 4]])[:2500]
        elif len(sentence) > 2500 or not find_claim_refs(sentence) or not wanted(sentence):
            continue
        if sentence in seen:
            continue
        seen.add(sentence)
        context = sentences[index - 1] if index else ""
        found.append(Candidate(id=f"S{len(found) + 1}", doc_id=doc_id, sentence=sentence, context=context[-600:]))
    return found


def document_text(
    docs_dir: Path,
    doc_id: str,
    *,
    kind: str,
    cache_dir: Path,
    cfg: ClaimsConfig | None = None,
    read: Callable[[Path], str] | None = None,
    log: Callable[[str], None] = print,
) -> str | None:
    """The readable text of one source document, or None if no PDF of it is
    on disk. Scanned pages are read with OCR (ocr.py). A complaint
    contributes only its own body, not its exhibits, and only its first
    pages are OCR'd: the asserted claims come early, the exhibits after.
    """
    files = pdf_files(docs_dir, doc_id)
    if not files:
        return None
    if read is None:
        from . import ocr

        max_pages = (cfg.ocr_complaint_pages if cfg else 60) if kind == "complaint" else (cfg.ocr_max_pages if cfg else None)
        use_ocr = cfg.ocr_enabled if cfg else True
        scale = cfg.ocr_scale if cfg else 2.0

        def read(path: Path) -> str:
            if kind == "complaint":
                # A complaint filing is mostly exhibits. Read one page first
                # and OCR the rest only if it is the complaint itself; for an
                # exhibit, the first page is all that is kept (and cached).
                first = ocr.pdf_text(path, max_pages=1, ocr=use_ocr, scale=scale, log=lambda m: None)
                if not is_complaint_body(first):
                    return first
            return ocr.pdf_text(path, max_pages=max_pages, ocr=use_ocr, scale=scale, log=log)

    parts = []
    for path in files:
        try:
            text = pdf_text(path, cache_dir, read)
        except Exception as exc:  # one unreadable PDF should not stop the build
            log(f"    ! could not read {path.name}: {exc}")
            continue
        if kind == "complaint" and not is_complaint_body(text):
            continue
        parts.append(text)
    return "\n".join(parts) if parts else ""


def chunks(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), max(1, size))]
