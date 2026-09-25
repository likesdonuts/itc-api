"""Settling the pairs that need a person, one command at a time.

    python cli.py decide              the pairs waiting, numbered
    python cli.py decide 3            one pair in full, with the model's view
    python cli.py decide 3 same       record an answer, then rebuild

The answer goes into analytics_reference.json, the hand-kept file that wins
over every rule and over the model, so it is kept (and can be edited or
undone there) and applies to every later rebuild:

    same          -> <kind>.merge
    different     -> <kind>.keep_apart
    a-became-b    -> firms.predecessors: b is a's newer name (a firm renamed
    b-became-a       or merged); the two stay separate firms, linked
    split P1 P2.. -> firms.splits: a firm field that names these firms
    one           -> firms.splits: a firm field that is a single firm

Names are written as they appear in the filings (the first spelling in the
pair's context), which normalize to the same key the pair was found by.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..config import DATA_DIR
from ..store import load_json
from . import attorneys, names, reference
from .reference import analytics_dir

Logger = Callable[[str], None]

SECTION = {"firm": "firms", "firm_split": "firms", "company": "companies", "attorney": "attorneys"}
KEY_OF = {"firm": names.firm_key, "company": names.company_key, "attorney": attorneys._key}
PAIR_VERDICTS = {"same", "different"}
FIRM_VERDICTS = {"a-became-b", "b-became-a"}
SPLIT_VERDICTS = {"split", "one"}


class DecideError(ValueError):
    pass


def pending(data_dir: Path = DATA_DIR) -> list[dict[str, Any]]:
    path = analytics_dir(data_dir) / "needs_review.json"
    if not path.exists():
        raise DecideError("No review list yet. Run 'python cli.py analytics' first.")
    return list(load_json(path, {}).get("items") or [])


def verdicts_for(item: dict[str, Any]) -> list[str]:
    if item["kind"] == "firm_split":
        return sorted(SPLIT_VERDICTS)
    if item["kind"] == "firm":
        return sorted(PAIR_VERDICTS) + sorted(FIRM_VERDICTS)
    return sorted(PAIR_VERDICTS)


def _label(item: dict[str, Any], side: str) -> str:
    """The name to show and to write: the first spelling seen, else the key."""
    index = 0 if side == "a" else 1
    context = item.get(side) or {}
    spellings = context.get("spellings") or []
    key = item["keys"][index] if index < len(item["keys"]) else item["keys"][0]
    key_of = KEY_OF.get(item["kind"])
    for spelling in spellings:
        if key_of is None or key_of(spelling) == key:
            return spelling
    return key


QUESTIONS = {
    "firm": "Is this the same law firm? (Or did one name become the other: a rename or merger?)",
    "company": "Is this the same legal entity? (A sister company or subsidiary is 'different'.)",
    "attorney": "Is this the same person?",
    "firm_split": "Does this firm field name several law firms?",
}


def summary_line(number: int, item: dict[str, Any]) -> str:
    if item["kind"] == "firm_split":
        names_text = item["keys"][0]
    else:
        names_text = f"{_label(item, 'a')}  vs  {_label(item, 'b')}"
    model = item.get("model") or {}
    said = f"  [model: {model.get('decision')} {float(model.get('confidence') or 0):.2f}]" if model else ""
    return f"{number:>3}. {item['kind']:<10} {names_text}{said}"


def details(number: int, item: dict[str, Any]) -> list[str]:
    lines = [summary_line(number, item), "", f"     {QUESTIONS.get(item['kind'], item.get('question', ''))}"]
    for side in ("a", "b"):
        context = item.get(side)
        if not context:
            continue
        lines.append(f"     {side}: {_label(item, side)}")
        for field, value in context.items():
            if value in (None, [], {}, ""):
                continue
            if isinstance(value, dict):
                value = ", ".join(f"{k} ({v})" for k, v in value.items())
            elif isinstance(value, list):
                value = "; ".join(str(v) for v in value)
            lines.append(f"        {field}: {value}")
    for field in ("note", "text", "other_candidates"):
        if item.get(field):
            lines.append(f"     {field}: {item[field]}")
    model = item.get("model") or {}
    if model:
        lines.append(
            f"     model ({model.get('model')}): {model.get('decision')}, confidence "
            f"{float(model.get('confidence') or 0):.2f} -- {model.get('reason', '')}"
        )
    else:
        lines.append("     model: not asked")
    lines += ["", f"     Answer with: python cli.py decide {number} " + " | ".join(verdicts_for(item))]
    if item["kind"] == "firm_split":
        lines.append(f'     (split takes the firms: python cli.py decide {number} split "A LLP" "B LLP")')
    return lines


@dataclass
class Recorded:
    section: str
    entry: str
    value: Any


def record(
    item: dict[str, Any],
    verdict: str,
    parts: list[str] | None = None,
    *,
    reference_path: Path | None = None,
) -> Recorded:
    """Write the answer into the reference file. Returns what was written."""
    reference_path = reference_path or reference.REFERENCE_PATH
    verdict = verdict.lower()
    allowed = verdicts_for(item)
    if verdict not in allowed:
        raise DecideError(f"'{verdict}' is not an answer for a {item['kind']} pair; use one of: {', '.join(allowed)}")

    raw = json.loads(reference_path.read_text(encoding="utf-8")) if reference_path.exists() else {}
    section = raw.setdefault(SECTION[item["kind"]], {})

    if item["kind"] == "firm_split":
        field = " ".join(item["keys"][0].split())
        if verdict == "split":
            parts = [p.strip() for p in parts or [] if p.strip()]
            if len(parts) < 2:
                raise DecideError('split needs the firms it names, e.g. split "A LLP" "B LLP"')
        else:
            parts = [field]
        section.setdefault("splits", {})[field] = parts
        recorded = Recorded(f"{SECTION[item['kind']]}.splits", field, parts)
    elif verdict in FIRM_VERDICTS:
        older, newer = (_label(item, "a"), _label(item, "b"))
        if verdict == "b-became-a":
            older, newer = newer, older
        predecessors = section.setdefault("predecessors", {})
        entries = predecessors.setdefault(newer, [])
        if older not in entries:
            entries.append(older)
        recorded = Recorded(f"{SECTION[item['kind']]}.predecessors", newer, entries)
    else:
        entry = "merge" if verdict == "same" else "keep_apart"
        pair = [_label(item, "a"), _label(item, "b")]
        pairs = section.setdefault(entry, [])
        if pair not in pairs and pair[::-1] not in pairs:
            pairs.append(pair)
        recorded = Recorded(f"{SECTION[item['kind']]}.{entry}", " / ".join(pair), pair)

    reference_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return recorded
