"""The optional second pass: flagged events to a stronger model.

Off by default (claims_config.json, "second_pass"). When on, an event Haiku
returned that failed an output check -- a quote not in its sentence, a
claim list that does not parse, a patent not on the record -- goes to
Claude Sonnet with the same sentence. Sonnet's answer is checked exactly like
Haiku's, and replaces the flagged event only if it passes. Either way the
event is tried once: a replacement is kept by later updates like any other
event, and one that could not be fixed is marked so it is not paid for again.

Its calls go through the same extractor, so they are priced (at Sonnet's
rates) into the build's cost and stopped by the same budget.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from . import extract, validate
from .candidates import Candidate
from .config import ClaimsConfig

Logger = Callable[[str], None]


def eligible(event: dict[str, Any]) -> bool:
    return (
        event.get("method") == "haiku"
        and event.get("status") == "needs_review"
        and not event.get("second_pass_tried")
        and bool(event.get("sentence"))
    )


def run(
    events: list[dict[str, Any]],
    extractor: extract.Extractor,
    cfg: ClaimsConfig,
    *,
    investigation: str,
    title: str,
    patents: list[str],
    respondents: list[str],
    documents: dict[str, dict[str, Any]],
    log: Logger = print,
) -> dict[str, int]:
    """Re-extract flagged events' sentences with the second-pass model,
    in place. Returns counts of what was fixed and what was not."""
    flagged = [e for e in events if eligible(e)]
    if not flagged:
        return {"sent": 0, "fixed": 0, "unresolved": 0}

    by_document: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in flagged:
        by_document[(event.get("source") or {}).get("id")].append(event)

    fixed = unresolved = 0
    log(f"  Second pass: {len(flagged)} flagged event(s) to {cfg.second_pass_model}")
    for doc_id, group in by_document.items():
        # One sentence per candidate, however many events it gave.
        sentences: dict[str, Candidate] = {}
        for event in group:
            sentence = event["sentence"]
            if sentence not in sentences:
                sentences[sentence] = Candidate(
                    id=f"S{len(sentences) + 1}", doc_id=doc_id, sentence=sentence, context=event.get("context") or ""
                )
        found = list(sentences.values())
        source = group[0].get("source") or {}
        document = documents.get(doc_id) or {"id": doc_id, "title": source.get("title"),
                                            "document_type": source.get("document_type")}
        try:
            raw = extractor.extract(
                investigation=investigation, title=title, patents=patents, respondents=respondents,
                document=document, candidates=found, model=cfg.second_pass_model,
            )
        except extract.BudgetExceeded as exc:
            log(f"  ! second pass stopped at the budget: {exc}")
            break
        replacements, _ = validate.to_events(
            raw, found, document=document, kind=source.get("source_kind") or "",
            patents=patents, respondents=respondents, model=cfg.second_pass_model,
        )
        for sentence, candidate in sentences.items():
            originals = [e for e in group if e["sentence"] == sentence]
            good = [r for r in replacements if r["sentence"] == sentence and r["status"] == "ok"]
            if good:
                for replacement in good:
                    replacement["method"] = "second_pass"
                    replacement["source"]["files"] = source.get("files") or []
                    replacement["notes"] = replacement.get("notes", []) + [
                        f"re-read by {cfg.second_pass_model} after Haiku's reading failed its checks"
                    ]
                for original in originals:
                    events.remove(original)
                events.extend(good)
                fixed += len(originals)
            else:
                for original in originals:
                    original["second_pass_tried"] = True
                    original["notes"] = original.get("notes", []) + [
                        f"{cfg.second_pass_model} could not resolve it either"
                    ]
                unresolved += len(originals)
    log(f"    fixed {fixed}, unresolved {unresolved}")
    return {"sent": len(flagged), "fixed": fixed, "unresolved": unresolved}
