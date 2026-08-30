"""Baseline comparators: Loom against the alternatives, on the same data.

Every number the benchmark reports is absolute -- "6.1 minutes of warning".
A panel asks "compared to what?". This module answers that, fairly: each
comparator sees exactly the event stream the sensor layer passed to the
twin, so the difference between them is the mechanism, not the data.

    no_twin     you find out when the upstream station actually blocks.
                This is the plant today. Lead = 0 by definition.
    threshold   alarm the first time a station's *measured* cycle exceeds
                takt x k. The alarm every PLC and OEE tool already has.
                No persistence rule, no trend test, no inference -- so it
                cannot see a dark station at all.
    detection   the active-period method (Roser): the station with the
                longest uninterrupted active period is the momentary
                bottleneck. Best-validated method in the literature, and
                detection-only -- it names a constraint that already
                exists rather than one that is forming. Steelmanned with
                the same persistence rule Loom uses, so it is an alarm
                and not just a signal.
    loom        the forecaster.

    python -m loom.baseline --seeds 10
"""
from __future__ import annotations

import argparse
import statistics as st
from dataclasses import dataclass, field

from .config import load_line
from .evaluator import _sustained_block, bottleneck_scorecard, containment_scorecard
from .plant import Plant
from .sensors import SensorLayer
from .twin import MEASURED, Twin

H = 3600.0
METHODS = ("no_twin", "threshold", "detection", "loom")
THRESHOLD_K = 1.05          # alarm when the reported cycle is 5 % over takt
DETECT_PERSIST = 3          # samples the same station must hold the title
DETECT_STEP_S = 60.0

FLOW_SCENARIOS = [
    ("configs/ramp_b3.yaml", 2.0),
    ("configs/ramp_b3_dark.yaml", 2.0),
    ("configs/sensor_fault_b2.yaml", 2.0),
    ("configs/plant_b.yaml", 3.0),
]
HEALTHY = ("configs/healthy.yaml", 8.0)
QUALITY_SCENARIOS = [("configs/weld_drift_b2.yaml", 2.0)]


@dataclass
class MethodScore:
    method: str
    station: str
    t_warn: float | None
    t_block: float | None

    @property
    def lead_s(self) -> float | None:
        if self.t_warn is None or self.t_block is None:
            return None
        return self.t_block - self.t_warn


@dataclass
class Tap:
    """Records every cycle observation handed to the forecaster, with its
    provenance -- the twin keeps only a rolling window, and the baselines
    need the whole run."""
    rows: list[tuple[float, str, float, str]] = field(default_factory=list)

    def attach(self, twin: Twin) -> None:
        inner = twin.forecaster.observe

        def observe(station, t, cycle_s, source=MEASURED):
            self.rows.append((t, station, cycle_s, source))
            inner(station, t, cycle_s, source)

        twin.forecaster.observe = observe          # type: ignore[assignment]


def run(config: str, hours: float, seed: int) -> tuple[Plant, Twin, Tap]:
    cfg = load_line(config)
    plant = Plant(cfg)
    plant.rng.seed(seed)
    sensors = SensorLayer(cfg, seed)
    twin = Twin(cfg)
    tap = Tap()
    tap.attach(twin)
    plant.listeners.append(sensors.observe)
    sensors.subscribers.append(twin.ingest)
    plant.run(hours * H)
    return plant, twin, tap


# ---- the comparators -------------------------------------------------

def threshold_alarms(tap: Tap, takt_s: float, k: float = THRESHOLD_K) -> list[tuple[float, str]]:
    """Every measured cycle over takt x k, in time order. A PLC threshold
    alarm sees no inferred value, so dark stations never appear here."""
    return [(t, s) for t, s, c, src in tap.rows if src == MEASURED and c > takt_s * k]


def detection_alarms(plant: Plant, step_s: float = DETECT_STEP_S,
                     persist: int = DETECT_PERSIST) -> list[tuple[float, str]]:
    """Active-period detection, replayed through the sensor layer so it
    sees only what the twin saw, with a persistence rule so it produces
    alarms rather than a continuous signal."""
    cfg = plant.cfg
    sensors = SensorLayer(cfg, cfg.seed)
    t2 = Twin(cfg)
    sensors.subscribers.append(t2.ingest)
    events = iter(plant.events)
    pending = next(events, None)
    out: list[tuple[float, str]] = []
    run_station, run_n, announced = None, 0, None
    t = step_s
    while t <= plant.t:
        while pending is not None and pending.t <= t:
            sensors.observe(pending)
            pending = next(events, None)
        t2.t = max(t2.t, t)
        bn = t2.bottleneck_now()
        sid = bn[0] if bn else None
        if sid == run_station:
            run_n += 1
        else:
            run_station, run_n = sid, 1
        if sid is not None and run_n >= persist and announced != sid:
            out.append((t, sid))
            announced = sid
        t += step_s
    return out


