"""What-if engine: recommendations come from simulation, not opinion.

Given the twin's *believed* state (fitted cycles, buffer counts, busy
stations), build a fresh plant with those values, apply a candidate
mitigation, run it forward, and measure. Because every input is a belief,
every output is tagged simulated.

The LLM's role is bounded: it proposes candidates from a fixed menu
(structured output), the simulator judges them, and the LLM explains the
ranked result. The template path proposes candidates by rule.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass

from . import llm
from .config import LineCfg
from .events import BLOCKED
from .plant import Plant
from .twin import Twin

MENU = {
    "none":      "do nothing (baseline)",
    "floater":   "add a second operator/robot assist at `station`: cycle x `factor` (0.6-0.95)",
    "rebalance": "move `seconds` of work content from `station` to `to` (an adjacent station)",
    "buffer":    "add `n` slots to the buffer feeding `station` (needs a maintenance window)",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array", "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["floater", "rebalance", "buffer"]},
                    "station": {"type": "string"},
                    "to": {"type": "string"},
                    "factor": {"type": "number"},
                    "seconds": {"type": "number"},
                    "n": {"type": "integer"},
                    "why": {"type": "string"},
                },
                "required": ["action", "station", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}

PROPOSE_SYSTEM = """You propose mitigations for a vehicle assembly line from a JSON evidence pack.
You may ONLY choose from this menu, and only for stations that exist in the pack:
""" + "\n".join(f"- {k}: {v}" for k, v in MENU.items() if k != "none") + """
Propose at most 4 candidates aimed at the station with the active alert (or the slowest station
relative to takt). Do not estimate their effect; a simulator will measure it. Keep `why` to one line."""

EXPLAIN_SYSTEM = """You explain simulated mitigation results to a line supervisor. Every number must come
from the JSON. Rank as given. Say that the effects are simulated from the twin's believed state,
mention what is inferred rather than measured, and end with one recommended action a person can take."""


@dataclass
class Candidate:
    action: str
    station: str
    to: str | None = None
    factor: float = 0.8
    seconds: float = 5.0
    n: int = 2
    why: str = ""

    def label(self) -> str:
        if self.action == "none":
            return "no action"
        if self.action == "floater":
            return f"floater at {self.station} (cycle x{self.factor})"
        if self.action == "rebalance":
            return f"move {self.seconds:.0f}s of work {self.station} -> {self.to}"
        return f"+{self.n} buffer slots before {self.station}"


@dataclass
class Outcome:
    candidate: Candidate
    veh_per_h: float
    blocked_min: float          # total upstream blocked time over the horizon
    first_block_min: float | None
    lost_slots: int

    def as_dict(self) -> dict:
        return {"mitigation": self.candidate.label(), "why": self.candidate.why,
                "simulated_veh_per_h": round(self.veh_per_h, 1),
                "simulated_upstream_blocked_min": round(self.blocked_min, 1),
                "simulated_first_block_min": None if self.first_block_min is None else round(self.first_block_min, 1),
                "simulated_lost_takt_slots": self.lost_slots}


def _believed_cfg(cfg: LineCfg, twin: Twin, cand: Candidate) -> LineCfg:
    stations = []
    for s in cfg.stations:
        c = twin.stations[s.id].cycle_s.value
        cycle = float(c) if c is not None else s.cycle_s
        buf = s.buffer_before
        if cand.action == "floater" and s.id == cand.station:
            cycle *= cand.factor
        if cand.action == "rebalance":
            if s.id == cand.station:
                cycle = max(1.0, cycle - cand.seconds)
            elif s.id == cand.to:
                cycle += cand.seconds
        if cand.action == "buffer" and s.id == cand.station:
            buf += cand.n
        stations.append(dataclasses.replace(s, cycle_s=cycle, buffer_before=buf))
    # no scheduled faults or drifts: the believed cycles already contain them
    return dataclasses.replace(cfg, stations=tuple(stations), perturbations=(),
                               param_drifts=(), defects=(), sensor_faults=())


def simulate(cfg: LineCfg, twin: Twin, cand: Candidate, horizon_s: float = 1800.0,
             focus: str | None = None) -> Outcome:
    twin.refresh()
    cfg2 = _believed_cfg(cfg, twin, cand)
    plant = Plant(cfg2)
    bufs = {sid: tb.value for sid, tb in twin.buffers.items()}
    busy = {s.id: (0.5 if twin.stations[s.id].state.value != "idle" else None) for s in cfg.stations}
    plant.prime(bufs, busy)
    plant.run(horizon_s)
    hours = horizon_s / 3600
    upstream = set(cfg.ids[:cfg.index(focus)]) if focus else set(cfg.ids)
    blocked = sum(st.time_in["blocked"] for st in plant.stations if st.cfg.id in upstream)
    first = next((e.t for e in plant.events if e.kind == BLOCKED and e.station in upstream), None)
    lost = sum(1 for e in plant.events if e.kind == "lost_slot")
    return Outcome(cand, len(plant.exited) / hours, blocked / 60,
                   None if first is None else first / 60, lost)


def rule_candidates(cfg: LineCfg, twin: Twin, focus: str) -> list[Candidate]:
    i = cfg.index(focus)
    out = [Candidate("floater", focus, factor=0.8, why="second operator absorbs the overrun")]
    neighbours = [cfg.ids[j] for j in (i - 1, i + 1) if 0 <= j < len(cfg.ids)]
    if neighbours:
        # move work to the neighbour with the most slack
        def slack(sid: str) -> float:
            c = twin.stations[sid].cycle_s.value
            return cfg.takt_s - (c if c is not None else cfg.station(sid).cycle_s)
        to = max(neighbours, key=slack)
        out.append(Candidate("rebalance", focus, to=to, seconds=5.0,
                             why=f"{to} has the most slack against takt"))
    out.append(Candidate("buffer", focus, n=2, why="decouple upstream from the slow station"))
    return out


def _parse_candidates(data: dict, cfg: LineCfg) -> list[Candidate]:
    out = []
    for c in data.get("candidates", []):
        if c.get("station") not in cfg.ids or c.get("action") not in MENU:
            continue
        cand = Candidate(c["action"], c["station"], c.get("to"), float(c.get("factor", 0.8)),
                         float(c.get("seconds", 5.0)), int(c.get("n", 2)), c.get("why", ""))
        if cand.action == "rebalance" and cand.to not in cfg.ids:
            continue
        cand.factor = min(0.95, max(0.6, cand.factor))
        out.append(cand)
    return out


def recommend(cfg: LineCfg, twin: Twin, pack: dict, focus: str | None = None,
              provider: llm.Provider | None = None, horizon_s: float = 1800.0) -> dict:
    prov = provider or llm.get_provider()
    twin.refresh()
    if focus is None:
        if twin.active:
            focus = next(iter(twin.active))
        else:
            focus = max(cfg.ids, key=lambda sid: (twin.stations[sid].cycle_s.value or 0) - cfg.takt_s)
    user = json.dumps({"focus_station": focus, "pack": pack}, indent=1)
    if isinstance(prov, llm.TemplateProvider):
        cands = rule_candidates(cfg, twin, focus)
    else:
        cands = _parse_candidates(prov.complete_json("whatif:propose", PROPOSE_SYSTEM, user, SCHEMA), cfg)
        if not cands:
            cands = rule_candidates(cfg, twin, focus)
    outcomes = [simulate(cfg, twin, Candidate("none", focus, why="baseline"), horizon_s, focus)]
    outcomes += [simulate(cfg, twin, c, horizon_s, focus) for c in cands]
    base = outcomes[0]
    ranked = sorted(outcomes[1:], key=lambda o: (-(o.veh_per_h - base.veh_per_h), o.blocked_min))
    result = {
        "focus_station": focus,
        "horizon_min": horizon_s / 60,
        "inputs": "believed cycles (inferred) and buffer counts from the twin; effects are simulated",
        "baseline": base.as_dict(),
        "ranked": [o.as_dict() for o in ranked],
    }
    result["explanation"] = prov.complete("whatif:explain", EXPLAIN_SYSTEM, json.dumps(result, indent=1))
    return result


def _explain_template(user: str) -> str:
    r = json.loads(user)
    b = r["baseline"]
    lines = [f"# What-if at {r['focus_station']} (next {r['horizon_min']:.0f} min, simulated from believed state)",
             f"- Baseline: {b['simulated_veh_per_h']} veh/h, upstream blocked {b['simulated_upstream_blocked_min']} min, "
             f"first block at {b['simulated_first_block_min']} min."]
    for k, o in enumerate(r["ranked"], 1):
        d = o["simulated_veh_per_h"] - b["simulated_veh_per_h"]
        lines.append(f"{k}. {o['mitigation']}: {o['simulated_veh_per_h']} veh/h ({d:+.1f}), blocked "
                     f"{o['simulated_upstream_blocked_min']} min — {o['why']}.")
    if r["ranked"]:
        lines.append(f"- Recommended: {r['ranked'][0]['mitigation']}. Effects are ○ simulated; cycle inputs are ◐ inferred.")
    return "\n".join(lines)


llm.register_template("whatif:explain", _explain_template)
