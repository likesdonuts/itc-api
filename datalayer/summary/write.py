"""The summary itself, written by Claude Sonnet 5 from the documents' notes.

The writer sees only the notes -- each numbered ("n7"), with its document,
page and topic -- and the facts that need no reading ("t3" from an order's
title, "c2" from the claims analysis; facts.py), never the filings, and cites
what each paragraph rests on. Code then checks the citations: an unknown id
is dropped, and a paragraph left citing nothing is dropped too, with a
warning. So everything shown traces to a quote on a page, an order's title,
or a checked finding of the claims analysis.
"""

from __future__ import annotations

from typing import Any

SECTIONS = ("about", "allegations", "rulings", "decisions", "standing")

SYSTEM = """You write a case summary of a U.S. International Trade Commission Section 337 investigation. The readers are lawyers who know litigation but not intellectual property law or Section 337.

You are given, each with an id:
- notes (n1, n2, ...) taken from the complaint, the notice of institution, the respondents' answers, the Administrative Law Judge's (ALJ's) summary determination rulings, the final initial determination (final ID), and the Commission's notices and opinions, each with its source and page;
- title facts (t1, t2, ...): what the titles of orders and notices say -- settlements, defaults, the Commission declining to review a ruling, exclusion orders issued;
- claims facts (c1, c2, ...): which patent claims were withdrawn, found invalid or not, found infringed or not, from a checked analysis of the decisions.
Write only from these: every statement must be supported by an item you cite. If they do not say something, leave it out; never add facts, law or background from elsewhere beyond a few words defining a term.

Write:
- headline: one sentence saying who accuses whom of what, over which products -- and, if the case has been decided, the outcome.
- about: one or two paragraphs. The parties; the technology and the products in plain words; the patents (or other rights) and what they cover; what the Commission instituted the investigation on.
- allegations: two to four paragraphs on how the complainant says Section 337 is violated: importation, infringement, its domestic industry, and the relief it asks for.
- answers: for each answer group listed, one paragraph of two to four sentences on that group's main defenses. Use the group's id from the list.
- rulings: the ALJ's rulings on summary determination before the hearing, one to three paragraphs, oldest first: what was asked, what was decided and why, and what it changed. Empty if there are none.
- decisions: the final ID and the Commission's review, in order, two to four paragraphs: what the ALJ found (violation or not; infringement, validity, domestic industry, by patent), what the Commission reviewed and decided, and the remedy. Empty if there are none yet.
- standing: one paragraph on where the case stands: which respondents left and how (settlement, consent order, default), which claims remain or were decided, the remedy in force, or what is still to be decided. Use the status given.

Rules:
- Each paragraph lists in cites the ids of the items it relies on (e.g. ["n3", "t2", "c5"]).
- Attribute allegations and defenses to the parties ("EPC alleges", "Innoscience denies"); state rulings and decisions as the ALJ's or the Commission's ("the ALJ found"). Never say who is right or predict the outcome of anything undecided.
- Plain English. The first time a term of art appears, define it in a few words: domestic industry (the complainant's U.S. activity tied to the patent that Section 337 requires), limited or general exclusion order, cease and desist order, claim, prior art, summary determination, initial determination.
- Refer to patents by their short form after the first mention ("the '294 patent"). Give claim numbers only as the notes and facts give them.
- Plain prose: no headings, lists or markdown. About 500 to 1,200 words in all, depending on how far the case has gone; in a long case, keep to the main findings and leave detail to the cited pages."""

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
        "required": ["headline", "about", "allegations", "answers", "rulings", "decisions", "standing"],  # reading order
        "properties": {
            "headline": {"type": "string"},
            "about": {"type": "array", "items": _PARAGRAPH},
            "allegations": {"type": "array", "items": _PARAGRAPH},
            # In reading order: the model writes them in this order, so
            # nothing early is lost if a long summary reaches the output limit.
            "answers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["group", "paragraph"],
                    "properties": {"group": {"type": "string"}, "paragraph": _PARAGRAPH},
                },
            },
            "rulings": {"type": "array", "items": _PARAGRAPH},
            "decisions": {"type": "array", "items": _PARAGRAPH},
            "standing": {"type": "array", "items": _PARAGRAPH},
        },
    },
}

SOURCE_LABELS = {"complaint": "Complaint", "notice_of_institution": "Notice of institution", "answer": "Answer",
                 "ruling": "ALJ ruling", "final_id": "Final ID", "commission_notice": "Commission notice",
                 "commission_opinion": "Commission opinion"}


def number_facts(titles: list[dict[str, Any]], claims: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The title facts as t1, t2, ... and the claims facts as c1, c2, ..."""
    numbered = {f"t{i}": {**fact, "type": "title"} for i, fact in enumerate(titles, 1)}
    numbered.update({f"c{i}": {**fact, "type": "claims"} for i, fact in enumerate(claims, 1)})
    return numbered


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
                 groups: dict[str, dict[str, Any]], facts: dict[str, dict[str, Any]] | None = None,
                 stage: str = "") -> str:
    stage = f" Stage: {stage}." if stage else ""
    lines = [f"Investigation {key}: {title}. Status: {status or 'unknown'}.{stage}", "", "Answer groups:"]
    lines += [f"- {gid}: {g['who']}" for gid, g in groups.items()] or ["- (none: no answers were read)"]
    lines += ["", "Notes:"]
    for nid, note in numbered.items():
        source = SOURCE_LABELS.get(note["kind"], note["kind"])
        if note["kind"] == "answer":
            gid = next((g for g, v in groups.items() if v["doc_id"] == note["doc_id"]), "")
            source += f" {gid}"
        lines.append(f"[{nid}] ({source}, p. {note['page']}; {note['topic']}) {note['point']}")
    facts = facts or {}
    titles = {k: v for k, v in facts.items() if v["type"] == "title"}
    claims = {k: v for k, v in facts.items() if v["type"] == "claims"}
    if titles:
        lines += ["", "Title facts:"]
        lines += [f"[{fid}] ({f['date']}, {f['label']}) {f['title']}" for fid, f in titles.items()]
    if claims:
        lines += ["", "Claims facts:"]
        lines += [f"[{fid}] ({f['date']}) {f['text']}" for fid, f in claims.items()]
    return "\n".join(lines)


def check(answer: dict[str, Any], numbered: dict[str, Any], groups: dict[str, Any],
          facts: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
    """The summary with only valid citations, and warnings for what was dropped."""
    warnings: list[str] = []
    numbered = {**numbered, **(facts or {})}  # every id a paragraph may cite

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
