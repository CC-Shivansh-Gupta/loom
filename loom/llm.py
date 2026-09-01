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
                        (`anthropic/claude-opus-5`, `deepseek/deepseek-r1:free`).

The last three are what an OpenRouter key needs; nothing else in the codebase
changes, because the gateway speaks the same Messages API.

A non-Anthropic model behind that gateway works, on reduced terms it declares
rather than hides. `thinking` and `output_config` are Messages API features, so
they are not sent to a model that does not have them (`speaks_anthropic_
extensions`), and a JSON schema stops being enforced and becomes an instruction
in the prompt -- which is why `extract_json` tolerates a markdown fence and why
every caller validates the shape it gets back.

This is the part of the design that pays for itself. Nothing above the provider
boundary knows which model answered, and nothing above it trusts the answer:
`store.grounding_check` still requires every number in a report to occur in the
evidence pack, `improve` still passes proposals through a gate the model cannot
reach, and `loom.aieval` still scores groundedness, abstention and red-team
catches. So "is this free model good enough" is a question with a measured
answer rather than an opinion -- run `python -m loom.aieval` against it.

Every call is logged to `TELEMETRY` (tokens, latency, estimated cost) so
the manager view can show what the AI layer costs to run.
"""
from __future__ import annotations

import json
import os
import re
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
    if model.endswith(":free"):
        return (0.0, 0.0)       # known to be free, as opposed to unknown
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


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Parse JSON out of a model response that may not be only JSON.

    With schema-constrained output this is `json.loads` and nothing else. Take
    that away -- any model that cannot enforce a schema -- and the same request
    comes back fenced in markdown, or with a sentence of preamble, or with the
    object followed by an explanation. All three are still the right answer
    wrapped in politeness, and all three make `json.loads` raise.

    Three attempts, cheapest first, then give up honestly. Never a regex that
    *builds* the object: this reads what the model produced or fails, it does
    not invent structure the model did not supply.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # the outermost balanced {...} or [...], scanned with string-awareness so a
    # brace inside a quoted value does not end the object early
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start >= 0:
        opens, closes = {"{": "}", "[": "]"}, []
        depth, in_str, esc = 0, False, False
        for i, ch in enumerate(text[start:], start):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in opens:
                closes.append(opens[ch])
                depth += 1
            elif closes and ch == closes[-1]:
                closes.pop()
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError(f"no JSON object in model response: {text[:200]!r}")


def speaks_anthropic_extensions(model: str) -> bool:
    """Whether `thinking` and `output_config` mean anything to this model.

    They are Messages API features, not HTTP ones. A gateway will happily carry
    them to a model that has never heard of them, and what comes back is either
    a 400 or -- worse -- a success with the request silently reinterpreted. The
    reply to that is not to hope, it is to send what the model actually has.
    """
    return "claude" in model.lower()


JSON_INSTRUCTION = (
    "\n\nReply with a single JSON value and nothing else: no markdown fence, no "
    "commentary before or after. It must validate against this schema:\n"
)


class Provider:
    name = "base"

    def complete(self, purpose: str, system: str, user: str, *,
                 schema: dict | None = None, max_tokens: int = 4000) -> str:
        raise NotImplementedError

    def complete_json(self, purpose: str, system: str, user: str, schema: dict,
                      max_tokens: int = 4000) -> Any:
        text = self.complete(purpose, system, user, schema=schema, max_tokens=max_tokens)
        return extract_json(text)


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
        self.extensions = speaks_anthropic_extensions(model)

    def complete(self, purpose, system, user, *, schema=None, max_tokens=4000):
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if self.extensions:
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": self.effort}
            if schema is not None:
                kwargs["output_config"]["format"] = {"type": "json_schema", "schema": schema}
        elif schema is not None:
            # No schema enforcement on this model, so the schema goes where it
            # can still do work: in the prompt, as an instruction. The result is
            # a request, not a guarantee -- which is why `extract_json` has to
            # cope with a fence and why every caller validates what it gets back
            # rather than trusting the shape.
            kwargs["system"] = system + JSON_INSTRUCTION + json.dumps(schema, indent=1)
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
