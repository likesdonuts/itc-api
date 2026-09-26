"""Notes on one document, taken by Claude Haiku: what it says, point by
point, each with the page it is on and a quote from that page.

The model answers with the record_notes tool (strict). Each point is then
checked: its quote must be on the page it cites (or the page either side,
which then becomes its page), or the point is rejected -- kept in the record
with the reason, never shown. Notes are cached per document in
data/summaries/notes/<document id>.json (tracked: they were paid for) and
reused until `notes_version` in summary_config.json changes.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..store import load_json, save_json

NOTES_DIR = Path("summaries") / "notes"

TOPICS = {
    "complaint": {
        "parties": "who the complainants and respondents are: what they do, where they are",
        "technology": "the technology, in plain words: what the patented invention does",
        "products": "the accused products, and the complainant's own products",
        "ip_asserted": "each patent or other right asserted: number, what it covers, which claims are asserted",
        "importation": "how the accused products are imported or sold for importation",
        "infringement": "how the accused products are said to infringe",
        "domestic_industry": "the complainant's U.S. domestic industry: products practicing the patents (technical prong) and U.S. investment (economic prong)",
        "other_unfair_acts": "any unfair act other than patent infringement (trademark, trade secret, false advertising)",
        "relief": "the relief asked for: limited or general exclusion order, cease and desist orders, bond",
        "related_proceedings": "related litigation or proceedings (district court cases, PTAB, earlier ITC cases)",
    },
    "notice_of_institution": {
        "scope": "the patents and claims the investigation covers",
        "products": "the products at issue, as the notice describes them",
        "parties": "the complainants and respondents named",
        "staff": "whether the Office of Unfair Import Investigations is a party",
        "other": "anything else the notice orders (e.g. an early ruling on one issue)",
    },
    "answer": {
        "respondents": "who is answering and what they do",
        "general_response": "the overall position: denials, and what is admitted",
        "non_infringement": "why the products do not infringe",
        "invalidity": "why the patent claims are invalid (prior art, obviousness, section 112, section 101)",
        "unenforceability": "inequitable conduct, estoppel, license, exhaustion or other bars",
        "domestic_industry": "challenges to the complainant's domestic industry",
        "importation": "challenges to importation, or to the Commission's jurisdiction",
        "other_defenses": "other affirmative defenses",
        "relief": "arguments against the relief asked for (public interest, bond, scope of an order)",
    },
    "ruling": {
        "motion": "who moved, for what, and against whom",
        "issue": "the question the ruling decides, in plain words",
        "outcome": "granted, denied or granted in part; which claims, patents or respondents it affects",
        "reasoning": "why: the judge's main reasons",
        "effect": "what follows: issues left for the hearing, claims or respondents out of the case",
    },
    "final_id": {
        "result": "the overall result: a violation of Section 337 or not, and as to whom",
        "infringement": "findings on infringement, by patent and claim",
        "validity": "findings on validity (anticipation, obviousness, section 112, section 101) and enforceability",
        "domestic_industry": "findings on the domestic industry, technical and economic prongs",
        "importation": "findings on importation and jurisdiction",
        "claim_construction": "how key claim terms were construed, where that decided the case",
        "remedy": "the recommended remedy (limited or general exclusion order, cease and desist orders) and bond",
    },
    "commission_notice": {
        "decision": "what the Commission decided: to review (which issues) or not, and its final determination",
        "findings": "its findings on violation, infringement, validity or domestic industry",
        "remedy": "the orders it issued or is considering, and the bond",
        "public_interest": "public interest findings or requests for submissions",
        "next_steps": "what comes next: submissions due, the target date, Presidential review",
    },
    "commission_opinion": {
        "holdings": "the Commission's conclusions, issue by issue",
        "reasoning": "its main reasons, especially where it departed from the ALJ",
        "remedy": "the remedy ordered, its scope and the bond",
        "public_interest": "its public interest analysis",
        "separate_views": "any dissent or separate views",
    },
}
ALL_TOPICS = sorted({t for topics in TOPICS.values() for t in topics})
KIND_NAMES = {"complaint": "complaint", "notice_of_institution": "notice of institution",
              "answer": "response (answer) to the complaint",
              "ruling": "ruling by the Administrative Law Judge on a motion for summary determination",
              "final_id": "final initial determination of the Administrative Law Judge (with the recommended determination on remedy)",
              "commission_notice": "notice of a Commission determination",
              "commission_opinion": "Commission opinion"}

SYSTEM = """You take notes on one filing from a U.S. International Trade Commission Section 337 investigation. A writer will turn your notes into a plain-English summary for lawyers who are new to intellectual property law and to Section 337, so the notes must be accurate, specific and easy to follow.

The filing is given page by page as <page n="12">...</page>; n is the page number to cite. Some pages may be left out.

