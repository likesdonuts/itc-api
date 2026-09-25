"""claims_config.json: the parts of the claims analysis meant to be tuned."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import ROOT

CONFIG_PATH = ROOT / "claims_config.json"


class ClaimsConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceKind:
    name: str
    document_types: frozenset[str]
    title_patterns: tuple[re.Pattern[str], ...]

    def matches(self, document: dict[str, Any]) -> bool:
        if document.get("document_type") in self.document_types:
            return True
        title = str(document.get("title") or "")
        return any(pattern.search(title) for pattern in self.title_patterns)


@dataclass(frozen=True)
class ClaimsConfig:
    pipeline_version: str
    source_kinds: tuple[SourceKind, ...]
    ruling_keywords: tuple[str, ...] = ()
    argument_keywords: tuple[str, ...] = ()
    costs_csv: Path = ROOT / "data" / "claims_costs.csv"
    budget_usd: float = 20.0
    second_pass: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def source_kind(self, document: dict[str, Any]) -> str | None:
        """Which kind of source document this is, or None if the analysis
        does not read it. Only public documents are ever sources.
        """
        if str(document.get("security_level") or "").lower() != "public":
            return None
        for kind in self.source_kinds:
            if kind.matches(document):
                return kind.name
        return None


def load(path: Path = CONFIG_PATH) -> ClaimsConfig:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ClaimsConfigError(f"no claims config at {path}") from exc
    except json.JSONDecodeError as exc:
        raise ClaimsConfigError(f"{Path(path).name} is not valid JSON: {exc}") from exc

    kinds = []
    for name, spec in (raw.get("source_documents") or {}).items():
        try:
            patterns = tuple(re.compile(p, re.I) for p in spec.get("title_patterns") or ())
        except re.error as exc:
            raise ClaimsConfigError(f"source kind {name!r} has a bad title pattern: {exc}") from exc
        kinds.append(SourceKind(name, frozenset(spec.get("document_types") or ()), patterns))

    costs = Path(raw.get("costs_csv") or "data/claims_costs.csv")
    return ClaimsConfig(
        pipeline_version=str(raw.get("pipeline_version") or "1.0"),
        source_kinds=tuple(kinds),
        ruling_keywords=tuple(raw.get("ruling_keywords") or ()),
        argument_keywords=tuple(raw.get("argument_keywords") or ()),
        costs_csv=costs if costs.is_absolute() else ROOT / costs,
        budget_usd=float(raw.get("budget_usd") or 0),
        second_pass=bool(raw.get("second_pass")),
        raw=raw,
    )
