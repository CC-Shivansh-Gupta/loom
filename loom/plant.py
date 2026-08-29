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
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .config import LineCfg, StationCfg
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
    exited_t: float | None = None
    record: list[Visit] = field(default_factory=list)


@dataclass
class Station:
    cfg: StationCfg
    state: str = IDLE
    vehicle: Vehicle | None = None
    state_since: float = 0.0
    time_in: dict[str, float] = field(
        default_factory=lambda: {IDLE: 0.0, BUSY: 0.0, BLOCKED_STATE: 0.0})

    def set_state(self, state: str, t: float) -> None:
        self.time_in[self.state] += t - self.state_since
        self.state = state
        self.state_since = t

    def cycle_time(self, vehicle: Vehicle) -> float:
        # Deterministic for now. Variation, drift and slowdowns hook in here.
        return self.cfg.cycle_s


class Plant:
    def __init__(self, cfg: LineCfg) -> None:
        self.cfg = cfg
        self.t = 0.0
        self._seq = 0
        self._q: list[tuple[float, int, Callable[[], None]]] = []
        self.events: list[Event] = []
        self.listeners: list[Callable[[Event], None]] = []

        self.stations = [Station(s) for s in cfg.stations]
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

    # -- line logic ------------------------------------------------------
    def _source_tick(self) -> None:
        if len(self.buffers[0]) < self.stations[0].cfg.buffer_before:
            v = Vehicle(self._next_vid, self.t)
            self._next_vid += 1
            self.vehicles[v.id] = v
            self.buffers[0].append(v)
            self._emit(RELEASE, vehicle=v)
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
        self._schedule(st.cycle_time(v), lambda: self._finish(i))
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

    def wip(self) -> int:
        return len(self.vehicles) - len(self.exited)
