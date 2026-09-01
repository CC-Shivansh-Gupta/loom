"""LLM provider boundary for the AI layer.

Two providers behind one interface:

  TemplateProvider   deterministic, no network. Every AI feature works
                     without a key so the demo never depends on one.
  ClaudeProvider     Anthropic SDK (`pip install anthropic`), model
                     claude-opus-5, adaptive thinking, optional JSON-schema
                     output. Picked automatically when the SDK is importable
                     and credentials resolve, or forced with LOOM_LLM=claude.

Environment:

  ANTHROPIC_API_KEY     first-party key. Read by the SDK, not by us.
  ANTHROPIC_BASE_URL    point the SDK at a Messages-API-compatible gateway.
  ANTHROPIC_AUTH_TOKEN  bearer credential, which is what a gateway wants
                        instead of `x-api-key`.
  LOOM_LLM_MODEL        model id, for gateways that namespace it
                        (`anthropic/claude-opus-5`).

The last three are what an OpenRouter key needs; nothing else in the codebase
changes, because the gateway speaks the same Messages API. Note that this only
holds for Anthropic models behind it -- `thinking` and `output_config` are
Messages API features, so pointing this provider at a non-Anthropic model is
not a supported configuration, and the template path is the offline fallback
rather than a second model.

Every call is logged to `TELEMETRY` (tokens, latency, estimated cost) so
the manager view can show what the AI layer costs to run.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

MODEL = os.environ.get("LOOM_LLM_MODEL", "claude-opus-5")
PRICE_PER_M = {"claude-opus-5": (5.00, 25.00)}       # $/M input, output


def price_of(model: str) -> tuple[float, float] | None:
    """$/M input and output, or None when we do not know.

    A gateway namespaces the model (`anthropic/claude-opus-5`), which is the
    same model at the same list price, so the vendor prefix is stripped before
    the lookup. Anything still unknown returns None rather than 0.0: this
    number is what the manager view reports as the AI layer's running cost,
    and an unpriced model silently costing $0.0000 is exactly the kind of
    confident-looking wrong number the rest of this system exists to catch.
    """
    if model in PRICE_PER_M:
        return PRICE_PER_M[model]
    return PRICE_PER_M.get(model.rsplit("/", 1)[-1])


@dataclass
class Call:
    purpose: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    cost_usd: float
    priced: bool = True         # False when the model is not in PRICE_PER_M


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
        # Models whose price we do not have, so the total above is a floor and
        # the reader is told which calls it is missing.
        "unpriced_models": sorted({c.model for c in calls if not c.priced}),
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
        price = price_of(self.model)
        pin, pout = price or (0.0, 0.0)
        cost = resp.usage.input_tokens * pin / 1e6 + resp.usage.output_tokens * pout / 1e6
        TELEMETRY.append(Call(purpose, self.name, self.model, resp.usage.input_tokens,
                              resp.usage.output_tokens, latency, cost, price is not None))
        return text


_default: Provider | None = None


def _credentials_visible(client) -> bool:
    """Whether the SDK found something to authenticate with.

    Constructing `Anthropic()` does not need a credential -- the client builds
    happily and raises only when the first request is sent. That is the wrong
    moment for us: auto-detection would choose Claude on the strength of the
    package being importable, and every AI feature would then fail mid-demo
    instead of falling back to the template path that exists for exactly this.
    So the choice is made on whether a credential actually resolved: a key, a
    bearer token, or the token cache an `ant auth login` profile populates.

    Being wrong here is cheap in one direction and expensive in the other. A
    missed credential falls back to the deterministic path and `LOOM_LLM=claude`
    overrides it; a missed *absence* breaks the demo.
    """
    return bool(getattr(client, "api_key", None)
                or getattr(client, "auth_token", None)
                or getattr(client, "_token_cache", None))


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
            if choice == "auto" and not _credentials_visible(prov.client):
                prov = None
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
