"""Attorneys: one person per lawyer, with the firms they were at and when.

Attorneys come from counsel.json -- each representation's team, read from
filings and appearance notices -- together with the firm entities firms.py
found for that representation.

Two spellings are one attorney when the names are compatible (same surname,
first names that agree or one is the other's initial, middle names that
agree wherever both give one: "Deanna T. Okun" / "Deanna Tanner Okun") and:

- they were at the same firm ("same firm"); or
- no other attorney has a clashing name ("one person by name"): lawyers
  move between firms, and with no second John Smith in the records, John
  Smith at two firms is one John Smith; or
- they give the same middle name ("same full name"), even though another
  attorney shares the first and last name.

Otherwise -- a compatible pair at different firms while a clashing name
exists ("Stephen Bosco" and "Stephen P. Bosco" while a "Stephen R. Bosco"
exists) -- the pair goes to review.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from rapidfuzz import fuzz

from . import names
from .cluster import Clusters, rekey
from .reference import Decisions, Reference
from .review import ReviewItem, settle


@dataclass
class _Stint:
    cases: set[str] = field(default_factory=set)
    first: str | None = None
    last: str | None = None

    def add(self, case: str, first: str | None, last: str | None) -> None:
        self.cases.add(case)
        if first:
            self.first = min(filter(None, (self.first, first)))
        if last:
            self.last = max(filter(None, (self.last, last)))


@dataclass
class AttorneyEntity:
    id: str
    name: str
    keys: list[str]
    spellings: Counter = field(default_factory=Counter)
    firms: dict[str | None, _Stint] = field(default_factory=dict)
    cases: set[str] = field(default_factory=set)
    lead_in: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        stints = sorted(self.firms.items(), key=lambda item: (item[1].first or "9999", item[0] or ""))
        return {
            "id": self.id,
            "name": self.name,
            "keys": self.keys,
            "spellings": [{"name": s, "count": n} for s, n in self.spellings.most_common()],
            "firms": [
                {"firm": firm, "cases": sorted(stint.cases), "first": stint.first, "last": stint.last}
                for firm, stint in stints
            ],
            "cases": sorted(self.cases),
            "lead_in": sorted(self.lead_in),
        }


@dataclass
class AttorneyResult:
    entities: dict[str, AttorneyEntity]
    # (case, representation index) -> attorney entity ids
    attorneys_of: dict[tuple[str, int], list[str]]
    review: list[ReviewItem]
    clusters: Clusters


def _key(name: str) -> str:
    person = names.parse_person(name)
    return person.full() if person else " ".join(names.words(name))


def build(
    counsel: dict[str, Any],
    firms_of: dict[tuple[str, int], list[str]],
    reference: Reference,
    decisions: Decisions,
) -> AttorneyResult:
    # (key, firm) observations, with each key's spellings and dates.
    seen: list[tuple[str, str, str | None, str, int, dict[str, Any], dict[str, Any]]] = []
    firms_by_key: dict[str, set[str | None]] = {}
    for case, built in counsel.items():
        for index, rep in enumerate(built.get("representations") or []):
            ids = firms_of.get((case, index)) or []
            # A representation split into several firms does not say which
            # of them each attorney was at.
            firm = ids[0] if len(ids) == 1 else None
            for attorney in rep.get("attorneys") or []:
                key = _key(attorney["name"])
                if not key:
                    continue
                seen.append((key, attorney["name"], firm, case, index, rep, attorney))
                firms_by_key.setdefault(key, set()).add(firm)

    clusters = Clusters()
    for key in firms_by_key:
        clusters.add(key)
    for a, b in reference.pairs("attorneys", "keep_apart"):
        clusters.keep_apart(_key(a), _key(b))
    for a, b in reference.pairs("attorneys", "merge"):
        clusters.union(_key(a), _key(b), "reference merge")

    review: list[ReviewItem] = []
    people = {key: names.parse_person(key) for key in firms_by_key}
    by_surname: dict[str, list[str]] = {}
    for key, person in people.items():
        if person:
            by_surname.setdefault(person.last, []).append(key)

    for surname, keys in by_surname.items():
        keys.sort()
        for a, b in combinations(keys, 2):
            pa, pb = people[a], people[b]
            if not names.compatible_people(pa, pb):
                continue
            shared = (firms_by_key[a] & firms_by_key[b]) - {None}
            if shared:
                clusters.union(a, b, "same firm")
                continue
            clash = any(
                not names.compatible_people(people[c], pa) or not names.compatible_people(people[c], pb)
                for c in keys
                if c not in (a, b) and names.compatible_part(people[c].first, pa.first)
            )
            if not clash:
                clusters.union(a, b, "one person by name")
            elif pa.middles and pa.middles == pb.middles:
                clusters.union(a, b, "same full name")
            else:
                settle("attorney", a, b, None, clusters, decisions, review,
                       context=lambda: _context(a, b, seen))

    # Typos within one firm ("Mareesa Fredrick" / "Mareesa Frederick"): the
    # names differ, so the surname buckets above never compare them.
    by_firm: dict[str, list[str]] = {}
    for key, firms in firms_by_key.items():
        for firm in firms - {None}:
            by_firm.setdefault(firm, []).append(key)
    for firm, keys in by_firm.items():
        for a, b in combinations(sorted(keys), 2):
            pa, pb = people.get(a), people.get(b)
            if not pa or not pb or pa.last == pb.last or pa.first[:1] != pb.first[:1]:
                continue
            if fuzz.ratio(a, b) >= 92 and fuzz.ratio(pa.last, pb.last) >= 80:
                clusters.union(a, b, "typo", f"{pa.last}/{pb.last}")

    # -- entities -----------------------------------------------------------
    entities: dict[str, AttorneyEntity] = {}
    entity_of_key: dict[str, str] = {}
    for root, members in clusters.groups().items():
        if not any(member in firms_by_key for member in members):
            continue  # only named in the reference file
        entity_id = "atty:" + re.sub(r"[^a-z0-9]+", "-", root).strip("-")
        entities[entity_id] = AttorneyEntity(id=entity_id, name="", keys=members)
        for member in members:
            entity_of_key[member] = entity_id

    attorneys_of: dict[tuple[str, int], list[str]] = {}
    for key, spelling, firm, case, index, rep, attorney in seen:
        entity = entities[entity_of_key[key]]
        entity.spellings[spelling] += 1
        entity.cases.add(case)
        entity.firms.setdefault(firm, _Stint()).add(case, rep.get("first_filed"), rep.get("last_filed"))
        if attorney.get("lead"):
            entity.lead_in.add(case)
        attorneys_of.setdefault((case, index), [])
        if entity.id not in attorneys_of[(case, index)]:
            attorneys_of[(case, index)].append(entity.id)

    for entity in entities.values():
        # The commonest spelling; on a tie, the fullest ("Stephen R. Smith").
        entity.name = max(entity.spellings.items(), key=lambda item: (item[1], len(item[0])))[0]
    renamed = rekey(entities, "atty", _key)
    entities = {entity.id: entity for entity in entities.values()}
    attorneys_of = {where: [renamed[i] for i in ids] for where, ids in attorneys_of.items()}
    return AttorneyResult(entities=entities, attorneys_of=attorneys_of, review=review, clusters=clusters)


def _context(a: str, b: str, seen: list[tuple]) -> dict[str, Any]:
    def describe(key: str) -> dict[str, Any]:
        firms: Counter = Counter()
        years: set[str] = set()
        spellings: set[str] = set()
        for k, spelling, firm, case, _, rep, _ in seen:
            if k == key:
                firms[firm or "unknown"] += 1
                spellings.add(spelling)
                if rep.get("first_filed"):
                    years.add(str(rep["first_filed"])[:4])
        return {"spellings": sorted(spellings)[:3], "firms": dict(firms), "years": sorted(years)}

    return {"a": describe(a), "b": describe(b)}
