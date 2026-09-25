"""The hand-kept reference file and the model's cached review decisions.

analytics_reference.json (repository root, tracked) holds what a person has
decided: firm aliases, predecessor firms, forced kinds, and merge /
keep-apart pairs. data/analytics/review_decisions.json (tracked too, since
each decision was paid for) holds what the model decided about the pairs
the rules could not settle. A person's entry always wins over a model's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import DATA_DIR
from ..store import load_json, save_json

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_PATH = ROOT / "analytics_reference.json"
ANALYTICS_DIR = "analytics"
DECISIONS_FILE = "review_decisions.json"

# A model's "same" or "different" is applied only this sure; below it the
# pair stays in the review list for a person.
MIN_CONFIDENCE = 0.85
# ... and a "same" for two companies where one name has a word the other
# lacks ("Rohm and Haas Electronic Materials" / "... CMP"), which is more
# often a subsidiary than a typo.
MIN_CONFIDENCE_WORD_ADDED = 0.95


class ReferenceError(RuntimeError):
    pass


@dataclass
class Reference:
    raw: dict[str, Any] = field(default_factory=dict)

    def section(self, kind: str) -> dict[str, Any]:
        return self.raw.get(kind) or {}

    def pairs(self, kind: str, name: str) -> list[tuple[str, str]]:
        """merge / keep_apart pairs; a longer list means all of them."""
        out = []
        for group in self.section(kind).get(name) or []:
            group = [str(item) for item in group]
            out.extend((group[0], other) for other in group[1:])
        return out


def load_reference(path: Path = REFERENCE_PATH) -> Reference:
    if not path.exists():
        return Reference()
    try:
        return Reference(raw=json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise ReferenceError(f"{path.name} is not valid JSON: {exc}") from exc


def analytics_dir(data_dir: Path = DATA_DIR) -> Path:
    return Path(data_dir) / ANALYTICS_DIR


@dataclass
class Decisions:
    """The model's answers, by review item id."""

    path: Path
    items: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "Decisions":
        path = analytics_dir(data_dir) / DECISIONS_FILE
        return cls(path=path, items=load_json(path, {}))

    def save(self) -> None:
        save_json(self.path, self.items)

    def get(self, item_id: str) -> dict[str, Any] | None:
        return self.items.get(item_id)

    def verdict(self, item_id: str, *, min_same: float = MIN_CONFIDENCE) -> str | None:
        """ "same", "different" or "split" when the model was sure enough;
        None when it was not asked, unsure, or not sure enough. `min_same`
        raises the bar for "same" on pairs where a wrong merge is likelier.
        """
        decision = self.items.get(item_id)
        verdict = decision.get("decision") if decision else None
        if verdict not in {"same", "different", "split"}:
            return None
        needed = min_same if verdict == "same" else MIN_CONFIDENCE
        return verdict if float(decision.get("confidence") or 0) >= needed else None
