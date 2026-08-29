"""Value of information: which station to instrument next.

Replays the plant's ground-truth event stream through a sensor layer with
one station's profile upgraded, and measures what the twin gains. Two
metrics:

  samples   extra exact cycle samples per hour the twin obtains, line-wide
            (needs no ground truth -- usable in production on history)
  lead_s    change in bottleneck lead time on the scenario, if it has one
            (needs ground truth -- evaluator only)
"""
from __future__ import annotations

import dataclasses

from .config import LineCfg, StationCfg, SensorProfile, BUILTIN_SENSOR_PROFILES
from .evaluator import bottleneck_scorecard
from .events import Event
from .plant import Plant
from .sensors import SensorLayer
from .twin import Twin

_UPGRADE_FROM = {"dark", "checklist", "cycle_only"}


def _profile(name: str) -> SensorProfile:
    p = BUILTIN_SENSOR_PROFILES[name]
    ev = p.get("events", "all")
    return SensorProfile(name, None if ev == "all" else frozenset(ev),
                         float(p.get("latency_s", 0.0)), float(p.get("drop_p", 0.0)),
                         float(p.get("jitter_s", 0.0)), float(p.get("clock_offset_s", 0.0)))


def with_profile(cfg: LineCfg, station: str, profile: str) -> LineCfg:
    stations = tuple(
        dataclasses.replace(s, sensors=_profile(profile)) if s.id == station else s
        for s in cfg.stations)
    return dataclasses.replace(cfg, stations=stations)


def replay(cfg: LineCfg, events: list[Event]) -> tuple[SensorLayer, Twin]:
    sensors = SensorLayer(cfg, cfg.seed)
    twin = Twin(cfg)
    sensors.subscribers.append(twin.ingest)
    for ev in events:
        sensors.observe(ev)
    return sensors, twin


def _total_samples(twin: Twin) -> int:
    return sum(b.measured_samples + b.inferred_samples for b in twin.stations.values())


def rank(cfg: LineCfg, plant: Plant, twin: Twin, upgrade_to: str = "cycle_only",
         cost: float = 50.0) -> list[dict]:
    """Rank candidate retrofits by what the twin would gain."""
    hours = max(plant.t / 3600, 1e-9)
    base_samples = _total_samples(twin)
    base_sc = bottleneck_scorecard(plant, twin)
    base_lead = {s.station: s.lead_s for s in base_sc["scores"]}

    out = []
    for s in cfg.stations:
        if s.sensors.name not in _UPGRADE_FROM or s.sensors.name == upgrade_to:
            continue
        cfg2 = with_profile(cfg, s.id, upgrade_to)
        _, twin2 = replay(cfg2, plant.events)
        d_samples = (_total_samples(twin2) - base_samples) / hours
        sc2 = bottleneck_scorecard(plant, twin2)
        d_lead = None
        for sc in sc2["scores"]:
            b = base_lead.get(sc.station)
            if sc.lead_s is not None and b is not None:
                d_lead = (d_lead or 0.0) + (sc.lead_s - b)
            elif sc.lead_s is not None and b is None:
                d_lead = (d_lead or 0.0) + sc.lead_s      # from missed to caught
        out.append({"station": s.id, "from": s.sensors.name, "to": upgrade_to,
                    "d_samples_per_h": d_samples, "d_lead_s": d_lead, "cost": cost})
    out.sort(key=lambda r: ((r["d_lead_s"] or 0.0), r["d_samples_per_h"]), reverse=True)
    return out
