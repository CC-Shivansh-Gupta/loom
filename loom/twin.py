"""Layer 3: Loom. Sees only what the sensor layer forwards.

Every value it holds carries a provenance tag so the UI can always say
whether it is showing a measurement or an estimate.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import LineCfg
from .events import BLOCKED, EXIT, FINISH, MOVE, RELEASE, START, Event
from .forecast import Alert, Forecaster

MEASURED, INFERRED, SIMULATED = "measured", "inferred", "simulated"


@dataclass
class Tagged:
    value: object
    source: str            # MEASURED | INFERRED | SIMULATED
    t: float               # when this belief was last updated

    def __repr__(self) -> str:
        mark = {MEASURED: "●", INFERRED: "◐", SIMULATED: "○"}[self.source]
        return f"{mark}{self.value}"


@dataclass
class StationBelief:
    state: Tagged
    vehicle: Tagged
    cycle_s: Tagged                 # fitted current cycle time
    _start_t: float | None = None


@dataclass
class AlertLog:
    t: float
    action: str                     # "raised" | "cleared"
    alert: Alert

    def __str__(self) -> str:
        if self.action == "raised":
            return str(self.alert)
        return f"[{self.t:8.1f}] clear {self.alert.station}"


@dataclass
class Twin:
    cfg: LineCfg
    t: float = 0.0
    stations: dict[str, StationBelief] = field(default_factory=dict)
    buffers: dict[str, Tagged] = field(default_factory=dict)  # count feeding station
    forecaster: Forecaster = field(init=False)
    active: dict[str, Alert] = field(default_factory=dict)    # live alerts by station
    _hits: dict[str, int] = field(default_factory=dict)       # consecutive positives
    _misses: dict[str, int] = field(default_factory=dict)     # consecutive negatives
    log: list[AlertLog] = field(default_factory=list)
    seen: int = 0
    exited: int = 0
    # Run rules: a condition must persist before we raise, and must be
    # absent for a while before we clear. Cheap, standard, cuts false alarms.
    RAISE_AFTER = 3
    CLEAR_AFTER = 3

    def __post_init__(self) -> None:
        self.forecaster = Forecaster(self.cfg.takt_s)
        for s in self.cfg.stations:
            self.stations[s.id] = StationBelief(
                state=Tagged("idle", MEASURED, 0.0),
                vehicle=Tagged(None, MEASURED, 0.0),
                cycle_s=Tagged(None, MEASURED, 0.0),
            )
            self.buffers[s.id] = Tagged(0, MEASURED, 0.0)

    # -- ingest ---------------------------------------------------------
    def ingest(self, ev: Event) -> None:
        self.seen += 1
        self.t = ev.t
        if ev.kind == RELEASE:
            self._bump(self.cfg.ids[0], +1, ev.t)
        elif ev.kind == START:
            b = self.stations[ev.station]
            self._bump(ev.station, -1, ev.t)
            b.state = Tagged("busy", MEASURED, ev.t)
            b.vehicle = Tagged(ev.vehicle, MEASURED, ev.t)
            b._start_t = ev.t
        elif ev.kind == FINISH:
            b = self.stations[ev.station]
            if b._start_t is not None:
                self._on_cycle(ev.station, ev.t, ev.t - b._start_t)
        elif ev.kind == BLOCKED:
            self.stations[ev.station].state = Tagged("blocked", MEASURED, ev.t)
        elif ev.kind == MOVE:
            b = self.stations[ev.station]
            b.state = Tagged("idle", MEASURED, ev.t)
            b.vehicle = Tagged(None, MEASURED, ev.t)
            self._bump(ev.payload["to"], +1, ev.t)
        elif ev.kind == EXIT:
            b = self.stations[ev.station]
            b.state = Tagged("idle", MEASURED, ev.t)
            b.vehicle = Tagged(None, MEASURED, ev.t)
            self.exited += 1

    def _bump(self, station: str, delta: int, t: float) -> None:
        cur = self.buffers[station]
        self.buffers[station] = Tagged(cur.value + delta, MEASURED, t)

    def _on_cycle(self, station: str, t: float, cycle: float) -> None:
        self.forecaster.observe(station, t, cycle)
        fit = self.forecaster.fit(station, t)
        if fit is not None:
            self.stations[station].cycle_s = Tagged(round(fit.c_now, 1), INFERRED, t)
        cap = self.cfg.stations[self.cfg.index(station)].buffer_before
        alert = self.forecaster.assess(station, t, self.buffers[station].value, cap)
        self._track(station, t, alert)

    def _track(self, station: str, t: float, alert: Alert | None) -> None:
        if alert is not None:
            self._misses[station] = 0
            self._hits[station] = self._hits.get(station, 0) + 1
            if station in self.active:
                self.active[station] = alert
            elif self._hits[station] >= self.RAISE_AFTER:
                self.active[station] = alert
                self.log.append(AlertLog(t, "raised", alert))
        else:
            self._hits[station] = 0
            if station in self.active:
                self._misses[station] = self._misses.get(station, 0) + 1
                if self._misses[station] >= self.CLEAR_AFTER:
                    self.log.append(AlertLog(t, "cleared", self.active.pop(station)))

    # -- views ----------------------------------------------------------
    def snapshot(self) -> dict:
        """Same shape as Plant.truth() so the evaluator can diff them."""
        return {
            "t": self.t,
            "stations": {sid: {"state": b.state.value, "vehicle": b.vehicle.value}
                         for sid, b in self.stations.items()},
            "buffer_counts": {sid: tb.value for sid, tb in self.buffers.items()},
        }
