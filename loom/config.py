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

# Process parameters: nominal, natural sd, spec limits, measurement noise.
BUILTIN_PARAMS: dict[str, dict] = {
    "weld_current": {"nominal": 8.5, "sd": 0.12, "lsl": 8.0, "usl": 9.0, "unit": "kA", "meas_sd": 0.03},
    "torque":       {"nominal": 45.0, "sd": 1.0, "lsl": 42.0, "usl": 48.0, "unit": "Nm", "meas_sd": 0.2},
    "booth_temp":   {"nominal": 22.0, "sd": 0.5, "lsl": 20.0, "usl": 24.0, "unit": "C", "meas_sd": 0.1},
    "humidity":     {"nominal": 55.0, "sd": 3.0, "lsl": 40.0, "usl": 65.0, "unit": "%", "meas_sd": 0.5},
    "gap_mm":       {"nominal": 4.0, "sd": 0.3, "lsl": 3.0, "usl": 5.0, "unit": "mm", "meas_sd": 0.05},
    "press_force":  {"nominal": 120.0, "sd": 3.0, "lsl": 110.0, "usl": 130.0, "unit": "kN", "meas_sd": 0.5},
    "bead_width":   {"nominal": 6.0, "sd": 0.4, "lsl": 5.0, "usl": 7.0, "unit": "mm", "meas_sd": 0.1},
}

BUILTIN_STATION_TYPES: dict[str, dict] = {
    "generic":     {"sensors": "plc_full", "params": []},
    "robot_weld":  {"sensors": "plc_full", "params": ["weld_current", "torque"]},
    "manual_fit":  {"sensors": "checklist", "params": []},
    "paint_booth": {"sensors": "plc_full", "params": ["booth_temp", "humidity"]},
    "inspection":  {"sensors": "plc_full", "params": [], "inspection": True},
}

# jitter_s: sd of timestamp noise. clock_offset_s: fixed skew of that
# station's clock. drop_p: per-event loss. latency_s: reporting delay.
# params: whether process-parameter readings are reported. param_every: report
# one reading in N, the audit-sample case -- a torque gauge read on every tenth
# body, a weld-current log sampled to keep the historian small. Inspection
# results are reported by any non-dark profile (an inspector logs them).
BUILTIN_SENSOR_PROFILES: dict[str, dict] = {
    "plc_full":   {"events": "all", "jitter_s": 0.2, "params": True},
    # Cycle times fully instrumented, process parameters sampled. Common where
    # the parameter is read by a hand tool or logged at a reduced rate, and the
    # case where a hold has to be reconstructed from an estimated onset rather
    # than read off each vehicle's own reading.
    "plc_sampled": {"events": "all", "jitter_s": 0.2, "params": True, "param_every": 5},
    "cycle_only": {"events": ["start", "finish"], "jitter_s": 1.0, "drop_p": 0.01},
    "checklist":  {"events": ["finish"], "latency_s": 120.0, "jitter_s": 30.0, "drop_p": 0.05},
    "dark":       {"events": []},
    # external (Factory I/O) sources: photo-eyes at station entry and/or exit
    "photo_eyes": {"events": ["start", "finish", "move", "exit"], "jitter_s": 0.1},
    "exit_eye":   {"events": ["finish", "move", "exit"], "jitter_s": 0.1},
}


@dataclass(frozen=True)
class SensorProfile:
    name: str
    events: frozenset[str] | None       # None = all
    latency_s: float = 0.0
    drop_p: float = 0.0
    jitter_s: float = 0.0
    clock_offset_s: float = 0.0
    params: bool = False
    param_every: int = 1        # report one parameter reading in N

    def passes(self, kind: str) -> bool:
        if kind == "param":
            return self.params
        if kind in ("inspect", "rework"):
            return self.events is None or len(self.events) > 0
        return self.events is None or kind in self.events


@dataclass(frozen=True)
class ParamSpec:
    name: str
    nominal: float
    sd: float
    lsl: float
    usl: float
    unit: str = ""
    meas_sd: float = 0.0

    def z(self, x: float) -> float:
        return (x - self.nominal) / self.sd


@dataclass(frozen=True)
class ParamDrift:
    """Parameter's true mean ramps from nominal to `to` over `ramp_s` from `at_s`."""
    station: str
    param: str
    at_s: float
    to: float
    ramp_s: float = 0.0


@dataclass(frozen=True)
class DefectCause:
    station: str
    param: str
    below: float | None = None
    above: float | None = None

    def holds(self, x: float) -> bool:
        return (self.below is None or x < self.below) and (self.above is None or x > self.above)


@dataclass(frozen=True)
class DefectModel:
    """A latent defect occurs with probability `p` when every cause holds
    for the vehicle; it is only visible at `detected_at`, with `detect_p`."""
    name: str
    causes: tuple[DefectCause, ...]
    p: float
    detected_at: str
    detect_p: float = 1.0

    @property
    def last_cause_station(self) -> str:
        return self.causes[-1].station


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
    params: tuple[ParamSpec, ...] = ()
    capacity: int = 1                   # parallel workers sharing the buffer
    rework_p: float = 0.0               # inspection only: share of fails sent to rework
    rework_s: float = 0.0               # time in the rework bay before re-entering this station


@dataclass(frozen=True)
class Break:
    at_s: float
    duration_s: float


@dataclass(frozen=True)
class Shift:
    name: str
    start_s: float
    cycle_mult: dict[str, float] = field(default_factory=dict)   # operator variation by station


