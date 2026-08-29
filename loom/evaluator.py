"""Layer 4: scores the twin against ground truth.

The only module allowed to see both the plant and the twin.
"""
from __future__ import annotations

from dataclasses import dataclass

from .events import BLOCKED
from .plant import Plant
from .twin import Twin


def state_agreement(plant: Plant, twin: Twin) -> dict:
    truth = plant.truth()
    belief = twin.snapshot()
    mismatches = []
    for sid, ts in truth["stations"].items():
        bs = belief["stations"][sid]
        if ts != bs:
            mismatches.append((sid, "station", ts, bs))
        if len(truth["buffers"][sid]) != belief["buffer_counts"][sid]:
            mismatches.append((sid, "buffer", len(truth["buffers"][sid]),
                               belief["buffer_counts"][sid]))
    return {"checked": 2 * len(truth["stations"]), "mismatches": mismatches}


@dataclass
class BottleneckScore:
    station: str
    t_ramp_start: float
    t_over_takt: float | None       # when the true cycle crossed takt
    t_upstream_blocked: float | None
    t_alert: float | None
    alert_eta_s: float | None       # ETA claimed at first alert

    @property
    def lead_s(self) -> float | None:
        if self.t_upstream_blocked is None or self.t_alert is None:
            return None
        return self.t_upstream_blocked - self.t_alert

    @property
    def eta_error_s(self) -> float | None:
        if self.lead_s is None or self.alert_eta_s is None:
            return None
        return self.alert_eta_s - self.lead_s


def bottleneck_scorecard(plant: Plant, twin: Twin) -> dict:
    cfg = plant.cfg
    takt = cfg.takt_s
    raised = [x for x in twin.log if x.action == "raised"]
    scores: list[BottleneckScore] = []
    explained: set[int] = set()

    for p in cfg.perturbations:
        i = cfg.index(p.station)
        base = plant.stations[i].cfg.cycle_s
        t_over = None
        if p.cycle_s > takt:
            if base >= takt or p.ramp_s <= 0:
                t_over = p.at_s
            else:
                t_over = p.at_s + p.ramp_s * (takt - base) / (p.cycle_s - base)
        upstream = set(cfg.ids[:i])
        t_block = next((e.t for e in plant.events
                        if e.kind == BLOCKED and e.station in upstream and e.t >= p.at_s),
                       None)
        first = None
        for k, x in enumerate(raised):
            if x.alert.station == p.station and x.t >= p.at_s:
                first = x
                explained.add(k)
                break
        scores.append(BottleneckScore(
            p.station, p.at_s, t_over, t_block,
            first.t if first else None, first.alert.eta_s if first else None))

    false_alarms = [x for k, x in enumerate(raised) if k not in explained]
    return {"scores": scores, "false_alarms": false_alarms,
            "alerts_raised": len(raised)}
