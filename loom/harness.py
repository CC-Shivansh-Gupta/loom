"""Evaluation harness: the gate every proposed change must pass.

Runs a fixed set of recorded scenarios through the twin with candidate
parameters and returns the two numbers that matter: false alarms per 8 h
on healthy shifts, and lead time on injected faults. Used by the
improvement loop and by the ledger calibration.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import load_line
from .evaluator import bottleneck_scorecard
from .forecast import Forecaster
from .plant import Plant
from .sensors import SensorLayer
from .twin import Twin

H = 3600.0
DEFAULT_PARAMS = {"window": 20, "min_tstat": 4.0, "min_over_z": 2.0, "raise_after": 3}
BOUNDS = {"window": (8, 40), "min_tstat": (2.0, 8.0), "min_over_z": (1.0, 5.0), "raise_after": (1, 6)}


@dataclass
class Scenario:
    config: str
    hours: float
    seeds: tuple[int, ...] = (0,)
    kind: str = "healthy"       # healthy -> counts false alarms; fault -> measures lead


# The gate must see what the benchmark sees, or it waves through changes the
# benchmark then fails. It nearly did: `loom.sweep` selects window=10 over the
# shipped 20 on the first three scenarios alone -- 9.3 min mean lead against
# 6.4, both at zero false alarms. Measured over eight seeds of all six
# benchmark scenarios, window=10 *doubles* the healthy-line false-alarm rate
# (0.38 -> 0.75 per 8 h) for 0.7 min of lead and catches one fault fewer.
#
# Two things had to change, and the second matters more. Adding the moving
# constraint and the 30-station plant is the obvious half. The half that
# actually decides it is healthy *hours*: false alarms are a low-rate quantity,
# and twelve hours cannot separate 0.4 per 8 h from 0.8 per 8 h. A gate that
# samples the cost of a change less precisely than its benefit will always
# drift toward the change.
DEFAULT_SCENARIOS = (
    Scenario("configs/healthy.yaml", 8.0, (0, 1, 2, 3, 4, 5), "healthy"),   # 48 h
    Scenario("configs/ramp_b3.yaml", 2.0, (0, 1), "fault"),
    Scenario("configs/ramp_b3_dark.yaml", 2.0, (0,), "fault"),
    Scenario("configs/shifting.yaml", 2.5, (0,), "fault"),
    Scenario("configs/plant_b.yaml", 3.0, (0,), "fault"),
)


@dataclass
class Record:
    """One alert with its outcome -- the unit of the trust ledger."""
    scenario: str
    station: str
    confidence: float
    hit: bool
    lead_s: float | None


@dataclass
class Result:
    params: dict
    fa_per_8h: float
    leads_min: list[float]
    misses: int
    records: list[Record] = field(default_factory=list)

    @property
    def mean_lead_min(self) -> float | None:
        return sum(self.leads_min) / len(self.leads_min) if self.leads_min else None

    def as_dict(self) -> dict:
        return {"params": self.params, "fa_per_8h": round(self.fa_per_8h, 2),
                "mean_lead_min": None if self.mean_lead_min is None else round(self.mean_lead_min, 1),
                "min_lead_min": None if not self.leads_min else round(min(self.leads_min), 1),
                "misses": self.misses}


def _run(config: str, hours: float, seed: int, params: dict) -> tuple[Plant, Twin]:
    cfg = load_line(config)
    plant = Plant(cfg)
    plant.rng.seed(seed)
    sensors = SensorLayer(cfg, seed)
    twin = Twin(cfg)
    fk = {k: v for k, v in params.items() if k != "raise_after"}
    twin.forecaster = Forecaster(cfg.takt_s, **fk)
    twin.RAISE_AFTER = int(params.get("raise_after", 3))
    plant.listeners.append(sensors.observe)
    sensors.subscribers.append(twin.ingest)
    plant.run(hours * H)
    return plant, twin


def evaluate(params: dict | None = None, scenarios=DEFAULT_SCENARIOS) -> Result:
    params = {**DEFAULT_PARAMS, **(params or {})}
    fa, healthy_hours = 0, 0.0
    leads, misses, records = [], 0, []
    for sc in scenarios:
        for seed in sc.seeds:
            plant, twin = _run(sc.config, sc.hours, seed, params)
            card = bottleneck_scorecard(plant, twin)
            for x in card["false_alarms"]:
                records.append(Record(sc.config, x.alert.station, x.alert.confidence, False, None))
            if sc.kind == "healthy":
                healthy_hours += sc.hours
                fa += card["alerts_raised"]
            else:
                for s in card["scores"]:
                    if s.lead_s is not None and s.lead_s > 0:
                        leads.append(s.lead_s / 60)
                        records.append(Record(sc.config, s.station, s.alert_conf or 0.0, True, s.lead_s))
                    else:
                        misses += 1
    return Result(params, fa / max(healthy_hours, 1e-9) * 8, leads, misses, records)


def calibration(records: list[Record], bins=(0.0, 0.5, 0.7, 0.9, 1.01)) -> list[dict]:
    """Stated confidence vs realised hit rate, per bin."""
    out = []
    for lo, hi in zip(bins, bins[1:]):
        xs = [r for r in records if lo <= r.confidence < hi]
        if xs:
            out.append({"confidence_bin": f"{lo:.1f}-{min(hi, 1.0):.1f}", "n": len(xs),
                        "hit_rate": round(sum(r.hit for r in xs) / len(xs), 2)})
    return out


def clamp(params: dict) -> dict:
    out = {}
    for k, v in params.items():
        lo, hi = BOUNDS[k]
        v = max(lo, min(hi, v))
        out[k] = int(v) if k in ("window", "raise_after") else float(v)
    return out
