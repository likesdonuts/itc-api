"""Model extraction: candidate sentences -> raw claim events.

One call per 15-25 candidate sentences from the same document. The model is
Claude Haiku 4.5 (claims_config.json), forced to answer with the
record_claim_events tool, with the system prompt cached. Every response's
usage is priced as it arrives, so a build knows its cost even when it fails
halfway; and before each call the worst case that call could cost is checked
against the budget, so a build stops rather than overspends.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import prompts
from .candidates import Candidate, chunks
from .config import ClaimsConfig

Logger = Callable[[str], None]

# Characters per token, on the low side so the pre-call estimate errs high.
CHARS_PER_TOKEN = 3.0


class BudgetExceeded(RuntimeError):
    pass


class MissingApiKeyError(RuntimeError):
    pass


def api_client(env_path: Path | None = None) -> Any:
    """An Anthropic client using ANTHROPIC_API_KEY from .env, next to the
    EDIS token; the environment or an `ant auth login` profile also work.

    A key that is not scoped to a workspace has to name one on every request:
    ANTHROPIC_WORKSPACE_ID in .env (Console -> Settings -> Workspaces) is
    sent as the anthropic-workspace-id header.
    """
    import anthropic

    from ..client import load_env
    from ..config import ENV_PATH

    env = load_env(env_path or ENV_PATH)
    headers = {"anthropic-workspace-id": env["ANTHROPIC_WORKSPACE_ID"]} if env.get("ANTHROPIC_WORKSPACE_ID") else None
    key = env.get("ANTHROPIC_API_KEY")
    if key:
        return anthropic.Anthropic(api_key=key, max_retries=4, default_headers=headers)
    try:
        return anthropic.Anthropic(max_retries=4, default_headers=headers)
    except anthropic.AnthropicError as exc:
        raise MissingApiKeyError(
            "No Anthropic API key: add ANTHROPIC_API_KEY=sk-ant-... to the .env file"
        ) from exc


@dataclass
class Extractor:
    cfg: ClaimsConfig
    client: Any
    spent_before: float = 0.0  # everything in the cost log before this run
    log: Logger = print
    cost: float = 0.0
    calls: int = 0
    usage: dict[str, int] = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    )

    @property
    def remaining(self) -> float:
        return self.cfg.budget_usd - self.spent_before - self.cost

    def _worst_case(self, user: str, model: str) -> float:
        prompt_tokens = int((len(prompts.SYSTEM) + len(user) + 3000) / CHARS_PER_TOKEN)
        return self.cfg.cost(
            model,
            {"input_tokens": 0, "cache_creation_input_tokens": prompt_tokens, "output_tokens": self.cfg.max_output_tokens},
        )

    def _call(self, user: str, model: str | None = None) -> Any:
        model = model or self.cfg.model
        worst = self._worst_case(user, model)
        if worst > self.remaining:
            raise BudgetExceeded(
                f"the next model call could cost up to ${worst:.4f} and only ${max(self.remaining, 0):.4f} of "
                f"the ${self.cfg.budget_usd:.2f} budget is left (claims_config.json, budget_usd)"
            )
        if self.client is None:
            self.client = api_client()  # only once there is something to send
        response = self.client.messages.create(
            model=model,
            max_tokens=self.cfg.max_output_tokens,
            system=[{"type": "text", "text": prompts.SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=[prompts.TOOL],
            tool_choice={"type": "tool", "name": prompts.TOOL_NAME},
            messages=[{"role": "user", "content": user}],
        )
        self.calls += 1
        self.cost += self.cfg.cost(model, response.usage)
        for name in self.usage:
            self.usage[name] += int(getattr(response.usage, name, 0) or 0)
        return response

    def extract(
        self,
        *,
        investigation: str,
        title: str,
        patents: list[str],
        respondents: list[str],
        document: dict[str, Any],
        candidates: list[Candidate],
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Raw events for one document's candidates, as the model gave them
        (Haiku unless `model` says otherwise -- the second pass uses Sonnet).
        A reply cut off at the output limit is retried in halves.
        """
        events: list[dict[str, Any]] = []
        pending = chunks(candidates, self.cfg.sentences_per_call)
        while pending:
            batch = pending.pop(0)
            user = prompts.user_message(
                investigation=investigation,
                title=title,
                patents=patents,
                respondents=respondents,
                document=document,
                candidates=batch,
            )
            response = self._call(user, model)
            if response.stop_reason == "max_tokens" and len(batch) > 1:
                half = len(batch) // 2
                self.log(f"    reply cut off at the output limit; retrying {len(batch)} sentences in halves")
                pending[:0] = [batch[:half], batch[half:]]
                continue
            tool = next((b for b in response.content if getattr(b, "type", None) == "tool_use"), None)
            if tool is None:
                self.log("    ! the model answered without the tool; those sentences are skipped")
                continue
            events.extend(dict(e) for e in (tool.input or {}).get("events") or [])
        return events
