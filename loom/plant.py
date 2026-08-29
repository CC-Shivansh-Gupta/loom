"""Layer 1: the physical line as a discrete-event simulation.

This is ground truth. It knows everything and exposes it only through
the event stream (`Plant.events`) and, for the evaluator, `truth()`.

Topology: serial line, one buffer in front of every station.

    source -> [buf0] -> S0 -> [buf1] -> S1 -> ... -> S(n-1) -> sink

A station is `busy` while processing, `blocked` when it has finished but
the next buffer is full, and `idle` otherwise (idle with an empty input
buffer is what the floor calls "starved").
"""
from __future__ import annotations

import heapq
import math
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .config import LineCfg, Perturbation, StationCfg
from .events import (BLOCKED, EXIT, FINISH, LOST_SLOT, MOVE, RELEASE, START,
                     Event)

IDLE, BUSY, BLOCKED_STATE = "idle", "busy", "blocked"


@dataclass
class Visit:
    station: str
    start_t: float
    finish_t: float | None = None
    exit_t: float | None = None     # when it actually left (>= finish_t)


@dataclass
class Vehicle:
    id: int
    released_t: float
    variant: str = "-"
    exited_t: float | None = None
    record: list[Visit] = field(default_factory=list)


@dataclass
class Station:
    cfg: StationCfg
    perturbations: tuple[Perturbation, ...] = ()
    state: str = IDLE
    vehicle: Vehicle | None = None
    state_since: float = 0.0
    time_in: dict[str, float] = field(
        default_factory=lambda: {IDLE: 0.0, BUSY: 0.0, BLOCKED_STATE: 0.0})

    def set_state(self, state: str, t: float) -> None:
        self.time_in[self.state] += t - self.state_since
        self.state = state
        self.state_since = t

    def nominal_cycle(self, t: float) -> float:
        """True nominal cycle at time t, after any scheduled perturbations."""
        c = self.cfg.cycle_s
        for p in self.perturbations:
            if t < p.at_s:
                continue
            if p.ramp_s <= 0 or t >= p.at_s + p.ramp_s:
                c = p.cycle_s
            else:
                c = c + (p.cycle_s - c) * (t - p.at_s) / p.ramp_s
        return c

    def cycle_time(self, t: float, rng: random.Random, cv: float,
                   mult: float = 1.0) -> float:
        nominal = self.nominal_cycle(t) * mult
        if cv <= 0:
            return nominal
        # Lognormal with the requested CV, mean-corrected so E[cycle] == nominal.
        sigma = math.sqrt(math.log(1 + cv * cv))
        return nominal * math.exp(rng.gauss(-sigma * sigma / 2, sigma))


class Plant:
    def __init__(self, cfg: LineCfg) -> None:
        self.cfg = cfg
        self.t = 0.0
        self._seq = 0
        self._q: list[tuple[float, int, Callable[[], None]]] = []
        self.events: list[Event] = []
        self.listeners: list[Callable[[Event], None]] = []

        self.rng = random.Random(cfg.seed)
        self.stations = [
            Station(s, tuple(p for p in cfg.perturbations if p.station == s.id))
            for s in cfg.stations
        ]
        self.buffers: list[deque[Vehicle]] = [deque() for _ in cfg.stations]
        self.vehicles: dict[int, Vehicle] = {}
        self.exited: list[Vehicle] = []
        self._next_vid = 1

        self._schedule(0.0, self._source_tick)

    # -- event queue ------------------------------------------------------
    def _schedule(self, dt: float, fn: Callable[[], None]) -> None:
        self._seq += 1
        heapq.heappush(self._q, (self.t + dt, self._seq, fn))

    def _emit(self, kind: str, station: str | None = None,
              vehicle: Vehicle | None = None, **payload) -> None:
        self._seq += 1
        ev = Event(self.t, self._seq, kind, station,
                   vehicle.id if vehicle else None, payload)
        self.events.append(ev)
        for fn in self.listeners:
            fn(ev)

    def run(self, until: float) -> None:
        while self._q and self._q[0][0] <= until:
            self.t, _, fn = heapq.heappop(self._q)
            fn()
        self.t = until
        for s in self.stations:
            s.set_state(s.state, until)   # close out time-in-state

    # -- mixed model -----------------------------------------------------
    def _pick_variant(self) -> str:
        if not self.cfg.variants:
            return "-"
        r = self.rng.random()
        acc = 0.0
        for v in self.cfg.variants:
            acc += v.share
            if r < acc:
                return v.name
        return self.cfg.variants[-1].name

    def _variant_mult(self, variant: str, station: str) -> float:
        for v in self.cfg.variants:
            if v.name == variant:
                return v.cycle_mult.get(station, 1.0)
        return 1.0

    # -- line logic ------------------------------------------------------
    def _source_tick(self) -> None:
        if len(self.buffers[0]) < self.stations[0].cfg.buffer_before:
            v = Vehicle(self._next_vid, self.t, self._pick_variant())
            self._next_vid += 1
            self.vehicles[v.id] = v
            self.buffers[0].append(v)
            self._emit(RELEASE, vehicle=v, variant=v.variant)
            self._try_start(0)
        else:
            self._emit(LOST_SLOT)
        self._schedule(self.cfg.takt_s, self._source_tick)

    def _try_start(self, i: int) -> None:
        st = self.stations[i]
        if st.state != IDLE or not self.buffers[i]:
            return
        v = self.buffers[i].popleft()
        st.vehicle = v
        st.set_state(BUSY, self.t)
        v.record.append(Visit(st.cfg.id, self.t))
        self._emit(START, st.cfg.id, v)
        mult = self._variant_mult(v.variant, st.cfg.id)
        self._schedule(st.cycle_time(self.t, self.rng, self.cfg.cv, mult),
                       lambda: self._finish(i))
        # We freed a slot in buffer i; a blocked upstream station may move.
        if i > 0 and self.stations[i - 1].state == BLOCKED_STATE:
            self._try_push(i - 1)

    def _finish(self, i: int) -> None:
        st = self.stations[i]
        st.vehicle.record[-1].finish_t = self.t
        self._emit(FINISH, st.cfg.id, st.vehicle)
        self._try_push(i)

    def _try_push(self, i: int) -> None:
        st = self.stations[i]
        v = st.vehicle
        last = i == len(self.stations) - 1
        if not last and len(self.buffers[i + 1]) >= self.stations[i + 1].cfg.buffer_before:
            if st.state != BLOCKED_STATE:
                st.set_state(BLOCKED_STATE, self.t)
                self._emit(BLOCKED, st.cfg.id, v)
            return
        v.record[-1].exit_t = self.t
        st.vehicle = None
        st.set_state(IDLE, self.t)
        if last:
            v.exited_t = self.t
            self.exited.append(v)
            self._emit(EXIT, st.cfg.id, v)
        else:
            self.buffers[i + 1].append(v)
            self._emit(MOVE, st.cfg.id, v, to=self.stations[i + 1].cfg.id)
            self._try_start(i + 1)
        self._try_start(i)

    # -- ground truth for the evaluator ---------------------------------
    def truth(self) -> dict:
        return {
            "t": self.t,
            "stations": {
                s.cfg.id: {"state": s.state,
                           "vehicle": s.vehicle.id if s.vehicle else None}
                for s in self.stations
            },
            "buffers": {s.cfg.id: [v.id for v in b]
                        for s, b in zip(self.stations, self.buffers)},
        }

    def true_cycle(self, station: str, t: float) -> float:
        return self.stations[self.cfg.index(station)].nominal_cycle(t)

    def wip(self) -> int:
        return len(self.vehicles) - len(self.exited)
