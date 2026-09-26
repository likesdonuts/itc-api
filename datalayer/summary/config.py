"""summary_config.json: the parts of the case summary meant to be tuned."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..claims import config as claims_config
from ..config import ROOT

CONFIG_PATH = ROOT / "summary_config.json"


class SummaryConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReadPages:
    """How much of one kind of document is read: its first and last pages."""

    first: int
    last: int = 0

    def of(self, total: int) -> int:
        return min(max(total, 0), self.first + self.last)


@dataclass(frozen=True)
class SummaryConfig:
    notes_model: str = "claude-haiku-4-5-20251001"
    writer_model: str = "claude-sonnet-5"
    budget_usd: float = 20.0
    costs_csv: Path = ROOT / "data" / "summary_costs.csv"
    max_answer_groups: int = 5
    max_rulings: int = 10
    complaint_body_min_pages: int = 10
    read_pages: dict[str, ReadPages] = field(default_factory=dict)
    tokens_per_page: int = 600
    prompt_tokens: int = 2500
    notes_output_tokens: int = 1500
    writer_prompt_tokens: int = 5000
    writer_output_tokens: int = 3000
    notes_version: int = 1
    notes_max_output_tokens: int = 6000
    writer_max_output_tokens: int = 5000
    prices: dict[str, claims_config.Rates] = field(default_factory=dict)

    def cost(self, model: str, usage: Any) -> float:
        """What one response cost, from its `usage` (cache reads and writes
        at their own rates), as the claims analysis prices it."""
        return claims_config.ClaimsConfig(pipeline_version="", source_kinds=(), prices=self.prices).cost(model, usage)

    def pages_for(self, kind: str) -> ReadPages:
        return self.read_pages.get(kind) or ReadPages(first=20)

    def rates(self, model: str) -> claims_config.Rates:
        try:
            return self.prices[model]
        except KeyError:
            raise SummaryConfigError(
                f"no prices for {model!r} in claims_config.json; add them from Anthropic's pricing page"
            ) from None


def load(path: Path = CONFIG_PATH, *, prices: dict[str, claims_config.Rates] | None = None) -> SummaryConfig:
    try:
        raw: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SummaryConfigError(f"no summary config at {path}") from exc
    except json.JSONDecodeError as exc:
        raise SummaryConfigError(f"{Path(path).name} is not valid JSON: {exc}") from exc

    if prices is None:
        try:
            prices = claims_config.load().prices
        except claims_config.ClaimsConfigError as exc:
            raise SummaryConfigError(f"model prices are read from claims_config.json: {exc}") from exc
    try:
        read_pages = {
            kind: ReadPages(first=int(spec.get("first") or 0), last=int(spec.get("last") or 0))
            for kind, spec in (raw.get("read_pages") or {}).items()
        }
    except (AttributeError, TypeError, ValueError) as exc:
        raise SummaryConfigError(f"read_pages in {Path(path).name} is malformed: {exc}") from exc
    estimate = raw.get("estimate") or {}
    limits = raw.get("max_output_tokens") or {}
    costs = Path(raw.get("costs_csv") or "data/summary_costs.csv")
    return SummaryConfig(
        notes_model=str(raw.get("notes_model") or "claude-haiku-4-5-20251001"),
        writer_model=str(raw.get("writer_model") or "claude-sonnet-5"),
        budget_usd=float(raw.get("budget_usd") or 0),
        costs_csv=costs if costs.is_absolute() else ROOT / costs,
        max_answer_groups=int(raw.get("max_answer_groups") or 5),
        max_rulings=int(raw.get("max_rulings") or 10),
        complaint_body_min_pages=int(raw.get("complaint_body_min_pages") or 10),
        read_pages=read_pages,
        tokens_per_page=int(estimate.get("tokens_per_page") or 600),
        prompt_tokens=int(estimate.get("prompt_tokens") or 2500),
        notes_output_tokens=int(estimate.get("notes_output_tokens") or 1500),
        writer_prompt_tokens=int(estimate.get("writer_prompt_tokens") or 5000),
        writer_output_tokens=int(estimate.get("writer_output_tokens") or 3000),
        notes_version=int(raw.get("notes_version") or 1),
        notes_max_output_tokens=int(limits.get("notes") or 6000),
        writer_max_output_tokens=int(limits.get("writer") or 5000),
        prices=dict(prices),
    )
