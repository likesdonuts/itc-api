"""Merging spellings into entities, each merge recorded with its reason.

A union-find over string keys. Every union carries the rule that made it
("same key", "typo", "former name", "review: model", "override"), so the
report can show why two spellings count as one, and `keep_apart` pairs from
the reference file (or a model's "different") veto a merge whatever rule
proposed it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from rapidfuzz import fuzz

# Two keys that are this similar, as whole strings, are typo candidates.
TYPO_RATIO = 90.0
# Below the typo ratio but at least this similar: a question for review.
REVIEW_RATIO = 85.0
# Each word that differs between two typo candidates has to be this close to
# its counterpart ("wiitgen"/"wirtgen"), or the pair is two companies that
# share every other word ("shenzhen carku technology"/"shenzhen yark ...").
WORD_TYPO_RATIO = 85.0
WORD_REVIEW_RATIO = 70.0
# Names this short are too easily someone else's to merge on similarity.
SHORT = 10


@dataclass
class Merge:
    a: str
    b: str
    rule: str
    detail: str = ""


@dataclass
class Clusters:
    parent: dict[str, str] = field(default_factory=dict)
    members: dict[str, set[str]] = field(default_factory=dict)
    merges: list[Merge] = field(default_factory=list)
    apart: list[tuple[str, str]] = field(default_factory=list)

    def add(self, key: str) -> None:
        if key not in self.parent:
            self.parent[key] = key
            self.members[key] = {key}

    def find(self, key: str) -> str:
        self.add(key)
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != root:  # path compression
            self.parent[key], key = root, self.parent[key]
        return root

    def keep_apart(self, a: str, b: str) -> None:
        self.add(a)
        self.add(b)
        self.apart.append((a, b))

    def _vetoed(self, ra: str, rb: str) -> bool:
        """A keep-apart pair across the two clusters vetoes joining them."""
        one, other = self.members[ra], self.members[rb]
        return any((x in one and y in other) or (y in one and x in other) for x, y in self.apart)

    def union(self, a: str, b: str, rule: str, detail: str = "") -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb or self._vetoed(ra, rb):
            return False
        # The lexically smaller root wins, so the result does not depend on
        # the order merges were found in.
        low, high = sorted((ra, rb))
        self.parent[high] = low
        self.members[low] |= self.members.pop(high)
        self.merges.append(Merge(a=a, b=b, rule=rule, detail=detail))
        return True

    def same(self, a: str, b: str) -> bool:
        return self.find(a) == self.find(b)

    def kept_apart(self, a: str, b: str) -> bool:
        return bool(self.apart) and self._vetoed(self.find(a), self.find(b))

    def groups(self) -> dict[str, list[str]]:
        return {root: sorted(keys) for root, keys in self.members.items()}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def rekey(entities: dict[str, Any], prefix: str, key_of_name: Any) -> dict[str, str]:
    """Give each entity the id of its display name's key ("firm:kellogg-
    hansen-todd-figel-frederick", not the typo that happened to sort first),
    so ids read well and stay put as spellings come and go. Returns
    old id -> new id; the entities' own `id` is updated.
    """
    renamed: dict[str, str] = {}
    taken: set[str] = set()
    for old_id, entity in sorted(entities.items(), key=lambda item: item[0]):
        new_id = f"{prefix}:{slug(key_of_name(entity.name)) or old_id.split(':', 1)[-1]}"
        while new_id in taken:
            new_id += "-2"
        taken.add(new_id)
        entity.id = new_id
        renamed[old_id] = new_id
    return renamed


def word_diff(a: list[str], b: list[str]) -> list[tuple[str, str]] | None:
    """The words that differ between two names, paired up in order.

    None when they cannot be paired (a different number of differing words):
    one name has a word the other lacks, which is a different company or a
    different form of the name, not a typo.
    """
    if len(a) != len(b):
        return None
    return [(x, y) for x, y in zip(a, b) if x != y]


@dataclass
class Similarity:
    verdict: str  # "typo" | "review" | "different"
    ratio: float
    detail: str


def compare(a: list[str], b: list[str], *, protected: Iterable[str] = ()) -> Similarity:
    """How two names relate, by the words that tell them apart.

    `protected` words can never be a typo of each other (numbers, "us" vs
    "uk", "east" vs "west"): a pair differing in one is different.
    """
    joined_a, joined_b = " ".join(a), " ".join(b)
    ratio = fuzz.ratio(joined_a, joined_b)
    if ratio < REVIEW_RATIO:
        return Similarity("different", ratio, "")
    diff = word_diff(a, b)
    protected = set(protected)
    if diff is None:
        # Words run together or split ("arentfox schiff"/"arentfox schiffllp",
        # "mc auliffe"/"mcauliffe"): compare with the spaces out.
        if "".join(a) == "".join(b):
            return Similarity("typo", 100.0, "spacing")
        # A place added ("... USA Inc.", "... (HK) Co.") is a sister entity.
        extra = set(a) ^ set(b)
        if extra and extra <= protected:
            return Similarity("different", ratio, "differs by " + ", ".join(sorted(extra)))
        return Similarity("review" if ratio >= TYPO_RATIO else "different", ratio, "a word added or dropped")
    if not diff:
        return Similarity("typo", 100.0, "same words")
    worst = min(fuzz.ratio(x, y) for x, y in diff)
    detail = ", ".join(f"{x}/{y}" for x, y in diff)
    if any(x in protected or y in protected or x.isdigit() or y.isdigit() for x, y in diff):
        return Similarity("different", ratio, detail)
    short = min(len(joined_a), len(joined_b)) <= SHORT
    if ratio >= TYPO_RATIO and worst >= WORD_TYPO_RATIO and not short:
        return Similarity("typo", ratio, detail)
    if worst >= WORD_REVIEW_RATIO:
        return Similarity("review", ratio, detail)
    return Similarity("different", ratio, detail)


def candidate_pairs(keys: list[str], *, cutoff: float = REVIEW_RATIO) -> list[tuple[str, str, float]]:
    """Every pair of keys at least `cutoff` similar, via rapidfuzz's matrix
    (fast enough for thousands of names without blocking by first letter,
    which would miss a typo in the first word).
    """
    from rapidfuzz import process

    if len(keys) < 2:
        return []
    pairs = []
    # In row blocks, so the matrix never holds all n x n scores at once.
    for start in range(0, len(keys), 500):
        block = keys[start : start + 500]
        matrix = process.cdist(block, keys, scorer=fuzz.ratio, score_cutoff=cutoff, workers=-1)
        for i, j in zip(*matrix.nonzero()):
            if start + i < j:
                pairs.append((keys[start + i], keys[j], float(matrix[i, j])))
    return pairs
