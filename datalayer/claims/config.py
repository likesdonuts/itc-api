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
class Rates:
    """US dollars per million tokens for one model."""

    input: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float
    output: float


@dataclass(frozen=True)
class ClaimsConfig:
    pipeline_version: str
    source_kinds: tuple[SourceKind, ...]
    ruling_keywords: tuple[str, ...] = ()
    argument_keywords: tuple[str, ...] = ()
    position_headings: tuple[re.Pattern[str], ...] = ()
    exclude_titles: tuple[re.Pattern[str], ...] = ()
    complaint_keywords: tuple[str, ...] = ()
    model: str = "claude-haiku-4-5-20251001"
    second_pass_model: str = "claude-sonnet-5"
    prices: dict[str, Rates] = field(default_factory=dict)
    batch_multiplier: float = 0.5
    sentences_per_call: int = 20
    max_sentences_per_build: int = 1200
    max_output_tokens: int = 8000
    ocr_enabled: bool = True
    ocr_scale: float = 2.0
    ocr_complaint_pages: int = 60
    ocr_max_pages: int = 400
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
        if any(p.search(str(document.get("title") or "")) for p in self.exclude_titles):
            return None
        # Only the Commission's and the ALJ's own document types (and the
        # complaint) can be sources: a party's brief or a staff letter that
        # mentions an "initial determination" in its title is not one.
        document_type = document.get("document_type")
        if document_type not in {t for kind in self.source_kinds for t in kind.document_types}:
            return None
        # Among those, a title pattern picks the more specific kind (a Notice
        # titled "Notice of Institution" is the notice of institution) ...
        title = str(document.get("title") or "")
        for kind in self.source_kinds:
            if any(pattern.search(title) for pattern in kind.title_patterns):
                return kind.name
        # ... and otherwise the document type decides.
        for kind in self.source_kinds:
            if document_type in kind.document_types:
                return kind.name
        return None

    def rates(self, model: str) -> Rates:
        try:
            return self.prices[model]
        except KeyError:
            raise ClaimsConfigError(
                f"no prices for {model!r} in claims_config.json; add them from Anthropic's pricing page"
            ) from None

    def cost(self, model: str, usage: Any, *, batch: bool = False) -> float:
        """What one model response cost, from its `usage`: uncached input,
        cache writes (split by lifetime when the API reports it), cache
        reads and output, each at its own rate.
        """
        rates = self.rates(model)

        def get(obj: Any, name: str) -> int:
            value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
            return int(value or 0)

        written = get(usage, "cache_creation_input_tokens")
        breakdown = usage.get("cache_creation") if isinstance(usage, dict) else getattr(usage, "cache_creation", None)
        written_1h = get(breakdown, "ephemeral_1h_input_tokens") if breakdown else 0
        written_5m = written - written_1h
        dollars = (
            get(usage, "input_tokens") * rates.input
            + written_5m * rates.cache_write_5m
            + written_1h * rates.cache_write_1h
            + get(usage, "cache_read_input_tokens") * rates.cache_read
            + get(usage, "output_tokens") * rates.output
        ) / 1_000_000
        return dollars * (self.batch_multiplier if batch else 1.0)


def load(path: Path = CONFIG_PATH) -> ClaimsConfig:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ClaimsConfigError(f"no claims config at {path}") from exc
    except json.JSONDecodeError as exc:
        raise ClaimsConfigError(f"{Path(path).name} is not valid JSON: {exc}") from exc

    def patterns(values: Any, what: str) -> tuple[re.Pattern[str], ...]:
        try:
            return tuple(re.compile(p, re.I) for p in values or ())
        except re.error as exc:
            raise ClaimsConfigError(f"{what} has a bad pattern: {exc}") from exc

    kinds = [
        SourceKind(name, frozenset(spec.get("document_types") or ()), patterns(spec.get("title_patterns"), f"source kind {name!r}"))
        for name, spec in (raw.get("source_documents") or {}).items()
    ]
    try:
        prices = {model: Rates(**{k: float(v) for k, v in table.items()}) for model, table in (raw.get("prices") or {}).items()}
    except TypeError as exc:
        raise ClaimsConfigError(f"a price table in claims_config.json is malformed: {exc}") from exc
    extraction = raw.get("extraction") or {}
    ocr = raw.get("ocr") or {}

    costs = Path(raw.get("costs_csv") or "data/claims_costs.csv")
    return ClaimsConfig(
        pipeline_version=str(raw.get("pipeline_version") or "1.0"),
        source_kinds=tuple(kinds),
        ruling_keywords=tuple(raw.get("ruling_keywords") or ()),
        argument_keywords=tuple(raw.get("argument_keywords") or ()),
        position_headings=patterns(raw.get("position_heading_patterns"), "position_heading_patterns"),
        exclude_titles=patterns(raw.get("exclude_title_patterns"), "exclude_title_patterns"),
        complaint_keywords=tuple(raw.get("complaint_keywords") or ()),
        model=str(raw.get("model") or "claude-haiku-4-5-20251001"),
        second_pass_model=str(raw.get("second_pass_model") or "claude-sonnet-5"),
        prices=prices,
        batch_multiplier=float(raw.get("batch_multiplier") or 0.5),
        sentences_per_call=int(extraction.get("sentences_per_call") or 20),
        max_sentences_per_build=int(extraction.get("max_sentences_per_build") or 1200),
        max_output_tokens=int(extraction.get("max_output_tokens") or 8000),
        ocr_enabled=bool(ocr.get("enabled", True)),
        ocr_scale=float(ocr.get("scale") or 2.0),
        ocr_complaint_pages=int(ocr.get("complaint_pages") or 60),
        ocr_max_pages=int(ocr.get("max_pages") or 400),
        costs_csv=costs if costs.is_absolute() else ROOT / costs,
        budget_usd=float(raw.get("budget_usd") or 0),
        second_pass=bool(raw.get("second_pass")),
        raw=raw,
    )
