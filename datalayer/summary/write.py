"""The summary itself, written by Claude Sonnet 5 from the documents' notes.

The writer sees only the notes -- each numbered ("n7"), with its document,
page and topic -- never the filings, and cites the notes each paragraph rests
on. Code then checks the citations: an unknown note number is dropped, and a
paragraph left citing nothing is dropped too, with a warning. So everything
shown traces to a note, and every note to a quote on a page.
"""

from __future__ import annotations

from typing import Any

SECTIONS = ("about", "allegations")

SYSTEM = """You write the opening of a case summary for a U.S. International Trade Commission Section 337 investigation. The readers are lawyers who know litigation but not intellectual property law or Section 337.

You are given numbered notes (n1, n2, ...) taken from the complaint, the Commission's notice of institution and the respondents' answers, each with its source and page. Write only from the notes: every statement must be supported by a note you cite. If the notes do not say something, leave it out; never add facts, law or background from elsewhere beyond a few words defining a term.

Write:
- headline: one sentence saying who accuses whom of what, over which products.
- about: one or two paragraphs. The parties; the technology and the products in plain words; the patents (or other rights) and what they cover; what the Commission instituted the investigation on.
- allegations: two to four paragraphs on how the complainant says Section 337 is violated: importation, infringement, its domestic industry, and the relief it asks for.
- answers: for each answer group listed, one paragraph of two to four sentences on that group's main defenses. Use the group's id from the list.

Rules:
- Each paragraph lists in cites the ids of the notes it relies on (e.g. ["n3", "n7"]).
- Attribute allegations and defenses ("EPC alleges", "Innoscience denies"); never say who is right or predict the outcome.
- Plain English. The first time a term of art appears, define it in a few words: domestic industry (the complainant's U.S. activity tied to the patent that Section 337 requires), limited or general exclusion order, cease and desist order, claim, prior art.
- Refer to patents by their short form after the first mention ("the '294 patent"). Give claim numbers only as the notes give them.
- Plain prose: no headings, lists or markdown. About 400 to 700 words in all."""

_PARAGRAPH = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text", "cites"],
    "properties": {"text": {"type": "string"}, "cites": {"type": "array", "items": {"type": "string"}}},
}
TOOL_NAME = "write_summary"
TOOL = {
    "name": TOOL_NAME,
    "description": "Record the summary. Call exactly once.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["headline", "about", "allegations", "answers"],
        "properties": {
            "headline": {"type": "string"},
            "about": {"type": "array", "items": _PARAGRAPH},
            "allegations": {"type": "array", "items": _PARAGRAPH},
            "answers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["group", "paragraph"],
                    "properties": {"group": {"type": "string"}, "paragraph": _PARAGRAPH},
                },
            },
        },
    },
}

SOURCE_LABELS = {"complaint": "Complaint", "notice_of_institution": "Notice of institution", "answer": "Answer"}


def number_notes(records: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Every point of every document, numbered n1, n2, ... in reading order;
    and the answer groups, numbered A1, A2, ..."""
    numbered: dict[str, dict[str, Any]] = {}
    groups: dict[str, dict[str, Any]] = {}
    for record in records:
        if record["kind"] == "answer":
            gid = f"A{len(groups) + 1}"
            groups[gid] = {"who": record.get("who") or "", "doc_id": record["doc_id"]}
        for point in record.get("points") or []:
            nid = f"n{len(numbered) + 1}"
            numbered[nid] = {**point, "doc_id": record["doc_id"], "kind": record["kind"], "who": record.get("who") or ""}
    return numbered, groups


def user_message(*, key: str, title: str, status: str, numbered: dict[str, dict[str, Any]],
                 groups: dict[str, dict[str, Any]]) -> str:
    lines = [f"Investigation {key}: {title}. Status: {status or 'unknown'}.", "", "Answer groups:"]
    lines += [f"- {gid}: {g['who']}" for gid, g in groups.items()] or ["- (none: no answers were read)"]
    lines += ["", "Notes:"]
    for nid, note in numbered.items():
        source = SOURCE_LABELS.get(note["kind"], note["kind"])
        if note["kind"] == "answer":
            gid = next((g for g, v in groups.items() if v["doc_id"] == note["doc_id"]), "")
            source += f" {gid}"
        lines.append(f"[{nid}] ({source}, p. {note['page']}; {note['topic']}) {note['point']}")
    return "\n".join(lines)


def check(answer: dict[str, Any], numbered: dict[str, Any], groups: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The summary with only valid citations, and warnings for what was dropped."""
    warnings: list[str] = []

    def paragraph(raw: dict[str, Any], where: str) -> dict[str, Any] | None:
        cites = [c for c in dict.fromkeys(raw.get("cites") or []) if c in numbered]
        unknown = [c for c in raw.get("cites") or [] if c not in numbered]
        if unknown:
            warnings.append(f"{where}: dropped unknown citation(s) {', '.join(unknown)}")
        text = " ".join(str(raw.get("text") or "").split())
        if not text:
            return None
        if not cites:
            warnings.append(f"{where}: dropped a paragraph that cited no note")
            return None
        return {"text": text, "cites": cites}

    out: dict[str, Any] = {"headline": " ".join(str(answer.get("headline") or "").split())}
    for section in SECTIONS:
        out[section] = [p for i, raw in enumerate(answer.get(section) or [])
                        if (p := paragraph(raw, f"{section} paragraph {i + 1}"))]
    # One entry per answer group, in the groups' order; a group the writer
    # gave several paragraphs keeps them all.
    by_group: dict[str, list[dict[str, Any]]] = {}
    for raw in answer.get("answers") or []:
        gid = str(raw.get("group") or "")
        if gid not in groups:
            warnings.append(f"answers: dropped a paragraph for unknown group {gid!r}")
            continue
        p = paragraph(raw.get("paragraph") or {}, f"answers {gid}")
        if p:
            by_group.setdefault(gid, []).append(p)
    out["answers"] = [
        {"group": gid, "who": groups[gid]["who"], "doc_id": groups[gid]["doc_id"], "paragraphs": by_group[gid]}
        for gid in groups if gid in by_group
    ]
    return out, warnings
