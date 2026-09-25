"""The analytics run: data/ in, data/analytics/ out.

    firms.json            one entry per firm, with its spellings, kind and cases
    attorneys.json        one per attorney, with the firms they were at, when
    companies.json        one per company, with former and trade names
    representations.json  who acted for whom in each case, by entity id
    needs_review.json     pairs neither the rules nor the model settled
    report.md             the match-quality report
    meta.json             when it was built, and the counts
    review_decisions.json the model's answers (tracked: each was paid for)

Resolution runs twice when there is something to ask: once to find the
pairs the rules cannot settle, then -- after the model has answered them --
again with those answers applied. Every run is rebuilt in full; nothing but
the decisions carries over.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..store import Store, save_json
from . import attorneys, companies, firms, report
from .reference import Decisions, analytics_dir, load_reference
from .review import ReviewItem, ReviewRun, ask

Logger = Callable[[str], None]

MAX_REVIEW_ITEMS = 300


@dataclass
class Resolved:
    firms: firms.FirmResult
    companies: companies.CompanyResult
    attorneys: attorneys.AttorneyResult

    @property
    def review(self) -> list[ReviewItem]:
        return self.firms.review + self.companies.review + self.attorneys.review


@dataclass
class AnalyticsReport:
    firms: int = 0
    attorneys: int = 0
    companies: int = 0
    representations: int = 0
    needs_review: int = 0
    review: ReviewRun = field(default_factory=ReviewRun)
    seconds: float = 0.0
    out_dir: Path | None = None


def resolve(store: Store, reference: Any, decisions: Decisions) -> Resolved:
    firm_result = firms.build(store.counsel, store.documents, reference, decisions)
    company_result = companies.build(store.investigations, store.counsel, reference, decisions)
    attorney_result = attorneys.build(store.counsel, firm_result.firms_of, reference, decisions)
    return Resolved(firms=firm_result, companies=company_result, attorneys=attorney_result)


def representations(store: Store, resolved: Resolved) -> list[dict[str, Any]]:
    """Each case's representations, by entity id: the firms, the attorneys,
    and the companies they acted for, with each company's role in the case.
    """
    out = []
    for case, built in sorted(store.counsel.items()):
        for index, rep in enumerate(built.get("representations") or []):
            parties = []
            for party in rep.get("parties") or []:
                for company in resolved.companies.ids_of_name.get(party.get("name"), []):
                    parties.append({"company": company, "role": party.get("role")})
            out.append({
                "case": case,
                "firms": resolved.firms.firms_of.get((case, index), []),
                "attorneys": resolved.attorneys.attorneys_of.get((case, index), []),
                "parties": parties,
                "roles": rep.get("roles") or [],
                "first_filed": rep.get("first_filed"),
                "last_filed": rep.get("last_filed"),
                "filings": rep.get("filings", 0),
            })
    return out


def run(
    store: Store,
    *,
    review: bool = True,
    max_review_items: int = MAX_REVIEW_ITEMS,
    client: Any = None,
    log: Logger = print,
) -> AnalyticsReport:
    started = time.monotonic()
    reference = load_reference()
    decisions = Decisions.load(store.data_dir)
    out = analytics_dir(store.data_dir)

    resolved = resolve(store, reference, decisions)
    result = AnalyticsReport(out_dir=out)
    if review and resolved.review:
        result.review = ask(resolved.review, decisions, client=client, max_items=max_review_items, log=log)
        if result.review.answered:
            resolved = resolve(store, reference, decisions)

    reps = representations(store, resolved)
    pending = [
        {**item.to_dict(), "model": decisions.get(item.id)}
        for item in resolved.review
    ]
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_json(out / "firms.json", {"built_at": built_at, "firms": [e.to_dict() for e in _sorted(resolved.firms.entities)]})
    save_json(out / "attorneys.json", {"built_at": built_at, "attorneys": [e.to_dict() for e in _sorted(resolved.attorneys.entities)]})
    save_json(out / "companies.json", {"built_at": built_at, "companies": [e.to_dict() for e in _sorted(resolved.companies.entities)]})
    save_json(out / "representations.json", {"built_at": built_at, "representations": reps})
    save_json(out / "needs_review.json", {"built_at": built_at, "items": pending})
    (out / "report.md").write_text(
        report.markdown(resolved, reps, pending, result.review, built_at=built_at), encoding="utf-8"
    )

    result.firms = len(resolved.firms.entities)
    result.attorneys = len(resolved.attorneys.entities)
    result.companies = len(resolved.companies.entities)
    result.representations = len(reps)
    result.needs_review = len(pending)
    result.seconds = round(time.monotonic() - started, 1)

    # Its own record, not state.json: the analytics app is a separate process
    # from the tracker, which rewrites state.json while its jobs run.
    save_json(out / "meta.json", {
        "built_at": built_at,
        "firms": result.firms,
        "attorneys": result.attorneys,
        "companies": result.companies,
        "representations": result.representations,
        "cases_with_counsel": len({r["case"] for r in reps}),
        "needs_review": result.needs_review,
        "review_cost_usd": round(result.review.cost, 6),
        "seconds": result.seconds,
    })
    log(
        f"Analytics: {result.firms} firms, {result.attorneys} attorneys, {result.companies} companies "
        f"from {result.representations} representations ({result.seconds}s)."
    )
    if result.review.calls:
        log(f"  Review: {result.review.answered} pair(s) answered by the model for ${result.review.cost:.4f}.")
    if pending:
        log(f"  {len(pending)} pair(s) still need a person: see {out / 'needs_review.json'}.")
    log(f"  Match-quality report: {out / 'report.md'}")
    return result


def _sorted(entities: dict[str, Any]) -> list[Any]:
    return sorted(entities.values(), key=lambda e: (-len(e.cases), e.name.lower()))
