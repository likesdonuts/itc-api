"""Companies: every party in every case, as the legal entities they are.

Parties come from the IDS case records (complainants, respondents,
intervenors -- every case, not only those with documents) and from the
non-parties counsel.py found in the filings.

- A name listing two companies ("NuRich, LLC and NuRich Accounting, LLC")
  is two companies, when each piece ends in a corporate form.
- A former name (f/k/a, formerly, n/k/a) is the same company: "HydraFacial
  LLC f/k/a Edge Systems LLC" joins the cases filed as Edge Systems LLC.
- A trade name (d/b/a, a/k/a) is shown and searched but never merged on:
  Sam's East and Sam's West both trade as Sam's Club.
- Same core words and the same corporate form is the same company; so is a
  name with no form when only one form of it exists. Different forms are
  different entities (Sony Interactive Entertainment Inc. in Japan, LLC in
  the US), grouped only in the family view.
- Typos merge when every differing word is close to its counterpart and
  none is a place or a number (cluster.compare).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .. import counsel as counsel_rules
from . import names
from .cluster import Clusters, candidate_pairs, compare, rekey
from .reference import MIN_CONFIDENCE_WORD_ADDED, Decisions, Reference
from .review import ReviewItem, settle

# Words two companies' names can differ by without either being a typo:
# the country or region of a subsidiary, an ordinal, a compass point.
PROTECTED_WORDS = {
    "us", "usa", "uk", "hk", "eu", "america", "americas", "europe", "asia", "china", "japan", "korea",
    "germany", "india", "canada", "mexico", "taiwan", "singapore", "australia", "international",
    "east", "west", "north", "south", "i", "ii", "iii", "iv", "v",
}
_ALIAS_MARKER_RE = re.compile(r"\b(d/b/a|dba|f/k/a|fka|a/k/a|aka|n/k/a|formerly|doing business)\b", re.I)


@dataclass
class CompanyEntity:
    id: str
    name: str
    keys: list[str]
    spellings: Counter = field(default_factory=Counter)
    trade_names: set[str] = field(default_factory=set)
    former_names: set[str] = field(default_factory=set)
    participant_ids: set[int] = field(default_factory=set)
    cases: dict[str, set[str]] = field(default_factory=dict)  # case -> roles
    family: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "keys": self.keys,
            "spellings": [{"name": s, "count": n} for s, n in self.spellings.most_common()],
            "former_names": sorted(self.former_names),
            "trade_names": sorted(self.trade_names),
            "participant_ids": sorted(self.participant_ids),
            "family": self.family,
            "cases": [{"case": case, "roles": sorted(roles)} for case, roles in sorted(self.cases.items())],
        }


@dataclass
class CompanyResult:
    entities: dict[str, CompanyEntity]
    # A party's name as the records give it -> the company entities it names
    ids_of_name: dict[str, list[str]]
    review: list[ReviewItem]
    clusters: Clusters


def split_entities(name: str) -> list[str]:
    """ "Medsource International Co., Ltd. and Medsource Factory, Inc." ->
    two companies. Split only when every piece ends in a corporate form and
    the name carries no alias ("X d/b/a Y and Z" is one company's names).
    """
    name = " ".join(str(name or "").split())
    if _ALIAS_MARKER_RE.search(name) or not re.search(r"\band\b", name):
        return [name]
    pieces = counsel_rules.split_parties(name)
    if len(pieces) > 1 and all(names.company_form(piece)[1] in _CLEAR_FORMS for piece in pieces):
        return pieces
    return [name]


# Forms that end a company's name unmistakably; "Spa", "AS" and "SA" are
# also ordinary words ("Sam's Spa and Nail Supply, Inc." is one company).
_CLEAR_FORMS = {
    "inc", "corp", "llc", "ltd", "co", "co ltd", "gmbh", "ag", "bv", "nv", "plc", "lp", "llp",
    "oy", "kk", "sas", "sarl", "srl", "co kg", "gmbh co kg", "pte ltd", "pty ltd", "inc co",
}


def _observations(investigations: dict[str, Any], counsel: dict[str, Any]) -> list[tuple[str, str, str, Any]]:
    """(case, role, name as given, participant id) for every party."""
    out = []
    for case, record in investigations.items():
        for party in counsel_rules.case_participants(record):
            out.append((case, str(party.get("role") or ""), party["name"], party.get("participant_id")))
    for case, built in counsel.items():
        for party in built.get("non_parties") or []:
            out.append((case, "Non-Party", party["name"], None))
    return out


def build(
    investigations: dict[str, Any],
    counsel: dict[str, Any],
    reference: Reference,
    decisions: Decisions,
) -> CompanyResult:
    observations = _observations(investigations, counsel)

    parsed: dict[str, names.CompanyName] = {}
    pieces_of: dict[str, list[str]] = {}
    for _, _, whole, _ in observations:
        if whole in pieces_of:
            continue
        pieces_of[whole] = split_entities(whole)
        for piece in pieces_of[whole]:
            parsed.setdefault(piece, names.parse_company(piece))

    clusters = Clusters()
    key_of = {piece: names.company_key(p.primary) for piece, p in parsed.items()}
    for key in key_of.values():
        clusters.add(key)
    for a, b in reference.pairs("companies", "keep_apart"):
        clusters.keep_apart(names.company_key(a), names.company_key(b))
    for a, b in reference.pairs("companies", "merge"):
        clusters.union(names.company_key(a), names.company_key(b), "reference merge")

    # Former names.
    for piece, name in parsed.items():
        for former in name.former:
            former_key = names.company_key(former)
            if former_key and former_key != key_of[piece]:
                clusters.union(key_of[piece], former_key, "former name", former)

    # A name with no corporate form, when only one form of it exists.
    by_core: dict[str, set[str]] = {}
    for key in list(clusters.parent):
        core, _, form = key.partition(" [")
        by_core.setdefault(core, set()).add(key)
    for core, keys in by_core.items():
        formed = sorted(k for k in keys if k != core)
        if core in keys and len(formed) == 1:
            clusters.union(core, formed[0], "no corporate form given")

    # Typos, within the same corporate form.
    review: list[ReviewItem] = []
    keys = sorted(clusters.parent)
    for a, b, _ in candidate_pairs(keys):
        core_a, _, form_a = a.partition(" [")
        core_b, _, form_b = b.partition(" [")
        if form_a != form_b:
            continue
        similarity = compare(core_a.split(), core_b.split(), protected=PROTECTED_WORDS)
        word_added = len(core_a.split()) != len(core_b.split())
        settle("company", a, b, similarity, clusters, decisions, review,
               context=lambda: _context(a, b, parsed, key_of, observations),
               min_same=MIN_CONFIDENCE_WORD_ADDED if word_added else None)

    # -- entities -----------------------------------------------------------
    entities: dict[str, CompanyEntity] = {}
    entity_of_key: dict[str, str] = {}
    observed = set(key_of.values())
    for root, members in clusters.groups().items():
        if not observed & set(members):
            continue  # only named in the reference file
        entity_id = "co:" + re.sub(r"[^a-z0-9]+", "-", root).strip("-")
        entities[entity_id] = CompanyEntity(id=entity_id, name="", keys=members)
        for member in members:
            entity_of_key[member] = entity_id

    ids_of_name: dict[str, list[str]] = {}
    for case, role, whole, participant_id in observations:
        ids = []
        for piece in pieces_of[whole]:
            entity = entities[entity_of_key[key_of[piece]]]
            entity.spellings[parsed[piece].primary] += 1
            entity.trade_names.update(parsed[piece].trade)
            entity.former_names.update(parsed[piece].former)
            entity.cases.setdefault(case, set()).add(role)
            if participant_id is not None and len(pieces_of[whole]) == 1:
                entity.participant_ids.add(int(participant_id))
            ids.append(entity.id)
        ids_of_name[whole] = list(dict.fromkeys(ids))

    for entity in entities.values():
        # The name now, not a former one; then the commonest, preferring one
        # in proper case and one that gives its corporate form.
        former = {names.company_key(f) for f in entity.former_names}
        current = {s: n for s, n in entity.spellings.items() if names.company_key(s) not in former} or entity.spellings
        ranked = sorted(
            current,
            key=lambda s: (-current[s], s.isupper() or s.islower(), not names.company_form(s)[1], s),
        )
        entity.name = (ranked or [entity.keys[0]])[0]
        entity.former_names -= {entity.name}
    renamed = rekey(entities, "co", names.company_key)
    entities = {entity.id: entity for entity in entities.values()}
    ids_of_name = {name: [renamed[i] for i in ids] for name, ids in ids_of_name.items()}

    # Families: the brand a name leads with, when two or more companies
    # share it; the reference file can name one outright.
    families = {names.company_key(n): f for n, f in (reference.section("companies").get("families") or {}).items()}
    guessed = {eid: names.family_key(names.company_form(e.name)[0]) for eid, e in entities.items()}
    shared = Counter(f for f in guessed.values() if f)
    for eid, entity in entities.items():
        pinned = next((families[k] for k in entity.keys if k in families), None)
        entity.family = pinned or (guessed[eid] if guessed[eid] and shared[guessed[eid]] > 1 else None)

    return CompanyResult(entities=entities, ids_of_name=ids_of_name, review=review, clusters=clusters)


def _context(a: str, b: str, parsed: dict[str, names.CompanyName], key_of: dict[str, str],
             observations: list[tuple[str, str, str, Any]]) -> dict[str, Any]:
    def describe(key: str) -> dict[str, Any]:
        spellings = sorted({p.primary for piece, p in parsed.items() if key_of[piece] == key})
        pieces = {piece for piece in parsed if key_of[piece] == key}
        roles: Counter = Counter()
        cases = set()
        for case, role, whole, _ in observations:
            if whole in pieces or any(piece in whole for piece in pieces):
                roles[role] += 1
                cases.add(case)
        return {"spellings": spellings[:4], "cases": sorted(cases)[:6], "roles": dict(roles)}

    return {"a": describe(a), "b": describe(b)}
