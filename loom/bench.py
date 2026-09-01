"""Multi-run benchmark: the numbers the pitch quotes, over many seeds.

    python -m loom.bench --seeds 10 --out docs/benchmark.md

For every scenario x seed: bottleneck lead / ETA error / false alarms,
containment precision / recall / escapes, inference accuracy at dark and
partial stations, active-period agreement, and a confidence calibration
table. Writes markdown so the result is reviewable in the repo.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
from dataclasses import dataclass, field

from .config import load_line
from .evaluator import (active_period_agreement, bottleneck_scorecard, containment_scorecard,
                        inference_accuracy)
from .harness import Record, calibration
from .plant import Plant
from .sensors import SensorLayer
from .twin import INFERRED, Twin

H = 3600.0

SCENARIOS = [
    # (config, hours, kind)  kind: healthy | flow | quality | both
    ("configs/healthy.yaml", 8.0, "healthy"),
    ("configs/ramp_b3.yaml", 2.0, "flow"),
    ("configs/ramp_b3_dark.yaml", 2.0, "flow"),
    ("configs/sensor_fault_b2.yaml", 2.0, "flow"),
    ("configs/shifting.yaml", 2.5, "flow"),
    ("configs/plant_b.yaml", 3.0, "flow"),
    ("configs/weld_drift_b2.yaml", 2.0, "quality"),
    ("configs/weld_drift_b2_sampled.yaml", 2.0, "quality"),   # same drift, 1 reading in 5
    ("configs/multi_cause.yaml", 3.0, "quality"),
]


@dataclass
class Agg:
    leads: list[float] = field(default_factory=list)
    eta_err: list[float] = field(default_factory=list)
    misses: int = 0
    faults: int = 0
    false_alarms: int = 0
    fault_hours: float = 0.0
    healthy_hours: float = 0.0
    healthy_alarms: int = 0
    ap_agreement: list[float] = field(default_factory=list)
    inferred_mae: list[float] = field(default_factory=list)
    precision: list[float] = field(default_factory=list)
    precision_insp: list[float] = field(default_factory=list)
    recall: list[float] = field(default_factory=list)
    escaped: int = 0
    hold_vs_blanket: list[float] = field(default_factory=list)
    hold_saved: list[int] = field(default_factory=list)      # blanket size - targeted size
    escapes_prevented: list[int] = field(default_factory=list)
    hold_ahead_min: list[float] = field(default_factory=list)
    records: list[Record] = field(default_factory=list)
    drift_warnings: int = 0
    holds_on_healthy: int = 0
    cause_found: int = 0
    cause_runs: int = 0


def _run(config: str, hours: float, seed: int):
    cfg = load_line(config)
    plant = Plant(cfg)
    plant.rng.seed(seed)
    sensors = SensorLayer(cfg, seed)
    twin = Twin(cfg)
    plant.listeners.append(sensors.observe)
    sensors.subscribers.append(twin.ingest)
    plant.run(hours * H)
    return cfg, plant, twin


def run(seeds: int, scenarios=SCENARIOS) -> dict[str, Agg]:
    out: dict[str, Agg] = {}
    for config, hours, kind in scenarios:
        a = out.setdefault(config, Agg())
        for seed in range(seeds):
            cfg, plant, twin = _run(config, hours, seed)
            card = bottleneck_scorecard(plant, twin)
            for x in card["false_alarms"]:
                a.records.append(Record(config, x.alert.station, x.alert.confidence, False, None))
            if kind == "healthy":
                a.healthy_hours += hours
                a.healthy_alarms += card["alerts_raised"]
                a.drift_warnings += len(twin.quality.drift_log)
                a.holds_on_healthy += len(twin.quality.holds)
                continue
            a.false_alarms += len(card["false_alarms"])
            a.fault_hours += hours
            for s in card["scores"]:
                a.faults += 1
                if s.lead_s is not None and s.lead_s > 0:
                    a.leads.append(s.lead_s / 60)
                    a.eta_err.append(abs(s.eta_error_s) / 60)
                    a.records.append(Record(config, s.station, s.alert_conf or 0.0, True, s.lead_s))
                else:
                    a.misses += 1
            if kind == "flow" and cfg.perturbations:
                ap = active_period_agreement(plant, twin)
                if ap["fault_agreement"] is not None:
                    a.ap_agreement.append(ap["fault_agreement"])
            for sid, acc in inference_accuracy(plant, twin).items():
                if acc[INFERRED]["mae"] is not None and acc[INFERRED]["n"] >= 10:
                    a.inferred_mae.append(acc[INFERRED]["mae"])
            if cfg.defects and twin.quality.hypotheses:
                truth = {(c.station, c.param) for d in cfg.defects for c in d.causes}
                top = {(c.station, c.param) for c in twin.quality.hypotheses[0].conditions}
                a.cause_runs += 1
                a.cause_found += int(top == truth)
            elif cfg.defects:
                a.cause_runs += 1
            for c in containment_scorecard(plant, twin):
                if c.precision is not None:
                    a.precision.append(c.precision)
                if c.precision_inspection is not None:
                    a.precision_insp.append(c.precision_inspection)
                if c.recall is not None:
                    a.recall.append(c.recall)
                a.escaped += c.escaped
                if c.hold_size and c.blanket_size:
                    a.hold_vs_blanket.append(c.hold_size / c.blanket_size)
                    a.hold_saved.append(c.blanket_size - c.hold_size)
                a.escapes_prevented.append(
                    max(0, (c.n_defective - c.detected_at_inspection) - c.escaped))
                if c.t_first_hold is not None and c.t_first_fail is not None:
                    a.hold_ahead_min.append((c.t_first_fail - c.t_first_hold) / 60)
    return out


def _q(xs: list[float], fmt="{:.1f}") -> str:
    if not xs:
        return "-"
    if len(xs) == 1:
        return fmt.format(xs[0])
    xs = sorted(xs)
    p10, p90 = xs[int(0.1 * (len(xs) - 1))], xs[int(0.9 * (len(xs) - 1))]
    return f"{fmt.format(st.mean(xs))} (p10 {fmt.format(p10)}, p90 {fmt.format(p90)})"


def markdown(res: dict[str, Agg], seeds: int) -> str:
    L = [f"# Benchmark — {seeds} seeds per scenario", "",
         "Generated by `python -m loom.bench`. Every number is against ground truth the twin never saw.", ""]
    L += ["## Healthy line (false-alarm floor)", "",
          "| scenario | hours | bottleneck alerts / 8 h | drift warnings / 8 h | holds |", "|---|---|---|---|---|"]
    for cfg, a in res.items():
        if a.healthy_hours:
            L.append(f"| `{cfg}` | {a.healthy_hours:.0f} | {a.healthy_alarms / a.healthy_hours * 8:.2f} | "
                     f"{a.drift_warnings / a.healthy_hours * 8:.2f} | {a.holds_on_healthy} |")
    L += ["", "## Bottleneck forecasting", "",
          "A false alarm is an alert that no injected fault explains. The rate is per 8 h of run "
          "time so it can be compared with the healthy-line floor above.", "",
          "| scenario | faults | caught | lead min (mean, p10, p90) | ETA error min | false alarms (per 8 h) | active-period agreement | inferred cycle MAE s |",
          "|---|---|---|---|---|---|---|---|"]
    for cfg, a in res.items():
        if a.faults:
            rate = (a.false_alarms / a.fault_hours * 8) if a.fault_hours else 0.0
            L.append(f"| `{cfg}` | {a.faults} | {a.faults - a.misses} | {_q(a.leads)} | {_q(a.eta_err)} | "
                     f"{a.false_alarms} ({rate:.1f}) | {_q(a.ap_agreement, '{:.0%}')} | "
                     f"{_q(a.inferred_mae)} |")
    L += ["", "## Containment", "",
          "Hold timing is relative to the first end-of-line catch: positive = the twin held before "
          "inspection saw anything (drift-triggered); negative = the hold was learned from inspection "
          "fails (no upstream signal existed).", "",
          "| scenario | true cause pair/single found | precision (all holds) | precision (traced holds) | recall | escaped (total) | hold / blanket | hold vs first inspection catch (min) |",
          "|---|---|---|---|---|---|---|---|"]
    for cfg, a in res.items():
        if a.precision or a.recall or a.cause_runs:
            found = f"{a.cause_found}/{a.cause_runs}" if a.cause_runs else "-"
            L.append(f"| `{cfg}` | {found} | {_q(a.precision, '{:.0%}')} | {_q(a.precision_insp, '{:.0%}')} | "
                     f"{_q(a.recall, '{:.0%}')} | {a.escaped} | "
                     f"{_q(a.hold_vs_blanket, '{:.2f}')} | {_q(a.hold_ahead_min)} |")
    records = [r for a in res.values() for r in a.records]
    L += ["", "## Confidence calibration (all alerts)", "",
          "| stated confidence | n | realised hit rate |", "|---|---|---|"]
    for c in calibration(records):
        L.append(f"| {c['confidence_bin']} | {c['n']} | {c['hit_rate']:.0%} |")
    return "\n".join(L) + "\n"


def summary(res: dict[str, Agg], seeds: int) -> dict:
    """Machine-readable aggregates, written beside the markdown.

    The leadership view needs a lead time to put a number on, and on a line
    that has not faulted yet it has none of its own. Rather than render $0 --
    which reads as a broken business case rather than an empty one -- it
    falls back to this file and says so. Same discipline as the rest: the
    figure comes from a run, not from someone typing it into a slide."""
    leads = [l for a in res.values() for l in a.leads]
    saved = [x for a in res.values() for x in a.hold_saved]
    prevented = [x for a in res.values() for x in a.escapes_prevented]
    return {
        "seeds": seeds,
        "mean_lead_min": round(st.mean(leads), 2) if leads else None,
        "mean_hold_saved": round(st.mean(saved)) if saved else None,
        "mean_escapes_prevented": round(st.mean(prevented)) if prevented else None,
        "scenarios": {
            cfg: {
                "faults": a.faults,
                "caught": a.faults - a.misses,
                "mean_lead_min": round(st.mean(a.leads), 2) if a.leads else None,
                "false_alarms": a.false_alarms,
                "fa_per_8h": round(a.false_alarms / a.fault_hours * 8, 2) if a.fault_hours else None,
                "healthy_alarms_per_8h": (round(a.healthy_alarms / a.healthy_hours * 8, 2)
                                          if a.healthy_hours else None),
            }
            for cfg, a in res.items()
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    res = run(args.seeds)
    md = markdown(res, args.seeds)
    if args.out:
        with open(args.out, "w") as f:
            f.write(md)
        js = os.path.splitext(args.out)[0] + ".json"
        with open(js, "w") as f:
            json.dump(summary(res, args.seeds), f, indent=1)
        print(f"wrote {args.out} and {js}")
    print(md)


if __name__ == "__main__":
    main()
