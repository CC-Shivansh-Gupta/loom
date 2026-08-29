"""Gated self-improvement loop.

    propose -> backtest -> gate -> (accept | reject) -> repeat

A proposer (LLM or rule) reads the trust ledger and the current
parameters and suggests a change. The harness backtests it on recorded
scenarios. The gate accepts only if the false-alarm budget still holds
and lead time did not get worse. Nothing the proposer says changes live
behaviour without passing the same test a person would set.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import harness, llm

FA_BUDGET_PER_8H = 0.2          # < 1 false alarm per five 8 h shifts
LEAD_TOLERANCE_MIN = 0.5

SCHEMA = {
    "type": "object",
    "properties": {
        "change": {
            "type": "object",
            "properties": {k: {"type": "number"} for k in harness.DEFAULT_PARAMS},
            "additionalProperties": False,
        },
        "rationale": {"type": "string"},
    },
    "required": ["change", "rationale"],
    "additionalProperties": False,
}

SYSTEM = """You tune the bottleneck forecaster of a digital twin. You see the current parameters, the
last backtest results, the false-alarm budget, and a ledger of alerts with outcomes.
Parameters: window (cycles in the trend fit), min_tstat (slope significance), min_over_z (standard
errors above takt), raise_after (consecutive positive assessments before raising).
Propose ONE small change (one or two parameters) that could raise lead time without breaking the
false-alarm budget, or lower false alarms without losing lead time. Return only the changed keys."""


@dataclass
class Iteration:
    proposal: dict
    rationale: str
    result: harness.Result
    accepted: bool
    reason: str

    def as_dict(self) -> dict:
        return {"proposal": self.proposal, "rationale": self.rationale,
                "result": self.result.as_dict(), "accepted": self.accepted, "reason": self.reason}


@dataclass
class Run:
    baseline: harness.Result
    iterations: list[Iteration] = field(default_factory=list)
    params: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"baseline": self.baseline.as_dict(), "final_params": self.params,
                "fa_budget_per_8h": FA_BUDGET_PER_8H,
                "iterations": [i.as_dict() for i in self.iterations]}


def gate(cand: harness.Result, best: harness.Result) -> tuple[bool, str]:
    if cand.fa_per_8h > FA_BUDGET_PER_8H:
        return False, f"false alarms {cand.fa_per_8h:.2f}/8h exceed budget {FA_BUDGET_PER_8H}"
    if cand.misses > best.misses:
        return False, f"misses rose from {best.misses} to {cand.misses}"
    cl, bl = cand.mean_lead_min, best.mean_lead_min
    if cl is None or (bl is not None and cl < bl - LEAD_TOLERANCE_MIN):
        return False, f"lead time fell from {bl} to {cl} min"
    if bl is not None and cl <= bl and cand.fa_per_8h >= best.fa_per_8h:
        return False, "no improvement on either metric"
    return True, f"lead {bl} -> {cl} min, false alarms {best.fa_per_8h:.2f} -> {cand.fa_per_8h:.2f}/8h"


def _rule_propose(params: dict, k: int) -> tuple[dict, str]:
    """Deterministic proposer: walk toward earlier warnings, one knob at a time."""
    steps = [
        ({"raise_after": params["raise_after"] - 1}, "raise sooner: one fewer confirming cycle"),
        ({"min_tstat": params["min_tstat"] - 0.5}, "accept a slightly weaker trend"),
        ({"window": params["window"] - 4}, "shorter window reacts faster to a ramp"),
        ({"min_over_z": params["min_over_z"] - 0.5}, "call over-takt with less margin"),
    ]
    change, why = steps[k % len(steps)]
    return harness.clamp({**params, **change}), why


def _llm_propose(prov: llm.Provider, params: dict, run: Run) -> tuple[dict, str]:
    user = json.dumps({
        "current_params": params, "fa_budget_per_8h": FA_BUDGET_PER_8H,
        "baseline": run.baseline.as_dict(),
        "history": [i.as_dict() for i in run.iterations],
        "ledger_sample": [r.__dict__ for r in run.baseline.records[:20]],
        "calibration": harness.calibration(run.baseline.records),
    }, indent=1)
    data = prov.complete_json("improve:propose", SYSTEM, user, SCHEMA)
    change = {k: v for k, v in data.get("change", {}).items() if k in params}
    return harness.clamp({**params, **change}), data.get("rationale", "")


def improve(iterations: int = 3, params: dict | None = None, scenarios=harness.DEFAULT_SCENARIOS,
            provider: llm.Provider | None = None) -> Run:
    prov = provider or llm.get_provider()
    params = harness.clamp({**harness.DEFAULT_PARAMS, **(params or {})})
    run = Run(harness.evaluate(params, scenarios), params=params)
    best = run.baseline
    for k in range(iterations):
        if isinstance(prov, llm.TemplateProvider):
            proposal, why = _rule_propose(run.params, k)
        else:
            proposal, why = _llm_propose(prov, run.params, run)
        if proposal == run.params:
            run.iterations.append(Iteration(proposal, why, best, False, "no change proposed"))
            continue
        res = harness.evaluate(proposal, scenarios)
        ok, reason = gate(res, best)
        run.iterations.append(Iteration(proposal, why, res, ok, reason))
        if ok:
            best, run.params = res, proposal
    return run
