"""The pairs the rules cannot settle, and asking Claude Haiku about them.

The rules merge what is clearly one entity and leave apart what is clearly
not; what is left in between becomes a review item: "Lippes Mathias" vs
"Lipper Mathias", a firm field that may name several firms, "Stephen Bosco"
at one firm and "Stephen P. Bosco" at another while a different Stephen
Bosco exists.

Each item is asked once. The answer is cached by the item's id (a hash of
what was asked), so a rebuild never pays twice, and it is applied only when
the model is sure (reference.MIN_CONFIDENCE); otherwise the item stays in
needs_review.json for a person, who settles it in analytics_reference.json.

Calls are priced as they return and counted against the same budget as the
claims analysis (claims_config.json budget_usd, data/claims_costs.csv, rows
named "analytics-review").
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .reference import Decisions

Logger = Callable[[str], None]

COST_ROW = "analytics-review"
PROMPT_VERSION = 2
ITEMS_PER_CALL = 25
MAX_OUTPUT_TOKENS = 4000


@dataclass
class ReviewItem:
    kind: str  # "firm" | "company" | "attorney" | "firm_split"
    keys: tuple[str, ...]
    question: str
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        # The prompt's version is part of the id, so changing what the model
        # is told asks every pair again rather than reusing old answers.
        text = json.dumps([PROMPT_VERSION, self.kind, sorted(self.keys)], ensure_ascii=False)
        return f"{self.kind}:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]}"

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "keys": list(self.keys), "question": self.question, **self.context}


SYSTEM = """You help deduplicate names in U.S. International Trade Commission Section 337 case records: law firms, companies, and attorneys.

Each item gives two or more names with context (spellings seen, cases, years, firms, parties). Decide:
- "same": they are the same firm, the same company (same legal entity), or the same person.
- "different": they are different.
- "split" (only for kind firm_split): the text names several law firms; list each firm's name in "parts".
- "unsure": the context does not settle it.

Rules:
- Law firms: typos and abbreviations of one firm's name are "same". A firm and the firm it merged into or was renamed to are "different" (they are tracked as predecessor and successor elsewhere).
- Companies: "same" only for the same legal entity written differently: a typo, a spelling or transliteration variant ("Womart" / "Wo Ma Te"), punctuation, or a filler word ("City" in "Shenzhen City X"). A parent and its subsidiary, or the same brand in different countries ("Sony Corp." vs "Sony Electronics Inc."), are "different". A name with an extra word naming a place, division, product line or business unit ("USA", "HK", "Europe Cardiff", "CMP", "RS", "SC", "IVHS", "VC") is a different entity: answer "different" unless the extra word is plainly a typo.
- Attorneys: lawyers move between firms, so different firms alone do not make two people different. Use middle names, dates and firms together.
- Be conservative. A wrong "same" merges two real entities; say "unsure" rather than guess.
- confidence is your probability (0 to 1) that your decision is right."""

TOOL_NAME = "record_decisions"
TOOL = {
    "name": TOOL_NAME,
    "description": "Record a decision for every item.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["decisions"],
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "decision", "confidence", "reason", "parts"],
                    "properties": {
                        "id": {"type": "string"},
                        "decision": {"type": "string", "enum": ["same", "different", "split", "unsure"]},
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                        "parts": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        },
    },
}


def settle(
    kind: str,
    a: str,
    b: str,
    similarity: Any,
    clusters: Any,
    decisions: Decisions,
    review: list[ReviewItem],
    *,
    context: Any,
    rule: str = "typo",
    min_same: float | None = None,
) -> None:
    """Merge a clear match, settle a borderline pair from its cached answer,
    queue it when there is none, and leave a clear mismatch alone.

    `similarity` is a cluster.Similarity, or None for a pair that is
    borderline by construction (a short form several firms share).
    """
    if clusters.same(a, b) or clusters.kept_apart(a, b):
        return
    verdict = similarity.verdict if similarity is not None else "review"
    if verdict == "typo":
        clusters.union(a, b, rule, similarity.detail)
        return
    if verdict != "review":
        return
    item = ReviewItem(
        kind=kind,
        keys=(a, b),
        question=f"Are these the same {kind}?",
        context=context() if callable(context) else dict(context or {}),
    )
    answer = decisions.verdict(item.id, **({"min_same": min_same} if min_same else {}))
    if answer == "same":
        clusters.union(a, b, "review: model", decisions.get(item.id).get("reason", ""))
    elif answer == "different":
        clusters.keep_apart(a, b)
    elif all(existing.id != item.id for existing in review):
        review.append(item)


def _user_message(items: list[ReviewItem]) -> str:
    lines = ["Decide each item. Answer with record_decisions, one decision per item id.", ""]
    for item in items:
        lines.append(json.dumps(item.to_dict(), ensure_ascii=False))
    return "\n".join(lines)


@dataclass
class ReviewRun:
    asked: int = 0
    answered: int = 0
    calls: int = 0
    cost: float = 0.0
    stopped: str | None = None


def ask(
    items: list[ReviewItem],
    decisions: Decisions,
    *,
    client: Any = None,
    max_items: int = 300,
    log: Logger = print,
) -> ReviewRun:
    """Ask the model about the items it has not answered yet, up to
    `max_items`, saving each batch's answers as they come.
    """
    from ..claims import config as claims_config
    from ..claims import costs
    from ..claims.extract import CHARS_PER_TOKEN, api_client

    cfg = claims_config.load()
    run = ReviewRun()
    todo = [item for item in items if decisions.get(item.id) is None][:max_items]
    if not todo:
        return run
    spent_before = costs.total(cfg.costs_csv)
    log(f"Review: asking {cfg.model} about {len(todo)} pair(s) the rules could not settle.")

    try:
        for start in range(0, len(todo), ITEMS_PER_CALL):
            batch = todo[start : start + ITEMS_PER_CALL]
            user = _user_message(batch)
            worst = cfg.cost(
                cfg.model,
                {"input_tokens": int((len(SYSTEM) + len(user)) / CHARS_PER_TOKEN), "output_tokens": MAX_OUTPUT_TOKENS},
            )
            if spent_before + run.cost + worst > cfg.budget_usd:
                run.stopped = (
                    f"the ${cfg.budget_usd:.2f} model budget would be exceeded "
                    f"(claims_config.json budget_usd; ${spent_before + run.cost:.2f} spent)"
                )
                log(f"  ! stopped: {run.stopped}")
                break
            if client is None:
                client = api_client()
            response = client.messages.create(
                model=cfg.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                tools=[TOOL],
                tool_choice={"type": "tool", "name": TOOL_NAME},
                messages=[{"role": "user", "content": user}],
            )
            run.calls += 1
            run.asked += len(batch)
            run.cost += cfg.cost(cfg.model, response.usage)
            tool = next((b for b in response.content if getattr(b, "type", None) == "tool_use"), None)
            known = {item.id: item for item in batch}
            for answer in ((tool.input or {}).get("decisions") if tool else None) or []:
                item = known.get(str(answer.get("id")))
                if item is None:
                    continue
                decisions.items[item.id] = {
                    "decision": answer.get("decision"),
                    "confidence": float(answer.get("confidence") or 0),
                    "reason": answer.get("reason") or "",
                    "parts": [str(p) for p in answer.get("parts") or []],
                    "kind": item.kind,
                    "keys": list(item.keys),
                    "model": cfg.model,
                    "decided_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                run.answered += 1
            decisions.save()
            log(f"  {run.answered} of {len(todo)} answered (${run.cost:.4f} so far)")
    finally:
        if run.calls:
            costs.append(cfg.costs_csv, COST_ROW, run.cost)
    return run
