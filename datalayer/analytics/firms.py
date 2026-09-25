"""Law firms: from every "firm" a filing names to the firms that exist.

In order:

1. Split. A firm field can name several firms; each piece becomes its own
   firm, acting for the same parties (names.split_firm_field).
2. Key. Each firm is keyed by its core words (names.firm_core), so
   punctuation, "LLP", "The", "(DC)" and "&"/"and" never tell two apart.
3. Merge, each merge with its reason: the reference file's aliases; typos
   (cluster.compare -- whole-name similarity plus every differing word
   close to its counterpart); short forms ("Pillsbury Winthrop" for
   Pillsbury Winthrop Shaw Pittman) when only one firm has that start; and
   the model's review decisions. keep_apart pairs veto any of them.
4. Kind. Not every filer is a law firm: a company filing for itself, a pro
   se individual, a trade group, a government body. They are kept, labeled,
   so the leaderboards can leave them out.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .. import counsel as counsel_rules
from . import names
from .cluster import Clusters, candidate_pairs, compare, rekey
from .reference import Decisions, Reference
from .review import ReviewItem, settle

KINDS = ("law_firm", "self_represented", "organization", "government", "company")

_GOVERNMENT_RE = re.compile(
    r"\b(united states|u\.?s\.? (?:department|house|senate|customs|patent|food)|department of|(?<!law )office of|"
    r"commission|congress|senate|house of representatives|court of|state of|embassy|bureau|"
    r"administration|agency|attorney general)\b",
    re.I,
)
_ORGANIZATION_RE = re.compile(
    r"\b(association|alliance|coalition|council|chamber|foundation|institute|university|college|"
    r"school|society|federation|cooperative|project|club|hospital|center|centre)\b",
    re.I,
)
_LAW_RE = re.compile(r"\b(law|legal|attorneys?|counsel|lawyers?|patent)\b", re.I)
_COMPANY_FORM_RE = re.compile(r"\b(inc|corp|corporation|ltd|limited|co|gmbh|ag)\b\.?,?\s*$", re.I)
# Suffixes only law firms (professional firms) use; "LLC" is everyone's.
_LAW_SUFFIX_RE = re.compile(
    r"\b(?:L\.?\s?L\.?\s?P|P\.?\s?L\.?\s?L\.?\s?C|P\.\s?C|PC|P\.\s?A|PLC|Chtd)\.?(?=[\s,;)]|$)", re.I
)
# Chicago firms end in "Ltd." ("Leydig, Voit & Mayer, Ltd.").
_PARTNERS_LTD_RE = re.compile(r"(&|\band\b|associates).*\bltd\b\.?\s*$", re.I)


@dataclass
class FirmEntity:
    id: str
    name: str
    kind: str
    keys: list[str]
    spellings: Counter = field(default_factory=Counter)
    cases: set[str] = field(default_factory=set)
    predecessors: list[str] = field(default_factory=list)  # entity ids
    successors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "keys": self.keys,
            "spellings": [{"name": s, "filings": n} for s, n in self.spellings.most_common()],
            "cases": sorted(self.cases),
            "predecessors": self.predecessors,
            "successors": self.successors,
        }


@dataclass
class FirmResult:
    entities: dict[str, FirmEntity]
    # (case, representation index) -> the firm entity ids acting in it
    firms_of: dict[tuple[str, int], list[str]]
    review: list[ReviewItem]
    clusters: Clusters
    splits: list[dict[str, Any]]


def _spellings_by_counsel_key(documents: dict[str, list[dict[str, Any]]]) -> dict[str, Counter]:
    """Every raw firm spelling in the filings, grouped the way counsel.json
    groups them (counsel.firm_key), with how many filings used each.
    """
    out: dict[str, Counter] = {}
    for docs in documents.values():
        for doc in docs or []:
            raw = " ".join(str(doc.get("firm_organization") or "").split())
            if raw:
                out.setdefault(counsel_rules.firm_key(raw), Counter())[raw] += 1
    return out


def _display(spellings: Counter) -> str:
    """The name to show: the commonest spelling, preferring one written in
    proper case over the all-lower-case entries of old filings.
    """
    ranked = [s for s, _ in spellings.most_common()]
    cased = [s for s in ranked if not s.islower()]
    return (cased or ranked or [""])[0]


def build(
    counsel: dict[str, Any],
    documents: dict[str, list[dict[str, Any]]],
    reference: Reference,
    decisions: Decisions,
) -> FirmResult:
    raw_spellings = _spellings_by_counsel_key(documents)

    # -- the firm fields, one per representation --------------------------
    fields: dict[tuple[str, int], tuple[str, Counter]] = {}
    field_cases: Counter = Counter()
    for case, built in counsel.items():
        for index, rep in enumerate(built.get("representations") or []):
            spellings = raw_spellings.get(rep.get("firm_key") or "") or Counter({rep["firm"]: 1})
            fields[(case, index)] = (rep["firm"], spellings)
            field_cases[names.firm_key(rep["firm"])] += 1

    # Firms known well enough to split run-together fields by: a spelling
    # with a legal suffix, or acting in three or more cases.
    known_cores = set()
    for firm, spellings in fields.values():
        core = tuple(names.firm_core(firm))
        if any(names.FIRM_SUFFIX_RE.search(s) for s in spellings) or field_cases[" ".join(core)] >= 3:
            known_cores.add(core)
    known = names.KnownFirms(known_cores)

    # -- 1. split -----------------------------------------------------------
    review: list[ReviewItem] = []
    splits: list[dict[str, Any]] = []
    parts_of: dict[tuple[str, int], list[str]] = {}
    key_spellings: dict[str, Counter] = {}
    split_cache: dict[str, list[str]] = {}
    decided_splits = {
        " ".join(str(field_text).split()): [str(p) for p in parts]
        for field_text, parts in (reference.section("firms").get("splits") or {}).items()
    }
    for where, (firm, spellings) in fields.items():
        if firm not in split_cache:
            split_cache[firm] = _split(firm, known, decisions, review, splits, decided_splits)
        parts = split_cache[firm]
        keys = []
        for part in parts:
            key = names.firm_key(part)
            if not key:
                continue
            keys.append(key)
            # A field's spellings belong to the firm when it is one firm; a
            # split field lends each firm only the piece's own text (none
            # for a piece found as a known firm's core -- that firm's own
            # spellings name it).
            target = key_spellings.setdefault(key, Counter())
            if len(parts) == 1:
                target.update(spellings)
            elif part != key:
                target[part] += 1
        parts_of[where] = list(dict.fromkeys(keys))

    # -- 2/3. merge ---------------------------------------------------------
    clusters = Clusters()
    for key in key_spellings:
        clusters.add(key)
    section = reference.section("firms")
    for a, b in reference.pairs("firms", "keep_apart"):
        clusters.keep_apart(names.firm_key(a), names.firm_key(b))
    # A firm and its predecessor are linked, never merged ("Hogan Lovells"
    # would otherwise pass for a short form of Hogan Lovells Cadwalader).
    for successor, predecessors in (section.get("predecessors") or {}).items():
        for predecessor in predecessors:
            clusters.keep_apart(names.firm_key(successor), names.firm_key(predecessor))
    for group in section.get("aliases") or []:
        for other in group[1:]:
            clusters.union(names.firm_key(group[0]), names.firm_key(other), "reference alias")
    for a, b in reference.pairs("firms", "merge"):
        clusters.union(names.firm_key(a), names.firm_key(b), "reference merge")

    keys = sorted(key_spellings)
    for a, b, _ in candidate_pairs(keys):
        settle("firm", a, b, compare(a.split(), b.split()), clusters, decisions, review,
                     context=lambda: _firm_context(a, b, key_spellings, counsel, parts_of))

    # Short forms: "maschoff brennan" for maschoff brennan gilmore israelsen
    # mauriel, when no other firm starts the same way. A name one word
    # longer is more often a merger ("hogan lovells cadwalader") than a
    # long form, so that is asked about rather than merged; so is a longer
    # name with a legal form inside it, which is several firms run together.
    starts: dict[tuple[str, ...], set[str]] = {}
    for key in keys:
        tokens = tuple(key.split())
        for size in range(2, len(tokens)):
            starts.setdefault(tokens[:size], set()).add(key)
    for key in keys:
        size = len(key.split())
        longer = sorted(
            k for k in starts.get(tuple(key.split()), set()) - {key}
            if not set(k.split()[size:]) & (names.FIRM_FORMS | {"law", "firm"})
        )
        roots = {clusters.find(k) for k in longer}
        if not longer:
            continue
        clear = len(roots) == 1 and all(len(k.split()) >= size + 2 for k in longer)
        if clear:
            clusters.union(key, longer[0], "short form")
        else:
            settle("firm", key, longer[0], None, clusters, decisions, review,
                   context=lambda: {"note": "the first name may be a short form of the second, or a different firm "
                                            "(a predecessor, or a firm that merged into it)",
                                    **_firm_context(key, longer[0], key_spellings, counsel, parts_of),
                                    "other_candidates": longer[1:6]})

    # Fragments: a signature block that wraps a firm's name onto two lines
    # leaves its tail as a "firm" ("richter hampton" from Sheppard, Mullin,
    # Richter & Hampton). Folded into the firm it ends, when there is only
    # one such firm and the fragment appears only in cases that firm acts in.
    cases_of: dict[str, set[str]] = {}
    for (case, _), part_keys in parts_of.items():
        for key in part_keys:
            cases_of.setdefault(key, set()).add(case)
    for key in keys:
        tokens = key.split()
        ends = [
            other for other in keys
            if other != key and len(other.split()) >= len(tokens) + 2 and other.split()[-len(tokens):] == tokens
        ]
        if len(ends) == 1 and cases_of.get(key, set()) <= cases_of.get(ends[0], set()):
            clusters.union(key, ends[0], "fragment", "the end of the firm's name, alongside it in every case")

    # -- entities -----------------------------------------------------------
    entities: dict[str, FirmEntity] = {}
    entity_of_key: dict[str, str] = {}
    for root, members in clusters.groups().items():
        # A name only the reference file mentions is not a firm in the data.
        if not any(member in key_spellings for member in members):
            continue
        spellings: Counter = Counter()
        for member in members:
            spellings.update(key_spellings.get(member) or Counter({member.title(): 1}))
        name = _display(spellings)
        entity_id = "firm:" + root.replace(" ", "-")
        entities[entity_id] = FirmEntity(id=entity_id, name=name, kind="law_firm", keys=members, spellings=spellings)
        for member in members:
            entity_of_key[member] = entity_id
    renamed = rekey(entities, "firm", names.firm_key)
    entities = {entity.id: entity for entity in entities.values()}
    entity_of_key = {key: renamed[old] for key, old in entity_of_key.items()}

    firms_of = {
        where: list(dict.fromkeys(entity_of_key[k] for k in keys if k in entity_of_key))
        for where, keys in parts_of.items()
    }
    for (case, index), ids in firms_of.items():
        for entity_id in ids:
            entities[entity_id].cases.add(case)

    # -- 4. kind, and predecessor links ------------------------------------
    kinds = {names.firm_key(n): k for n, k in (section.get("kinds") or {}).items()}
    for entity in entities.values():
        forced = next((kinds[k] for k in entity.keys if k in kinds), None)
        entity.kind = forced or _kind(entity, counsel, firms_of)
    for successor, predecessors in (section.get("predecessors") or {}).items():
        after = entity_of_key.get(names.firm_key(successor))
        for predecessor in predecessors:
            before = entity_of_key.get(names.firm_key(predecessor))
            if after and before and after != before:
                entities[after].predecessors.append(before)
                entities[before].successors.append(after)

    return FirmResult(entities=entities, firms_of=firms_of, review=review, clusters=clusters, splits=splits)


def _split(
    firm: str,
    known: names.KnownFirms,
    decisions: Decisions,
    review: list[ReviewItem],
    splits: list[dict[str, Any]],
    decided: dict[str, list[str]] | None = None,
) -> list[str]:
    result = names.split_firm_field(firm, known)
    parts = result.parts
    # A person's answer (analytics_reference.json firms.splits) wins.
    by_hand = (decided or {}).get(" ".join(firm.split()))
    if by_hand:
        parts = list(by_hand)
        if len(parts) > 1:
            splits.append({"field": firm, "parts": parts, "rule": "reference split"})
    elif result.review:
        item = ReviewItem(
            kind="firm_split",
            keys=(firm,),
            question="Does this firm field name more than one law firm? If so, list each firm.",
            context={"text": firm},
        )
        if decisions.verdict(item.id) == "split":
            parts = decisions.get(item.id)["parts"] or parts
            splits.append({"field": firm, "parts": parts, "rule": "review: model"})
        elif decisions.verdict(item.id) is None:
            review.append(item)
    elif result.rule:
        splits.append({"field": firm, "parts": parts, "rule": result.rule})
    # "Paul N. Unmack d/b/a ARPC LLC" is ARPC LLC.
    cleaned = []
    for part in parts:
        parsed = names.parse_company(part)
        cleaned.append(parsed.trade[0] if parsed.trade else part)
    return list(dict.fromkeys(cleaned))


def _firm_context(a: str, b: str, key_spellings: dict[str, Counter], counsel: dict[str, Any],
                  parts_of: dict[tuple[str, int], list[str]]) -> dict[str, Any]:
    def describe(key: str) -> dict[str, Any]:
        cases = sorted({case for (case, _), keys in parts_of.items() if key in keys})
        attorneys: Counter = Counter()
        for (case, index), keys in parts_of.items():
            if key in keys:
                for attorney in counsel[case]["representations"][index].get("attorneys") or []:
                    attorneys[attorney["name"]] += 1
        return {
            "spellings": [s for s, _ in (key_spellings.get(key) or Counter()).most_common(4)],
            "cases": len(cases),
            "attorneys": [n for n, _ in attorneys.most_common(5)],
        }

    return {"a": describe(a), "b": describe(b)}


def _kind(entity: FirmEntity, counsel: dict[str, Any], firms_of: dict[tuple[str, int], list[str]]) -> str:
    """What kind of filer this is, from its name and whom it acts for."""
    text = " ".join(entity.spellings) or entity.name
    has_suffix = any(names.FIRM_SUFFIX_RE.search(s) for s in entity.spellings)
    # A law firm by its name, even in a case where it acted for itself (a
    # firm subpoenaed as a non-party).
    if (_LAW_RE.search(text) and not _GOVERNMENT_RE.search(text)) or any(
        _LAW_SUFFIX_RE.search(s) or _PARTNERS_LTD_RE.search(s) for s in entity.spellings
    ):
        return "law_firm"
    # Acting for itself: its name is a party it represents (a company's own
    # filing, or a pro se individual), or its family's (in-house counsel of
    # a parent or sister company).
    core = " ".join(names.firm_core(entity.name))
    # Only a filer named like a company can be a sister company's in-house
    # counsel; "Fish & Richardson" is not Fish Inc.'s.
    firm_family = (
        names.family_key(names.company_form(entity.name)[0])
        if _COMPANY_FORM_RE.search(entity.name) and not has_suffix
        else None
    )
    for (case, index), ids in firms_of.items():
        if entity.id not in ids:
            continue
        for party in counsel[case]["representations"][index].get("parties") or []:
            party_core = " ".join(names.company_form(party.get("name"))[0])
            if party_core == core or (
                firm_family and firm_family == names.family_key(names.company_form(party.get("name"))[0])
            ):
                return "self_represented"
    if _GOVERNMENT_RE.search(text):
        return "government"
    if _ORGANIZATION_RE.search(text) and not has_suffix:
        return "organization"
    if _COMPANY_FORM_RE.search(entity.name) and not has_suffix:
        return "company"
    return "law_firm"
