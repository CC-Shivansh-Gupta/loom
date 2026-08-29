"""LLM provider boundary for the AI layer.

Two providers behind one interface:

  TemplateProvider   deterministic, no network. Every AI feature works
                     without a key so the demo never depends on one.
  ClaudeProvider     Anthropic SDK (`pip install anthropic`), model
                     claude-opus-5, adaptive thinking, optional JSON-schema
                     output. Picked automatically when the SDK is importable
                     and credentials resolve, or forced with LOOM_LLM=claude.

Every call is logged to `TELEMETRY` (tokens, latency, estimated cost) so
the manager view can show what the AI layer costs to run.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

MODEL = "claude-opus-5"
PRICE_PER_M = {"claude-opus-5": (5.00, 25.00)}       # $/M input, output


@dataclass
class Call:
    purpose: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    cost_usd: float


TELEMETRY: list[Call] = []


def telemetry_summary() -> dict:
    calls = TELEMETRY
    return {
        "calls": len(calls),
        "input_tokens": sum(c.input_tokens for c in calls),
        "output_tokens": sum(c.output_tokens for c in calls),
        "latency_s": round(sum(c.latency_s for c in calls), 2),
        "cost_usd": round(sum(c.cost_usd for c in calls), 4),
        "providers": sorted({c.provider for c in calls}),
    }


class Provider:
    name = "base"

    def complete(self, purpose: str, system: str, user: str, *,
                 schema: dict | None = None, max_tokens: int = 4000) -> str:
        raise NotImplementedError

    def complete_json(self, purpose: str, system: str, user: str, schema: dict,
                      max_tokens: int = 4000) -> Any:
        text = self.complete(purpose, system, user, schema=schema, max_tokens=max_tokens)
        return json.loads(text)


class TemplateProvider(Provider):
    """Deterministic stand-in. `handlers` maps a purpose to a function that
    renders from the *same* structured input the LLM would get."""
    name = "template"

    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[str], str]] = {}

    def complete(self, purpose, system, user, *, schema=None, max_tokens=4000):
        t0 = time.perf_counter()
        fn = self.handlers.get(purpose)
        if fn is None:
            raise KeyError(f"no template handler registered for {purpose!r}")
        out = fn(user)
        TELEMETRY.append(Call(purpose, self.name, "template", len(user) // 4,
                              len(out) // 4, time.perf_counter() - t0, 0.0))
        return out


class ClaudeProvider(Provider):
    name = "claude"

    def __init__(self, model: str = MODEL, effort: str = "medium") -> None:
        import anthropic                                  # lazy: optional dependency
        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort

    def complete(self, purpose, system, user, *, schema=None, max_tokens=4000):
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
        }
        if schema is not None:
            kwargs["output_config"]["format"] = {"type": "json_schema", "schema": schema}
        t0 = time.perf_counter()
        try:
            resp = self.client.messages.create(**kwargs)
        except self._anthropic.RateLimitError as e:
            raise RuntimeError(f"Claude rate limited: {e.message}") from e
        except self._anthropic.APIStatusError as e:
            raise RuntimeError(f"Claude API error {e.status_code}: {e.message}") from e
        except self._anthropic.APIConnectionError as e:
            raise RuntimeError("Claude API unreachable") from e
        latency = time.perf_counter() - t0
        if resp.stop_reason == "refusal":
            raise RuntimeError("Claude declined the request")
        text = "".join(b.text for b in resp.content if b.type == "text")
        pin, pout = PRICE_PER_M.get(self.model, (0.0, 0.0))
        cost = resp.usage.input_tokens * pin / 1e6 + resp.usage.output_tokens * pout / 1e6
        TELEMETRY.append(Call(purpose, self.name, self.model, resp.usage.input_tokens,
                              resp.usage.output_tokens, latency, cost))
        return text


_default: Provider | None = None


def get_provider(force: str | None = None) -> Provider:
    """LOOM_LLM=template|claude overrides auto-detection."""
    global _default
    if _default is not None and force is None:
        return _default
    choice = force or os.environ.get("LOOM_LLM", "auto")
    prov: Provider | None = None
    if choice in ("claude", "auto"):
        try:
            prov = ClaudeProvider()
        except Exception:                                  # no SDK, no credentials
            if choice == "claude":
                raise
    if prov is None:
        prov = TemplateProvider()
    if force is None:
        _default = prov
    return prov


def register_template(purpose: str, fn: Callable[[str], str]) -> None:
    """Modules register their deterministic renderers at import time."""
    prov = get_provider()
    if isinstance(prov, TemplateProvider):
        prov.handlers[purpose] = fn
    _TEMPLATES[purpose] = fn


_TEMPLATES: dict[str, Callable[[str], str]] = {}


def template_provider() -> TemplateProvider:
    p = TemplateProvider()
    p.handlers.update(_TEMPLATES)
    return p