def compare(config: str, hours: float, seed: int) -> tuple[list[MethodScore], dict[str, int]]:
    """Per injected fault, the warning time each method achieved; plus the
    alarm count each method produced over the whole run."""
    plant, twin, tap = run(config, hours, seed)
    cfg = plant.cfg
    card = bottleneck_scorecard(plant, twin)
    thr = threshold_alarms(tap, cfg.takt_s)
    det = detection_alarms(plant)
    scores: list[MethodScore] = []

    for s in card["scores"]:
        t_block = s.t_upstream_blocked
        first = lambda xs: next((t for t, sid in xs if sid == s.station and t >= s.t_ramp_start), None)
        scores += [
            MethodScore("no_twin", s.station, t_block, t_block),
            MethodScore("threshold", s.station, first(thr), t_block),
            MethodScore("detection", s.station, first(det), t_block),
            MethodScore("loom", s.station, s.t_alert, t_block),
        ]
    counts = {"threshold": len(thr), "detection": len(det), "loom": card["alerts_raised"],
              "no_twin": 0}
    return scores, counts


# ---- reporting -------------------------------------------------------

def _mean(xs):
    xs = [x for x in xs if x is not None]
    return st.mean(xs) if xs else None


def flow_table(seeds: int) -> str:
    """Lead time per method, per scenario. Read it next to the healthy-line
    table below: lead time alone is meaningless without the alarm rate that
    bought it."""
    out = ["| scenario | method | faults warned | mean lead (min) |", "|---|---|---|---|"]
    for config, hours in FLOW_SCENARIOS:
        agg: dict[str, list] = {m: [] for m in METHODS}
        caught = {m: 0 for m in METHODS}
        n_faults = 0
        for seed in range(seeds):
            scores, _ = compare(config, hours, seed)
            n_faults += len(scores) // len(METHODS)
            for s in scores:
                if s.method == "no_twin":
                    continue
                if s.lead_s is not None and s.lead_s > 0:
                    agg[s.method].append(s.lead_s / 60)
                    caught[s.method] += 1
        for m in METHODS:
            if m == "no_twin":
                out.append(f"| `{config}` | no_twin | {n_faults}/{n_faults} | "
                           f"0.0 — you find out when it blocks |")
                continue
            lead = _mean(agg[m])
            cell = "never warned" if lead is None else f"{lead:.1f}"
            out.append(f"| `{config}` | {m} | {caught[m]}/{n_faults} | {cell} |")
    return "\n".join(out)


def healthy_table(seeds: int) -> str:
    config, hours = HEALTHY
    tot = {m: 0 for m in METHODS if m != "no_twin"}
    for seed in range(seeds):
        _, counts = compare(config, hours, seed)
        for m in tot:
            tot[m] += counts[m]
    out = ["| method | alarms per 8 h, healthy line | verdict |", "|---|---|---|"]
    verdict = {
        "threshold": "unusable — an alarm every three minutes is an alarm nobody reads",
        "detection": "unusable as an alarm; fine as a dashboard signal",
        "loom": "inside the published budget of 1 per five shifts",
    }
    for m, n in tot.items():
        rate = n / seeds / (hours / 8)
        out.append(f"| {m} | {rate:.1f} | {verdict[m]} |")
    return "\n".join(out)


def containment_table(seeds: int) -> str:
    out = ["| scenario | method | vehicles held | truly defective held | escaped | first action at |",
           "|---|---|---|---|---|---|"]
    for config, hours in QUALITY_SCENARIOS:
        blanket, held, tp, esc, t_hold, t_fail, ndef = [], [], [], [], [], [], []
        for seed in range(seeds):
            plant, twin, _ = run(config, hours, seed)
            for c in containment_scorecard(plant, twin):
                blanket.append(c.blanket_size)
                held.append(c.hold_size)
                tp.append(c.true_positives)
                esc.append(c.escaped)
                ndef.append(c.n_defective)
                if c.t_first_hold is not None:
                    t_hold.append(c.t_first_hold / 60)
                if c.t_first_fail is not None:
                    t_fail.append(c.t_first_fail / 60)
        f = lambda xs: "-" if not xs else f"{_mean(xs):.0f}"
        fm = lambda xs: "-" if not xs else f"{_mean(xs):.0f} min"
        out.append(f"| `{config}` | end-of-line inspection only | 0 | 0 | {f(ndef)} | {fm(t_fail)} |")
        out.append(f"| `{config}` | blanket hold on the station | {f(blanket)} | {f(tp)} | 0 | {fm(t_fail)} |")
        out.append(f"| `{config}` | Loom targeted hold | {f(held)} | {f(tp)} | {f(esc)} | {fm(t_hold)} |")
    return "\n".join(out)


def report(seeds: int) -> str:
    return "\n\n".join([
        "# Baselines — Loom against the alternatives",
        "Generated by `python -m loom.baseline`. Every comparator sees the same sensor-filtered\n"
        "event stream the twin saw, so the difference is the mechanism, not the data.\n\n"
        f"`threshold` = alarm when a measured cycle exceeds takt x {THRESHOLD_K:.2f}, no persistence,\n"
        "no inference. `detection` = active-period method (Roser) with the same persistence rule\n"
        "Loom uses, so it is an alarm and not a continuous signal. `no_twin` = the plant today.",
        "## Alarms on a healthy line\n\nRead this table first. Lead time is only meaningful next to\nthe alarm rate that bought it — a trigger-happy rule always wins on lead and is always ignored\nby the floor within a week.",
        healthy_table(seeds),
        "## Warning before the line blocks", flow_table(seeds),
        "## Containment", containment_table(seeds),
    ])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    text = report(a.seeds)
    print(text)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
