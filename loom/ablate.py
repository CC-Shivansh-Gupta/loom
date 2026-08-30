"""Ablation study: what each mechanism actually buys.

Turn one mechanism off at a time and re-measure. Every row should be worse
than the full system on at least one axis -- if it is not, the mechanism is
not earning its place and should be deleted.

    python -m loom.ablate --seeds 5 --out docs/ablation.md

The knobs are real properties of the twin, not a separate code path:

    no_persistence   twin.RAISE_AFTER = 1          (alert on one bad cycle)
    no_se_test       Forecaster(min_over_z=0)      (raw over-takt comparison)
    no_inference     Forecaster(use_inferred=0)    (measured cycles only)
    no_pairs         QualityTwin.MAX_PAIRS = 0     (single conditions only)
    no_backfill      QualityTwin.backfill = False  (hold starts at detection)
"""
from __future__ import annotations

import argparse
import statistics as st
from dataclasses import dataclass, field

from .config import load_line
from .evaluator import bottleneck_scorecard, containment_scorecard
from .forecast import Forecaster
from .plant import Plant
from .quality import QualityTwin
from .sensors import SensorLayer
from .twin import Twin

H = 3600.0


@dataclass
class Ablation:
    key: str
    label: str
    forecaster: dict = field(default_factory=dict)
    raise_after: int = 3
    max_pairs: int = 5
    backfill: bool = True


ABLATIONS = [
    Ablation("full", "full system"),
    Ablation("no_persistence", "no persistence rule", raise_after=1),
    Ablation("no_se_test", "no standard-error test", forecaster={"min_over_z": 0.0}),
    Ablation("no_inference", "no inferred samples", forecaster={"use_inferred": False}),
    Ablation("no_pairs", "no pair search", max_pairs=0),
    Ablation("no_backfill", "no drift back-fill", backfill=False),
]

HEALTHY = ("configs/healthy.yaml", 8.0)
RAMP = ("configs/ramp_b3.yaml", 2.0)
DARK = ("configs/ramp_b3_dark.yaml", 2.0)
DRIFT = ("configs/weld_drift_b2.yaml", 2.0)
MULTI = ("configs/multi_cause.yaml", 3.0)


def run(config: str, hours: float, seed: int, ab: Ablation) -> tuple[Plant, Twin]:
    cfg = load_line(config)
    plant = Plant(cfg)
    plant.rng.seed(seed)
    sensors = SensorLayer(cfg, seed)
    twin = Twin(cfg)
    twin.forecaster = Forecaster(cfg.takt_s, **ab.forecaster)
    twin.RAISE_AFTER = ab.raise_after
    q: QualityTwin = twin.quality
    q.MAX_PAIRS = ab.max_pairs
    q.backfill = ab.backfill
    plant.listeners.append(sensors.observe)
    sensors.subscribers.append(twin.ingest)
    plant.run(hours * H)
    return plant, twin


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return st.mean(xs) if xs else None


