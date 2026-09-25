"""The data the analytics page draws from, packed small.

Entities are lists and refer to each other by position, so a browser loads
a few megabytes rather than tens and builds its own indexes once:

    cases       [number, title, year, status, open]
    firms       {id, name, kind, spellings, predecessors, successors}
    attorneys   {id, name, spellings, firms: [[firm, first, last]]}
    companies   {id, name, former, trade, family, cases: [[case, roles]]}
    reps        [case, [firm], [attorney], [[company, role]], side]

Roles and sides are one letter: C complainant, R respondent (intervenors
count with respondents: they join a case to defend the accused products),
N non-party, O other. Who a firm or attorney *opposed* is not stored: the
page reads it off the case, as the companies on the other side.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from datalayer.analytics.reference import analytics_dir
from datalayer.backfill import OPEN_STATUSES
from datalayer.store import INVESTIGATIONS_FILE, load_json

ROLE_CODES = {
    "Complainant": "C",
    "Respondent": "R",
    "Intervenor": "R",
    "Non-Party": "N",
    "Non-Party Petitioner": "N",
}


class NoAnalyticsError(RuntimeError):
    pass


def role_code(role: Any) -> str:
    return ROLE_CODES.get(str(role or ""), "O")


def side_of(roles: list[str]) -> str:
    """Which side a representation acted for, from its parties' roles."""
    codes = {role_code(r) for r in roles}
    if codes == {"C"}:
        return "C"
    if codes == {"R"}:
        return "R"
    if codes == {"N"}:
        return "N"
    return "O"


def _year(value: Any) -> int | None:
    text = str(value or "")[:4]
    return int(text) if text.isdigit() else None


def build(data_dir: Path) -> dict[str, Any]:
    out = analytics_dir(data_dir)
    if not (out / "firms.json").exists():
        raise NoAnalyticsError("No analytics yet: run 'python cli.py analytics' (or the app's Rebuild button).")

    firms = load_json(out / "firms.json", {}).get("firms") or []
    attorneys = load_json(out / "attorneys.json", {}).get("attorneys") or []
    companies = load_json(out / "companies.json", {}).get("companies") or []
    reps = load_json(out / "representations.json", {}).get("representations") or []
    pending = load_json(out / "needs_review.json", {}).get("items") or []
    meta = load_json(out / "meta.json", {})
    investigations = load_json(Path(data_dir) / INVESTIGATIONS_FILE, {})

    # Every case any entity mentions, with what the page shows of it.
    numbers = sorted(
        {c["case"] for co in companies for c in co.get("cases") or []}
        | {r["case"] for r in reps}
        | {case for f in firms for case in f.get("cases") or []},
        key=lambda n: [int(p) if p.isdigit() else 0 for p in n.split("-")],
    )
    case_ix = {number: i for i, number in enumerate(numbers)}
    cases = []
    for number in numbers:
        record = investigations.get(number) or {}
        cases.append([
            number,
            record.get("title") or "",
            _year(record.get("date_initiated")),
            record.get("status") or "",
            1 if record.get("status") in OPEN_STATUSES and not record.get("withdrawn") else 0,
        ])

    firm_ix = {f["id"]: i for i, f in enumerate(firms)}
    atty_ix = {a["id"]: i for i, a in enumerate(attorneys)}
    company_ix = {c["id"]: i for i, c in enumerate(companies)}

    packed_firms = [
        {
            "id": f["id"],
            "name": f["name"],
            "kind": f["kind"],
            "spellings": [s["name"] for s in f.get("spellings") or [] if s["name"] != f["name"]][:8],
            "predecessors": [firm_ix[p] for p in f.get("predecessors") or [] if p in firm_ix],
            "successors": [firm_ix[s] for s in f.get("successors") or [] if s in firm_ix],
        }
        for f in firms
    ]
    packed_attorneys = [
        {
            "id": a["id"],
            "name": a["name"],
            "spellings": [s["name"] for s in a.get("spellings") or [] if s["name"] != a["name"]][:5],
            "firms": [[firm_ix.get(s["firm"], -1), s.get("first"), s.get("last")] for s in a.get("firms") or []],
        }
        for a in attorneys
    ]
    packed_companies = [
        {
            "id": c["id"],
            "name": c["name"],
            "former": c.get("former_names") or [],
            "trade": c.get("trade_names") or [],
            "family": c.get("family"),
            "cases": [
                [case_ix[x["case"]], "".join(sorted({role_code(r) for r in x.get("roles") or []}))]
                for x in c.get("cases") or []
                if x["case"] in case_ix
            ],
        }
        for c in companies
    ]
    packed_reps = [
        [
            case_ix[r["case"]],
            [firm_ix[f] for f in r.get("firms") or [] if f in firm_ix],
            [atty_ix[a] for a in r.get("attorneys") or [] if a in atty_ix],
            [[company_ix[p["company"]], role_code(p.get("role"))] for p in r.get("parties") or [] if p["company"] in company_ix],
            side_of(r.get("roles") or [p.get("role") for p in r.get("parties") or []]),
        ]
        for r in reps
        if r["case"] in case_ix
    ]

    return {
        "meta": {
            "built_at": meta.get("built_at"),
            "cases_with_counsel": meta.get("cases_with_counsel") or len({r[0] for r in packed_reps}),
            "cases": len(investigations),
            "review_cost_usd": meta.get("review_cost_usd"),
        },
        "cases": cases,
        "firms": packed_firms,
        "attorneys": packed_attorneys,
        "companies": packed_companies,
        "reps": packed_reps,
        "review": [
            {
                "kind": item["kind"],
                "names": [((item.get(side) or {}).get("spellings") or [key])[0]
                          for side, key in zip(("a", "b"), item["keys"])],
                "model": (item.get("model") or {}).get("decision"),
                "confidence": (item.get("model") or {}).get("confidence"),
            }
            for item in pending
        ],
    }
