"""Layer 4: scores the twin against ground truth.

The only module allowed to see both the plant and the twin.
"""
from __future__ import annotations

from dataclasses import dataclass

from .events import BLOCKED, MOVE
from .plant import Plant
from .twin import INFERRED, MEASURED, Twin


def state_agreement(plant: Plant, twin: Twin) -> dict:
    """Diff belief vs truth now, split by the belief's provenance."""
    truth = plant.truth()
    belief = twin.snapshot()
    mismatches = []
    for sid, ts in truth["stations"].items():
        bs = belief["stations"][sid]
        if plant.cfg.station(sid).capacity > 1:
            ts, bs = {"state": ts["state"]}, {"state": bs["state"]}   # which worker holds which vehicle is not a belief
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
    precision_inspection: float | None = None   # holds traced from inspection fails only
    precision_drift: float | None = None        # holds from out-of-spec readings only

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
        def _prec(reason: str) -> float | None:
            hv = set()
            for h in holds:
                if h.reason == reason:
                    hv.update(h.sure, h.uncertain)
            return (len(hv & truth) / len(hv)) if hv else None

        out.append(ContainmentScore(
            d.name, cause, len(truth), t_drift,
            alert.t if alert else None, t_fail, first.t if first else None,
            len(held), sum(len(h.sure) for h in holds), sum(len(h.uncertain) for h in holds),
            tp, (tp / len(truth)) if truth else None, (tp / len(held)) if held else None,
            blanket, escaped, detected, _prec("inspection"), _prec("drift")))
    return out


def _blocked_intervals(plant: Plant, station: str) -> list[tuple[float, float]]:
    out, t0 = [], None
    for e in plant.events:
        if e.station != station:
            continue
        if e.kind == BLOCKED:
            t0 = e.t
        elif e.kind == MOVE and t0 is not None:
            out.append((t0, e.t))
            t0 = None
    if t0 is not None:
        out.append((t0, plant.t))
    return out


def _sustained_block(plant: Plant, station: str, after: float,
                     window_s: float = 600.0, min_fraction: float = 0.15) -> float | None:
    """When the fault at `station` first *sustainably* blocks the station
    before it: the first block after `after`, while the station is truly
    over takt, that is followed by >= `min_fraction` blocked time in the
    next `window_s`. Filters out transient blocks from surges (e.g. the
    backlog released when an earlier bottleneck is repaired)."""
    cfg = plant.cfg
    i = cfg.index(station)
    if i == 0:
        return None
    ivs = _blocked_intervals(plant, cfg.ids[i - 1])
    for t0, _ in ivs:
        if t0 < after or plant.true_cycle(station, t0) <= cfg.takt_s:
            continue
        end = t0 + window_s
        blocked = sum(max(0.0, min(b, end) - max(a, t0)) for a, b in ivs)
        if blocked / window_s >= min_fraction:
            return t0
    return None


def bottleneck_scorecard(plant: Plant, twin: Twin) -> dict:
    cfg = plant.cfg
    takt = cfg.takt_s
    raised = [x for x in twin.log if x.action == "raised"]
    scores: list[BottleneckScore] = []
    explained: set[int] = set()

    for p in cfg.perturbations:
        if p.cycle_s <= takt:
            continue                                  # a recovery, not a fault
        i = cfg.index(p.station)
        base = plant.stations[i].cfg.cycle_s
        t_over = None
        if p.cycle_s > takt:
            if base >= takt or p.ramp_s <= 0:
                t_over = p.at_s
            else:
                t_over = p.at_s + p.ramp_s * (takt - base) / (p.cycle_s - base)
        t_block = _sustained_block(plant, p.station, p.at_s)
        first = None
        for k, x in enumerate(raised):
            if x.alert.station == p.station and x.t >= p.at_s and k not in explained:
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


def true_bottleneck_now(plant: Plant, t: float, tol: float = 0.01) -> tuple[str, float] | None:
    """Ground-truth momentary bottleneck by the active-period method,
    from the plant's complete vehicle records."""
    best = None
    for st in plant.stations:
        visits = sorted(((v.id, x) for v in plant.vehicles.values() for x in v.record
                         if x.station == st.cfg.id), key=lambda r: r[0])
        cur = None
        for k, (vid, x) in enumerate(visits):
            if x.start_t <= t and (x.exit_t is None or x.exit_t > t):
                cur = k
                break
        if cur is None:
            continue
        x = visits[cur][1]
        if x.finish_t is not None and x.finish_t <= t - tol:
            continue                                      # blocked now
        start = x.start_t
        k = cur
        while k > 0:
            p = visits[k - 1][1]
            if p.exit_t is None or visits[k][1].start_t - p.exit_t > tol:
                break                                     # starved gap
            if p.finish_t is None or p.exit_t - p.finish_t > tol:
                break                                     # was blocked
            start = p.start_t
            k -= 1
        dur = t - start
        if best is None or dur > best[1]:
            best = (st.cfg.id, dur)
    return best


def active_period_agreement(plant: Plant, twin: Twin, step_s: float = 60.0,
                            warmup_s: float = 1200.0) -> dict:
    """How often the twin's momentary bottleneck (from partial data) equals
    the plant's (from complete data). Replays sensors -> twin over the
    recorded events, sampling every `step_s`."""
    from .sensors import SensorLayer
    cfg = plant.cfg
    sensors = SensorLayer(cfg, cfg.seed)
    t2 = Twin(cfg)
    sensors.subscribers.append(t2.ingest)
    events = iter(plant.events)
    pending = next(events, None)
    hits = total = fault_hits = fault_total = 0
    t = step_s
    while t <= plant.t:
        while pending is not None and pending.t <= t:
            sensors.observe(pending)
            pending = next(events, None)
        t2.t = max(t2.t, t)
        if t >= warmup_s:
            truth = true_bottleneck_now(plant, t)
            bn = t2.bottleneck_now()
            hit = truth is not None and bn is not None and bn[0] == truth[0]
            total += 1
            hits += hit
            if any(plant.true_cycle(p.station, t) > cfg.takt_s for p in cfg.perturbations):
                fault_total += 1
                fault_hits += hit
        t += step_s
    return {"samples": total, "agreement": (hits / total) if total else None,
            "fault_samples": fault_total,
            "fault_agreement": (fault_hits / fault_total) if fault_total else None}