For each point:
- topic: one of the topics listed for this kind of filing.
- point: one or two plain sentences stating what the filing says, specifically (names, patent numbers, claim numbers, products, amounts, dates). Attribute it: "The complainant alleges ...", "Respondents deny ...", "The ALJ found ...", "The Commission determined ...". Never state a party's allegation or defense as fact, and never assess its merits. For a ruling or decision, say plainly what was decided and by whom; a party's argument that a decision recounts is attributed to that party.
- page: the n of the page where it is stated.
- quote: 8 to 25 consecutive words copied exactly from that page's text, character for character, including any OCR errors. Copy them from the page as given; never retype, shorten, reorder or tidy them. Choose the words that best support the point.

Cover every listed topic the pages address, most important first; skip a topic they do not address. Prefer the body of the filing over its table of contents. At most 30 points. overview: two or three sentences on what the filing is and says."""

TOOL_NAME = "record_notes"
TOOL = {
    "name": TOOL_NAME,
    "description": "Record the notes on this filing. Call exactly once.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["overview", "points"],
        "properties": {
            "overview": {"type": "string"},
            "points": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["topic", "point", "page", "quote"],
                    "properties": {
                        "topic": {"type": "string", "enum": ALL_TOPICS},
                        "point": {"type": "string"},
                        "page": {"type": "integer"},
                        "quote": {"type": "string"},
                    },
                },
            },
        },
    },
}


def user_message(*, key: str, title: str, kind: str, document: dict[str, Any], who: str, prompt_pages: str) -> str:
    topics = "\n".join(f"- {name}: {what}" for name, what in TOPICS[kind].items())
    by = f" Filed for: {who}." if who else ""
    return (
        f"Investigation {key}: {title}.\n"
        f"This filing is a {KIND_NAMES[kind]}: \"{document.get('title') or 'untitled'}\", "
        f"dated {str(document.get('document_date') or '')[:10]}.{by}\n\n"
        f"Topics for this kind of filing:\n{topics}\n\n<filing>\n{prompt_pages}\n</filing>"
    )


def _loose(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


MIN_QUOTE = 20  # loose characters: long enough not to be anywhere by chance
# How alike a quote and the page must be when not identical: OCR and PDF text
# differ in small ways (a dropped letter, "fi" ligatures), not in wording.
FUZZY_MATCH = 92


def _on_page(quote: str, page_text: str) -> bool:
    if not page_text:
        return False
    if quote in page_text:
        return True
    from rapidfuzz import fuzz

    return fuzz.partial_ratio(quote, page_text) >= FUZZY_MATCH


def validate(points: list[dict[str, Any]], texts: dict[int, str], kind: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep the points whose quote is on the page they cite (or the page
    either side, which then becomes their page) -- word for word, or all
    but identical."""
    loose_pages = {n: _loose(t) for n, t in texts.items()}
    kept, rejected = [], []
    for raw in points:
        point = {k: raw.get(k) for k in ("topic", "point", "page", "quote")}
        quote = _loose(point["quote"])
        reason = None
        # Any known topic will do: an answer's history of the dispute is worth
        # keeping even filed under "related_proceedings".
        if point["topic"] not in ALL_TOPICS:
            reason = "unknown topic"
        elif len(quote) < MIN_QUOTE:
            reason = "quote too short to check"
        else:
            page = point["page"] if isinstance(point["page"], int) else -1
            nearby = [n for n in (page, page - 1, page + 1) if n in loose_pages]
            others = [n for n in loose_pages if n not in nearby]
            found = next((n for n in nearby + others if _on_page(quote, loose_pages[n])), None)
            if found is None:
                reason = "quote not found in the filing"
            else:
                point["page"] = found
        (rejected if reason else kept).append({**point, **({"rejected": reason} if reason else {})})
    return kept, rejected


def notes_path(data_dir: Path, doc_id: str) -> Path:
    return Path(data_dir) / NOTES_DIR / f"{doc_id}.json"


def cached(data_dir: Path, doc_id: str, version: int) -> dict[str, Any] | None:
    found = load_json(notes_path(data_dir, doc_id), None)
    return found if found and found.get("notes_version") == version else None


def record(
    data_dir: Path,
    *,
    doc_id: str,
    kind: str,
    document: dict[str, Any],
    who: str,
    file: str,
    pages_read: list[int],
    answer: dict[str, Any],
    texts: dict[int, str],
    model: str,
    version: int,
    cost: float,
    truncated: bool,
) -> dict[str, Any]:
    kept, rejected = validate(list(answer.get("points") or []), texts, kind)
    result = {
        "doc_id": doc_id,
        "kind": kind,
        "title": document.get("title"),
        "date": str(document.get("document_date") or "")[:10],
        "who": who,
        "file": file,
        "pages_read": pages_read,
        "overview": str(answer.get("overview") or ""),
        "points": kept,
        "rejected": rejected,
        "truncated": truncated,
        "model": model,
        "notes_version": version,
        "cost_usd": round(cost, 6),
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    save_json(notes_path(data_dir, doc_id), result)
    return result
