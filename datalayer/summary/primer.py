"""The "How Section 337 works" explainer shown with every case summary.

Written by hand, not by a model, and the same for every case:
content/section337_primer.md. Its first lines say whether a Section 337
practitioner has reviewed it:

    status: draft            (or: reviewed)
    reviewed_by: <name>
    reviewed_on: <date>

Until it says "reviewed", the page labels it a draft. The rest is a small
subset of Markdown: "## " headings, "- " and "1. " lists, paragraphs,
**bold** and *italics*.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import ROOT

PRIMER_PATH = ROOT / "content" / "section337_primer.md"

_META = re.compile(r"^(status|reviewed_by|reviewed_on):\s*(.*)$")
_ORDERED = re.compile(r"^\d+\.\s+")


@dataclass(frozen=True)
class Primer:
    html: str
    reviewed: bool
    reviewed_by: str = ""
    reviewed_on: str = ""


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", out)


def to_html(markdown: str) -> str:
    blocks: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []
    list_tag = ""

    def flush() -> None:
        nonlocal list_tag
        if paragraph:
            blocks.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()
        if items:
            blocks.append(f"<{list_tag}>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + f"</{list_tag}>")
            items.clear()
            list_tag = ""

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            flush()
        elif line.startswith("## "):
            flush()
            blocks.append(f"<h3>{_inline(line[3:])}</h3>")
        elif line.startswith("- ") or _ORDERED.match(line):
            tag = "ul" if line.startswith("- ") else "ol"
            if paragraph or (items and tag != list_tag):
                flush()
            list_tag = tag
            items.append(line[2:] if tag == "ul" else _ORDERED.sub("", line))
        elif items:
            items[-1] += " " + line  # a list item continued on the next line
        else:
            paragraph.append(line)
    flush()
    return "\n".join(blocks)


def load(path: Path = PRIMER_PATH) -> Primer | None:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    meta: dict[str, str] = {}
    body = []
    header = True
    for line in text.splitlines():
        match = _META.match(line.strip()) if header else None
        if match:
            meta[match.group(1)] = match.group(2).strip()
            continue
        if line.strip():
            header = False
        body.append(line)
    return Primer(
        html=to_html("\n".join(body)),
        reviewed=meta.get("status", "").lower() == "reviewed",
        reviewed_by=meta.get("reviewed_by", ""),
        reviewed_on=meta.get("reviewed_on", ""),
    )
