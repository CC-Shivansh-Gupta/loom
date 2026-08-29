"""Layer 4: scores the twin against ground truth.

The only module allowed to see both the plant and the twin.
"""
from __future__ import annotations

from dataclasses import dataclass

from .events import BLOCKED
from .plant import Plant
from .twin import INFERRED, MEASURED, Twin


def state_agreement(plant: Plant, twin: Twin) -> dict:
    """Diff belief vs truth now, split by the belief's provenance."""
    truth = plant.truth()
    belief = twin.snapshot()
    mismatches = []
    for sid, ts in truth["stations"].items():
        bs = belief["stations"][sid]
        if ts != bs:
            mismatches.append((sid, "station", belief["provenance"][sid], ts, bs))
        if len(truth["buffers"][sid]) != belief["buffer_counts"][sid]:
            mismatches.append((sid, "buffer", belief["buffer_provenance"][sid],
                               len(truth["buffers"][sid]), belief["buffer_counts"][sid]))
    return {
        "checked": 2 * len(truth["stations"]),
        "mismatches": mismatches,
        "measured_wrong": [m for m in mismatches if m[2] == MEASURED],
        "inferred_wrong": [m for m in mismatches if m[2] == INFERRED],
    }


def inference_accuracy(plant: Plant, twin: Twin) -> dict[str, dict]:
    """Per station: how good the twin's cycle samples are, by provenance."""
    truth: dict[tuple[str, int], float] = {}
    for v in plant.vehicles.values():
        for x in v.record:
            if x.finish_t is not None:
                truth[(x.station, v.id)] = x.finish_t - x.start_t
    out = {}
    for sid, samples in twin.samples.items():
        acc = {MEASURED: [], INFERRED: []}
        for vid, c, src in samples:
            tr = truth.get((sid, vid))
            if tr is not None:
                acc[src].append(abs(c - tr))
        out[sid] = {
            src: {"n": len(xs), "mae": (sum(xs) / len(xs)) if xs else None}
            for src, xs in acc.items()
        }
    return out


@dataclass
class BottleneckScore:
    station: str
    t_ramp_start: float
    t_over_takt: float | None       # when the true cycle crossed takt
    t_upstream_blocked: float | None
    t_alert: float | None
    alert_eta_s: float | None       # ETA claimed at first alert
    alert_conf: float | None = None
    alert_inferred_share: float | None = None

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


@dataclass
class ContainmentScore:
    defect: str
    cause_station: str
    n_defective: int                # ground truth, all vehicles built
    t_drift_start: float | None
    t_drift_alert: float | None
    t_first_fail: float | None      # when inspection first caught one (no-twin baseline)
    t_first_hold: float | None
    hold_size: int
    hold_sure: int
    hold_uncertain: int
    true_positives: int
    recall: float | None            # of all defective vehicles built
    precision: float | None
    blanket_size: int               # every vehicle built at the cause station this shift
    escaped: int                    # defective, left the line, never detected nor held
    detected_at_inspection: int

    @property
    def lag_s(self) -> float | None:
        if self.t_drift_start is None or self.t_first_hold is None:
            return None
        return self.t_first_hold - self.t_drift_start


def hold_precision(plant: Plant, twin: Twin) -> list[tuple]:
    """Per hold: (hold, true positives among sure, among uncertain)."""
    defective = {v.id for v in plant.vehicles.values() if v.defects}
    return [(h, len(set(h.sure) & defective), len(set(h.uncertain) & defective))
            for h in twin.quality.holds]


def containment_scorecard(plant: Plant, twin: Twin) -> list[ContainmentScore]:
    cfg = plant.cfg
    q = twin.quality
    out = []
    for d in cfg.defects:
        cause = d.last_cause_station
        truth = {v.id for v in plant.vehicles.values() if d.name in v.defects}
        drift = next((x for x in cfg.param_drifts if x.station in {c.station for c in d.causes}), None)
        t_drift = drift.at_s if drift else None
        alert = next((a for a in q.drift_log if a.station == cause or
                      a.station in {c.station for c in d.causes}), None)
        holds = [h for h in q.holds if h.station in {c.station for c in d.causes}]
        held = set()
        for h in holds:
            held.update(h.sure, h.uncertain)
        first = holds[0] if holds else None
        t_fail = next((t for v in plant.vehicles.values() for (s, t, r) in v.inspections
                       if s == d.detected_at and r == "fail" and d.name in v.detected), None)
        tp = len(held & truth)
        detected = sum(1 for v in plant.vehicles.values() if d.name in v.detected)
        escaped = sum(1 for v in plant.vehicles.values()
                      if d.name in v.defects and v.exited_t is not None
                      and d.name not in v.detected and v.id not in held)
        # what a plant without genealogy holds: everything built at the cause
        # station since the last known-good point (here: since drift start,
        # or since shift start when there is no drift signal at all)
        since = t_drift if t_drift is not None else 0.0
        blanket = sum(1 for v in plant.vehicles.values()
                      if any(x.station == cause and x.start_t >= since for x in v.record))
        out.append(ContainmentScore(
            d.name, cause, len(truth), t_drift,
            alert.t if alert else None, t_fail, first.t if first else None,
            len(held), sum(len(h.sure) for h in holds), sum(len(h.uncertain) for h in holds),
            tp, (tp / len(truth)) if truth else None, (tp / len(held)) if held else None,
            blanket, escaped, detected))
    return out


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
            first.t if first else None,
            first.alert.eta_s if first else None,
            first.alert.confidence if first else None,
            first.alert.inferred_share if first else None))

    false_alarms = [x for k, x in enumerate(raised) if k not in explained]
    return {"scores": scores, "false_alarms": false_alarms,
            "alerts_raised": len(raised)}
