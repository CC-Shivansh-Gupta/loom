"""Plant / line definition loaded from YAML.

The line is data, not code. Schema (all keys optional unless noted):

    extends: base.yaml               # deep-merge over another file
    plant: {name, site, area}        # ISA-95 naming
    libraries:
      station_types:  {name: {sensors, params, description}}
      sensor_profiles: {name: {events: all|[kinds], latency_s, drop_p}}
    line:                            # required
      takt_s (required), cv, seed, default_buffer
      zones: [{name, stations: [{id, type, cycle_s, buffer_before, sensors}]}]
    variants: {name: {share, cycle_mult: {station: factor}}}
    scenario: {perturbations: [{station, at_s, cycle_s, ramp_s}]}

Built-in libraries cover the common cases; a plant file can add to or
override them.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

import yaml

BUILTIN_STATION_TYPES: dict[str, dict] = {
    "generic":     {"sensors": "plc_full", "params": []},
    "robot_weld":  {"sensors": "plc_full", "params": ["weld_current", "torque"]},
    "manual_fit":  {"sensors": "checklist", "params": []},
    "paint_booth": {"sensors": "plc_full", "params": ["booth_temp", "humidity"]},
    "inspection":  {"sensors": "plc_full", "params": [], "inspection": True},
}

# jitter_s: sd of timestamp noise. clock_offset_s: fixed skew of that
# station's clock. drop_p: per-event loss. latency_s: reporting delay.
BUILTIN_SENSOR_PROFILES: dict[str, dict] = {
    "plc_full":   {"events": "all", "jitter_s": 0.2},
    "cycle_only": {"events": ["start", "finish"], "jitter_s": 1.0, "drop_p": 0.01},
    "checklist":  {"events": ["finish"], "latency_s": 120.0, "jitter_s": 30.0, "drop_p": 0.05},
    "dark":       {"events": []},
}


@dataclass(frozen=True)
class SensorProfile:
    name: str
    events: frozenset[str] | None       # None = all
    latency_s: float = 0.0
    drop_p: float = 0.0
    jitter_s: float = 0.0
    clock_offset_s: float = 0.0

    def passes(self, kind: str) -> bool:
        return self.events is None or kind in self.events


@dataclass(frozen=True)
class SensorFault:
    """Instrumentation on `station` goes silent for `duration_s` from `at_s`."""
    station: str
    at_s: float
    duration_s: float


@dataclass(frozen=True)
class StationType:
    name: str
    sensors: str
    params: tuple[str, ...] = ()
    inspection: bool = False
    description: str = ""


@dataclass(frozen=True)
class StationCfg:
    id: str
    zone: str
    type: StationType
    cycle_s: float
    buffer_before: int
    sensors: SensorProfile


@dataclass(frozen=True)
class Variant:
    name: str
    share: float
    cycle_mult: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Perturbation:
    """Station's nominal cycle ramps linearly from base to `cycle_s`,
    starting at `at_s` and taking `ramp_s` (0 = step change)."""
    station: str
    at_s: float
    cycle_s: float
    ramp_s: float = 0.0


@dataclass(frozen=True)
class LineCfg:
    name: str
    plant: dict
    takt_s: float
    stations: tuple[StationCfg, ...]
    cv: float = 0.0
    seed: int = 0
    variants: tuple[Variant, ...] = ()
    perturbations: tuple[Perturbation, ...] = ()
    sensor_faults: tuple[SensorFault, ...] = ()

    @property
    def ids(self) -> list[str]:
        return [s.id for s in self.stations]

    def index(self, station: str) -> int:
        return self.ids.index(station)

    def station(self, station: str) -> StationCfg:
        return self.stations[self.index(station)]


# -- loading ---------------------------------------------------------------

def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_raw(path: str | Path) -> dict:
    path = Path(path)
    raw = yaml.safe_load(path.read_text()) or {}
    if "extends" in raw:
        base = load_raw(path.parent / raw.pop("extends"))
        raw = _deep_merge(base, raw)
    return raw


def _sensor_profiles(raw: dict) -> dict[str, SensorProfile]:
    merged = _deep_merge(BUILTIN_SENSOR_PROFILES,
                         raw.get("libraries", {}).get("sensor_profiles", {}) or {})
    out = {}
    for name, p in merged.items():
        ev = p.get("events", "all")
        out[name] = SensorProfile(
            name, None if ev == "all" else frozenset(ev),
            float(p.get("latency_s", 0.0)), float(p.get("drop_p", 0.0)),
            float(p.get("jitter_s", 0.0)), float(p.get("clock_offset_s", 0.0)))
    return out


def _station_types(raw: dict) -> dict[str, StationType]:
    merged = _deep_merge(BUILTIN_STATION_TYPES,
                         raw.get("libraries", {}).get("station_types", {}) or {})
    return {name: StationType(name, t.get("sensors", "plc_full"),
                              tuple(t.get("params", [])), bool(t.get("inspection", False)),
                              str(t.get("description", "")))
            for name, t in merged.items()}


def load_line(path: str | Path) -> LineCfg:
    raw = load_raw(path)
    line = raw["line"]
    profiles = _sensor_profiles(raw)
    types = _station_types(raw)
    default_buf = int(line.get("default_buffer", 2))

    stations: list[StationCfg] = []
    for zone in line["zones"]:
        for s in zone["stations"]:
            st_type = types[s.get("type", "generic")]
            prof = profiles[s.get("sensors", st_type.sensors)]
            stations.append(StationCfg(
                id=str(s["id"]), zone=str(zone["name"]), type=st_type,
                cycle_s=float(s["cycle_s"]),
                buffer_before=int(s.get("buffer_before", default_buf)),
                sensors=prof))
    if not stations:
        raise ValueError(f"{path}: line has no stations")
    ids = {s.id for s in stations}
    if len(ids) != len(stations):
        raise ValueError(f"{path}: duplicate station ids")

    variants = []
    for name, v in (raw.get("variants") or {}).items():
        for sid in v.get("cycle_mult", {}):
            if sid not in ids:
                raise ValueError(f"{path}: variant {name} references unknown station {sid}")
        variants.append(Variant(str(name), float(v.get("share", 1.0)),
                                {k: float(x) for k, x in v.get("cycle_mult", {}).items()}))
    if variants:
        total = sum(v.share for v in variants)
        variants = [Variant(v.name, v.share / total, v.cycle_mult) for v in variants]

    scenario = raw.get("scenario") or {}
    perts = []
    for p in scenario.get("perturbations", []) or []:
        if p["station"] not in ids:
            raise ValueError(f"{path}: perturbation on unknown station {p['station']}")
        perts.append(Perturbation(str(p["station"]), float(p["at_s"]),
                                  float(p["cycle_s"]), float(p.get("ramp_s", 0))))
    faults = []
    for f in scenario.get("sensor_faults", []) or []:
        if f["station"] not in ids:
            raise ValueError(f"{path}: sensor fault on unknown station {f['station']}")
        faults.append(SensorFault(str(f["station"]), float(f["at_s"]), float(f["duration_s"])))

    return LineCfg(
        name=str(line.get("id", Path(path).stem)),
        plant=dict(raw.get("plant") or {}),
        takt_s=float(line["takt_s"]),
        stations=tuple(stations),
        cv=float(line.get("cv", 0.0)),
        seed=int(line.get("seed", 0)),
        variants=tuple(variants),
        perturbations=tuple(perts),
        sensor_faults=tuple(faults),
    )