@dataclass(frozen=True)
class Calendar:
    breaks: tuple[Break, ...] = ()
    shifts: tuple[Shift, ...] = ()

    def in_break(self, t: float) -> Break | None:
        for b in self.breaks:
            if b.at_s <= t < b.at_s + b.duration_s:
                return b
        return None

    def break_overlap(self, t0: float, t1: float) -> float:
        return sum(max(0.0, min(t1, b.at_s + b.duration_s) - max(t0, b.at_s)) for b in self.breaks)

    def shift_at(self, t: float) -> Shift | None:
        cur = None
        for s in self.shifts:
            if t >= s.start_s:
                cur = s
        return cur


@dataclass(frozen=True)
class Economics:
    """Stated assumptions behind the ROI view. Override per plant."""
    downtime_cost_per_min: float = 8000.0      # this line's share; Siemens 2024: $38k/min for a whole auto plant
    bottleneck_events_per_week: float = 3.0
    prevented_share: float = 0.5                # warnings acted on in time
    hold_cost_per_vehicle: float = 250.0        # inspection, rework, delay
    escape_cost_per_defect: float = 5000.0      # field repair + warranty; 10x-100x-1000x ladder
    quality_events_per_month: float = 1.0
    sensor_cost_per_station: float = 500.0      # retrofit kit incl. install
    licence_per_line_per_year: float = 60000.0
    weeks_per_year: float = 48.0


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
    param_drifts: tuple[ParamDrift, ...] = ()
    defects: tuple[DefectModel, ...] = ()
    calendar: Calendar = field(default_factory=Calendar)
    economics: Economics = field(default_factory=Economics)

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
            float(p.get("jitter_s", 0.0)), float(p.get("clock_offset_s", 0.0)),
            bool(p.get("params", False)), max(1, int(p.get("param_every", 1))))
    return out


def _params(raw: dict) -> dict[str, ParamSpec]:
    merged = _deep_merge(BUILTIN_PARAMS, raw.get("libraries", {}).get("params", {}) or {})
    return {name: ParamSpec(name, float(p["nominal"]), float(p["sd"]), float(p["lsl"]),
                            float(p["usl"]), str(p.get("unit", "")), float(p.get("meas_sd", 0.0)))
            for name, p in merged.items()}


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
    params = _params(raw)
    default_buf = int(line.get("default_buffer", 2))

    stations: list[StationCfg] = []
    for zone in line["zones"]:
        for s in zone["stations"]:
            st_type = types[s.get("type", "generic")]
            prof = profiles[s.get("sensors", st_type.sensors)]
            names = s.get("params", st_type.params)
            for n in names:
                if n not in params:
                    raise ValueError(f"{path}: station {s['id']} uses unknown param {n!r}")
            stations.append(StationCfg(
                id=str(s["id"]), zone=str(zone["name"]), type=st_type,
                cycle_s=float(s["cycle_s"]),
                buffer_before=int(s.get("buffer_before", default_buf)),
                sensors=prof, params=tuple(params[n] for n in names),
                capacity=int(s.get("capacity", 1)),
                rework_p=float(s.get("rework_p", 0.0)), rework_s=float(s.get("rework_s", 0.0))))
            if stations[-1].rework_p and not st_type.inspection:
                raise ValueError(f"{path}: station {s['id']}: rework needs an inspection type")
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
    by_id = {s.id: s for s in stations}

    def _check_param(station: str, param: str, what: str) -> None:
        if station not in by_id:
            raise ValueError(f"{path}: {what} on unknown station {station}")
        if param not in {p.name for p in by_id[station].params}:
            raise ValueError(f"{path}: {what}: station {station} has no param {param!r}")

    drifts = []
    for d in scenario.get("param_drifts", []) or []:
        _check_param(d["station"], d["param"], "param drift")
        drifts.append(ParamDrift(str(d["station"]), str(d["param"]), float(d["at_s"]),
                                 float(d["to"]), float(d.get("ramp_s", 0))))
    defects = []
    for d in scenario.get("defects", []) or []:
        causes = []
        for c in d["causes"]:
            _check_param(c["station"], c["param"], f"defect {d['name']}")
            causes.append(DefectCause(str(c["station"]), str(c["param"]),
                                      None if c.get("below") is None else float(c["below"]),
                                      None if c.get("above") is None else float(c["above"])))
        if d["detected_at"] not in by_id:
            raise ValueError(f"{path}: defect {d['name']} detected at unknown station")
        # causes must be in line order so the defect materialises at the last one
        causes.sort(key=lambda c: [s.id for s in stations].index(c.station))
        defects.append(DefectModel(str(d["name"]), tuple(causes), float(d.get("p", 1.0)),
                                   str(d["detected_at"]), float(d.get("detect_p", 1.0))))

    cal = line.get("calendar") or {}
    breaks = tuple(Break(float(b["at_s"]), float(b["duration_s"])) for b in cal.get("breaks", []) or [])
    shifts = []
    for sh in cal.get("shifts", []) or []:
        for sid in sh.get("cycle_mult", {}):
            if sid not in ids:
                raise ValueError(f"{path}: shift {sh['name']} references unknown station {sid}")
        shifts.append(Shift(str(sh["name"]), float(sh["start_s"]),
                            {k: float(v) for k, v in sh.get("cycle_mult", {}).items()}))
    econ = Economics(**{k: float(v) for k, v in (raw.get("economics") or {}).items()})

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
        param_drifts=tuple(drifts),
        defects=tuple(defects),
        calendar=Calendar(breaks, tuple(shifts)),
        economics=econ,
    )
