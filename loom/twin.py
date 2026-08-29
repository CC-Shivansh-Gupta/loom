"""Layer 3: Loom. Sees only what the sensor layer forwards.

Every value it holds carries a provenance tag so the UI can always say
whether it is showing a measurement or an estimate.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import LineCfg
from .events import BLOCKED, EXIT, FINISH, MOVE, RELEASE, START, Event

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
    last_cycle_s: Tagged
    _start_t: float | None = None


@dataclass
class Twin:
    cfg: LineCfg
    t: float = 0.0
    stations: dict[str, StationBelief] = field(default_factory=dict)
    buffers: dict[str, Tagged] = field(default_factory=dict)  # count feeding station
    seen: int = 0
    exited: int = 0

    def __post_init__(self) -> None:
        for s in self.cfg.stations:
            self.stations[s.id] = StationBelief(
                state=Tagged("idle", MEASURED, 0.0),
                vehicle=Tagged(None, MEASURED, 0.0),
                last_cycle_s=Tagged(None, MEASURED, 0.0),
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
                b.last_cycle_s = Tagged(ev.t - b._start_t, MEASURED, ev.t)
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

    # -- views ----------------------------------------------------------
    def snapshot(self) -> dict:
        """Same shape as Plant.truth() so the evaluator can diff them."""
        return {
            "t": self.t,
            "stations": {sid: {"state": b.state.value, "vehicle": b.vehicle.value}
                         for sid, b in self.stations.items()},
            "buffer_counts": {sid: tb.value for sid, tb in self.buffers.items()},
        }