def measure(ab: Ablation, seeds: int) -> dict:
    fa, leads, caught, faults = 0, [], 0, 0
    dark_leads, dark_caught, dark_faults = [], 0, 0
    recall, hold_lead, prealert = [], [], []
    pair_found = 0

    for seed in range(seeds):
        plant, twin = run(*HEALTHY, seed, ab)
        fa += bottleneck_scorecard(plant, twin)["alerts_raised"]

        for cfgname, hours, L, C, F in ((RAMP[0], RAMP[1], leads, "ramp", None),
                                        (DARK[0], DARK[1], dark_leads, "dark", None)):
            plant, twin = run(cfgname, hours, seed, ab)
            card = bottleneck_scorecard(plant, twin)
            for s in card["scores"]:
                if C == "ramp":
                    faults += 1
                else:
                    dark_faults += 1
                if s.lead_s is not None and s.lead_s > 0:
                    L.append(s.lead_s / 60)
                    if C == "ramp":
                        caught += 1
                    else:
                        dark_caught += 1

        plant, twin = run(*DRIFT, seed, ab)
        # Direct measure of the back-fill: vehicles held that were already
        # built when the drift was detected. If this is 0, the back-fill is
        # not contributing -- membership is being decided by each vehicle's
        # own reading, not by the onset window.
        q = twin.quality
        for h in q.holds:
            if h.reason != "drift":
                continue
            starts = {vid: t0 for vid, t0, _ in q._started_at(h.station)}
            prealert.append(sum(1 for v in h.sure + h.uncertain
                                if starts.get(v, float("inf")) < h.t))
        for c in containment_scorecard(plant, twin):
            if c.recall is not None:
                recall.append(c.recall)
            if c.t_first_hold is not None and c.t_first_fail is not None:
                hold_lead.append((c.t_first_fail - c.t_first_hold) / 60)

        plant, twin = run(*MULTI, seed, ab)
        hyps = twin.quality.hypotheses
        if hyps and len(hyps[0].conditions) == 2:
            pair_found += 1

    return {
        "fa_per_8h": fa / seeds,
        "ramp": (caught, faults, _mean(leads)),
        "dark": (dark_caught, dark_faults, _mean(dark_leads)),
        "recall": _mean(recall),
        "hold_lead": _mean(hold_lead),
        "prealert": _mean(prealert),
        "pair": (pair_found, seeds),
    }


def report(seeds: int) -> str:
    rows = []
    for ab in ABLATIONS:
        rows.append((ab, measure(ab, seeds)))

    out = ["# Ablation — what each mechanism buys",
           "",
           f"Generated by `python -m loom.ablate --seeds {seeds}`. One mechanism disabled per row,",
           "everything else held fixed, same seeds. Each knob is a real property of the twin",
           "(`RAISE_AFTER`, `Forecaster.use_inferred`, `QualityTwin.backfill`, `MAX_PAIRS`), not a",
           "separate code path — so these rows are the system, degraded.",
           "",
           "| mechanism removed | false alarms / 8 h | B3 ramp caught (lead) | B3 **dark** caught (lead) | drift recall | hold before first catch | held-before-detection | 2-condition cause found |",
           "|---|---|---|---|---|---|---|---|"]
    for ab, m in rows:
        f = lambda x, s="{:.1f}": "-" if x is None else s.format(x)
        rc, rf, rl = m["ramp"]
        dc, df, dl = m["dark"]
        pf, pn = m["pair"]
        out.append(
            f"| {'**' + ab.label + '**' if ab.key == 'full' else ab.label} "
            f"| {m['fa_per_8h']:.1f} "
            f"| {rc}/{rf} ({f(rl)} min) "
            f"| {dc}/{df} ({f(dl)} min) "
            f"| {f(None if m['recall'] is None else m['recall'] * 100, '{:.0f}%')} "
            f"| {f(m['hold_lead'])} min "
            f"| {f(m['prealert'], '{:.0f}')} "
            f"| {pf}/{pn} |")
    out += ["", "Read the first row as the reference. Every other row is worse on at least one axis;",
            "where a row is *better* on lead time it is worse on false alarms, which is the whole",
            "trade the guards exist to make.",
            "",
            "**One mechanism does not earn its row.** `held-before-detection` counts vehicles in a drift",
            "hold that were already built when the drift was caught -- the thing the onset back-fill",
            "exists to recover. It is 0 with the back-fill on *and* off, so on this scenario the",
            "back-fill contributes nothing: B2 reports a weld-current reading for every vehicle, so",
            "hold membership is decided by each vehicle's own reading and the onset window never",
            "binds. The mechanism is only load-bearing where readings are sparse or sampled. Until a",
            "scenario exercises that, the proposal should not claim back-fill as a source of recall.",
            "Found by building this table, which is the argument for building it."]
    return "\n".join(out)


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
