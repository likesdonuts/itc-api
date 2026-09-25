"""The match-quality report (data/analytics/report.md).

For checking the resolution by eye: how many spellings became how many
entities, which rule made each kind of merge (with examples to spot-check),
the largest entities with every spelling folded into them, what the model
decided, and what is still waiting for a person.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

SAMPLES = 8


def _merges(clusters: Any) -> list[str]:
    by_rule: dict[str, list[Any]] = {}
    for merge in clusters.merges:
        by_rule.setdefault(merge.rule, []).append(merge)
    lines = ["| Rule | Merges | Examples |", "| --- | --- | --- |"]
    for rule, merges in sorted(by_rule.items(), key=lambda item: -len(item[1])):
        examples = "; ".join(f"{m.a} = {m.b}" for m in merges[:SAMPLES]).replace("|", "/")
        lines.append(f"| {rule} | {len(merges)} | {examples} |")
    return lines if by_rule else ["No merges."]


def _largest(entities: dict[str, Any], count: int = 15) -> list[str]:
    ranked = sorted(entities.values(), key=lambda e: -len(e.cases))[:count]
    lines = ["| Cases | Name | Spellings folded in |", "| --- | --- | --- |"]
    for entity in ranked:
        spellings = [s for s in entity.spellings if s != entity.name][:5]
        lines.append(f"| {len(entity.cases)} | {entity.name} | {'; '.join(spellings) or '-'} |".replace("\n", " "))
    return lines


def markdown(resolved: Any, reps: list[dict[str, Any]], pending: list[dict[str, Any]], review: Any,
             *, built_at: str) -> str:
    firms = resolved.firms
    companies = resolved.companies
    attorneys = resolved.attorneys
    kinds = Counter(e.kind for e in firms.entities.values())
    firm_keys = sum(len(e.keys) for e in firms.entities.values())
    cases_with_counsel = len({r["case"] for r in reps})

    lines = [
        "# Representation analytics: match quality",
        "",
        f"Built {built_at} from {len(reps)} representations in {cases_with_counsel} cases with counsel on file.",
        "",
        "| | Distinct names | Entities |",
        "| --- | --- | --- |",
        f"| Firms (every filer) | {firm_keys} | {len(firms.entities)} |",
        f"| Attorneys | {sum(len(e.keys) for e in attorneys.entities.values())} | {len(attorneys.entities)} |",
        f"| Companies | {sum(len(e.keys) for e in companies.entities.values())} | {len(companies.entities)} |",
        "",
        "Filers by kind: " + ", ".join(f"{k.replace('_', ' ')} {n}" for k, n in kinds.most_common()) + ".",
        "",
        "## Firm fields split into several firms",
        "",
    ]
    if firms.splits:
        lines += ["| Field | Firms | Rule |", "| --- | --- | --- |"]
        for split in firms.splits[:40]:
            lines.append(f"| {split['field']} | {'; '.join(split['parts'])} | {split['rule']} |".replace("\n", " "))
    else:
        lines.append("None.")

    for title, result in (("Firms", firms), ("Attorneys", attorneys), ("Companies", companies)):
        lines += ["", f"## {title}: merges by rule", "", *_merges(result.clusters)]
        lines += ["", f"## {title}: the largest, with what was folded in", "", *_largest(result.entities)]

    non_law = [e for e in firms.entities.values() if e.kind != "law_firm"]
    if non_law:
        lines += ["", "## Filers that are not law firms", "", "Left out of the law-firm leaderboards.", "",
                  "| Kind | Name | Cases |", "| --- | --- | --- |"]
        for entity in sorted(non_law, key=lambda e: (e.kind, e.name)):
            lines.append(f"| {entity.kind.replace('_', ' ')} | {entity.name} | {len(entity.cases)} |")

    lines += ["", "## Model review", ""]
    if review.calls:
        lines.append(
            f"This run asked about {review.asked} pair(s) in {review.calls} call(s), for ${review.cost:.4f}; "
            f"{review.answered} answered."
        )
    else:
        lines.append("No model calls this run (nothing new to ask, or review was off).")
    lines += ["", "## Still needs a person", ""]
    if pending:
        lines.append(
            "Settle these in analytics_reference.json (merge or keep_apart); the model was unsure, "
            "not sure enough, or not asked."
        )
        lines += ["", "| Kind | Names | Model said |", "| --- | --- | --- |"]
        for item in pending[:80]:
            model = item.get("model") or {}
            said = f"{model.get('decision')} ({float(model.get('confidence') or 0):.2f}): {model.get('reason', '')}" if model else "not asked"
            lines.append(f"| {item['kind']} | {' / '.join(item['keys'])} | {said} |".replace("\n", " "))
    else:
        lines.append("Nothing.")
    return "\n".join(lines) + "\n"
