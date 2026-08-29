"""Layer 3: Loom. Sees only what the sensor layer forwards.

The twin keeps a timeline per vehicle (arrive / start / finish / exit at
every station) and *always* reconstructs it from the flow rules of a
serial FIFO line; measurements pin values, inference fills the gaps:

  R1  arrive[i]   = exit[i-1]                      (a move is one instant)
  R2  start[i]    = max(arrive[i], exit[i] of the previous vehicle)
                                                  (a free station starts at once)
  R3  exit[i]     = finish[i]  unless the station was blocked
  R4  if station i was idle when it measurably started vehicle v, then v
      arrived exactly then, so the upstream station released v exactly
      then -- an exact sample of a dark upstream station's finish time.

R4 is the soft sensor for dark stations: it yields exact samples precisely
when the downstream neighbour is starved, i.e. when the dark station is
the bottleneck. Every stamp carries MEASURED or INFERRED; tolerances come
from the sensor profiles' declared jitter.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import LineCfg
from .events import (BLOCKED, EXIT, FINISH, INSPECT, LOST_SLOT, MOVE, PARAM,
                     RELEASE, START, Event)
from .forecast import Alert, Forecaster
from .quality import QualityTwin

MEASURED, INFERRED, SIMULATED = "measured", "inferred", "simulated"
MARK = {MEASURED: "●", INFERRED: "◐", SIMULATED: "○"}


@dataclass
class Tagged:
    value: object
    source: str            # MEASURED | INFERRED | SIMULATED
    t: float               # when this belief was last updated

    def __repr__(self) -> str:
        return f"{MARK[self.source]}{self.value}"


@dataclass
class Stamp:
    t: float
    source: str
    exact: bool = True      # False = a bound (e.g. "left no later than")


@dataclass
class VehicleTL:
    vid: int
    variant: str = "-"
    arrive: dict[int, Stamp] = field(default_factory=dict)
    start: dict[int, Stamp] = field(default_factory=dict)
    finish: dict[int, Stamp] = field(default_factory=dict)
    exit: dict[int, Stamp] = field(default_factory=dict)
    blocked: set[int] = field(default_factory=set)
    sampled: set[int] = field(default_factory=set)
    exited: bool = False


@dataclass
class StationBelief:
    state: Tagged
    vehicle: Tagged
    cycle_s: Tagged                 # fitted current cycle time
    health: str = "ok"              # ok | silent | inconsistent
    last_measured_t: float = 0.0
    measured_samples: int = 0
    inferred_samples: int = 0


@dataclass
class AlertLog:
    t: float
    action: str                     # "raised" | "cleared" | "grouped"
    alert: Alert
    cause: str | None = None        # for "grouped": the downstream root alert

    def __str__(self) -> str:
        if self.action == "raised":
            return str(self.alert)
        if self.action == "grouped":
            return (f"[{self.t:8.1f}] note  {self.alert.station} slow "
                    f"(cycle {self.alert.cycle_now:.1f}s) -- consequence of {self.cause}, not a new alert")
        return f"[{self.t:8.1f}] clear {self.alert.station}"


class Twin:
    # Run rules: a condition must persist before we raise, and must be
    # absent for a while before we clear. Cheap, standard, cuts false alarms.
    RAISE_AFTER = 3
    CLEAR_AFTER = 3
    SILENT_AFTER_TAKTS = 4

    def __init__(self, cfg: LineCfg) -> None:
        self.cfg = cfg
        self.n = len(cfg.stations)
        self.t = 0.0
        self.seen = 0
        self.exited = 0
        self.tl: dict[int, VehicleTL] = {}
        self.stations: dict[str, StationBelief] = {}
        self.forecaster = Forecaster(cfg.takt_s)
        self.active: dict[str, Alert] = {}
        self._hits: dict[str, int] = {}
        self._misses: dict[str, int] = {}
        self._grouped: set[str] = set()
        self.quality = QualityTwin(cfg, self)
        self.log: list[AlertLog] = []
        self.samples: dict[str, list[tuple[int, float, str]]] = {s.id: [] for s in cfg.stations}

        self._pending: list[set[int]] = [set() for _ in range(self.n)]   # arrived, not started
        self._last_started: list[int | None] = [None] * self.n
        self._first_vid: int | None = None

        jit = [s.sensors.jitter_s for s in cfg.stations]
        self._tol = [2 * (jit[i] + (jit[i - 1] if i else 0.0)) + 0.5 for i in range(self.n)]
        self._sees = [s.sensors for s in cfg.stations]
        for s in cfg.stations:
            self.stations[s.id] = StationBelief(
                state=Tagged("idle", MEASURED, 0.0),
                vehicle=Tagged(None, MEASURED, 0.0),
                cycle_s=Tagged(None, MEASURED, 0.0))

    # -- ingest ---------------------------------------------------------
    def ingest(self, ev: Event) -> None:
        self.seen += 1
        self.t = max(self.t, ev.t)
        if ev.kind == LOST_SLOT:
            return
        if ev.kind in (PARAM, INSPECT):
            self._tl(ev.vehicle, None)
            self.quality.ingest(ev)
            return
        tl = self._tl(ev.vehicle, ev.payload.get("variant"))
        i = self.cfg.index(ev.station) if ev.station else None
        if i is not None:
            self.stations[ev.station].last_measured_t = self.t
        if ev.kind == RELEASE:
            self._set(tl, "arrive", 0, ev.t, MEASURED)
        elif ev.kind == START:
            self._set(tl, "start", i, ev.t, MEASURED)
        elif ev.kind == FINISH:
            self._set(tl, "finish", i, ev.t, MEASURED)
        elif ev.kind == BLOCKED:
            tl.blocked.add(i)
        elif ev.kind == MOVE:
            self._set(tl, "exit", i, ev.t, MEASURED)
            self._set(tl, "arrive", i + 1, ev.t, MEASURED)
        elif ev.kind == EXIT:
            self._set(tl, "exit", i, ev.t, MEASURED)
            if not tl.exited:
                tl.exited = True
                self.exited += 1
        self._propagate(tl.vid)
        self._health()

    def _tl(self, vid: int, variant: str | None) -> VehicleTL:
        tl = self.tl.get(vid)
        if tl is None:
            tl = self.tl[vid] = VehicleTL(vid, variant or "-")
            if self._first_vid is None or vid < self._first_vid:
                self._first_vid = vid
        elif variant:
            tl.variant = variant
        return tl

    def _set(self, tl: VehicleTL, kind: str, i: int, t: float, source: str,
             exact: bool = True) -> bool:
        if i < 0 or i >= self.n:
            return False
        d: dict[int, Stamp] = getattr(tl, kind)
        cur = d.get(i)
        if cur is not None:
            if cur.source == MEASURED:
                return False                  # measured wins
            if source == INFERRED and (cur.exact or not exact):
                return False                  # exact inference beats a bound; else first sticks
        d[i] = Stamp(t, source, exact if source == INFERRED else True)
        if kind == "arrive":
            if i not in tl.start:
                self._pending[i].add(tl.vid)
            st = tl.start.get(i)
            if st and st.source == MEASURED and source == MEASURED and st.t < t - self._tol[i]:
                self.stations[self.cfg.ids[i]].health = "inconsistent"
        elif kind == "start":
            # FIFO: everything released before this vehicle has started too,
            # even if we never learned exactly when.
            self._pending[i] = {v for v in self._pending[i] if v > tl.vid}
            ls = self._last_started[i]
            if ls is None or tl.vid > ls:
                self._last_started[i] = tl.vid
            ar = tl.arrive.get(i)
            if ar and ar.source == MEASURED and source == MEASURED and t < ar.t - self._tol[i]:
                self.stations[self.cfg.ids[i]].health = "inconsistent"
        return True

    # -- reconstruction -------------------------------------------------
    def _propagate(self, vid: int) -> None:
        work = [vid]
        while work:
            v = work.pop()
            tl = self.tl.get(v)
            if tl is None:
                continue
            changed = True
            any_change = False
            while changed:
                changed = self._rules(tl)
                any_change |= changed
            if any_change:
                work += [w for w in (v + 1, v - 1) if w in self.tl]

    def _reliable(self, i: int, kind: str) -> bool:
        """A healthy sensor that reports `kind` makes absence informative:
        no start event means the station has not started."""
        return self._sees[i].passes(kind) and self.stations[self.cfg.ids[i]].health == "ok"

    @staticmethod
    def _lacks(d: dict[int, Stamp], i: int) -> bool:
        """No stamp yet, or only a bound (which an exact value may replace)."""
        s = d.get(i)
        return s is None or not s.exact

    def _congested_downstream(self, i: int, lookahead: int = 3) -> bool:
        """A finish-only station upstream of a bottleneck cannot separate
        work from waiting: its finish->exit inference is not exact."""
        for j in range(i + 1, min(self.n, i + 1 + lookahead)):
            sid = self.cfg.ids[j]
            if sid in self.active:
                return True
            c = self.stations[sid].cycle_s.value
            if c is not None and c > 1.05 * self.cfg.takt_s:
                return True
        return False

    def _rules(self, tl: VehicleTL) -> bool:
        changed = False
        prev = self.tl.get(tl.vid - 1)
        nxt = self.tl.get(tl.vid + 1)
        first = tl.vid == self._first_vid
        for i in range(self.n):
            # R1
            if i > 0:
                if self._lacks(tl.arrive, i) and i - 1 in tl.exit:
                    e = tl.exit[i - 1]
                    changed |= self._set(tl, "arrive", i, e.t, INFERRED, e.exact)
                if self._lacks(tl.exit, i - 1) and i in tl.arrive:
                    a = tl.arrive[i]
                    changed |= self._set(tl, "exit", i - 1, a.t, INFERRED, a.exact)
            # R5: the downstream station started this vehicle, so it had
            # left here by then -- a bound, good for state, not for cycles.
            if i < self.n - 1 and i not in tl.exit and i + 1 in tl.start:
                changed |= self._set(tl, "exit", i, tl.start[i + 1].t, INFERRED, False)
            # R4: measured start at an idle station pins the arrival
            st = tl.start.get(i)
            ar = tl.arrive.get(i)
            if st and st.source == MEASURED and (ar is None or not ar.exact):
                pe = prev.exit.get(i) if prev else None
                if first or (pe is not None and pe.t < st.t - self._tol[i]):
                    changed |= self._set(tl, "arrive", i, st.t, INFERRED)
            # R2
            if self._lacks(tl.start, i) and not self._reliable(i, START):
                pe = prev.exit.get(i) if prev else None
                if i in tl.arrive:
                    if first or prev is None or pe is not None:
                        a = tl.arrive[i]
                        if pe is None or a.t >= pe.t:
                            t0, ex = a.t, a.exact
                        else:
                            t0, ex = pe.t, pe.exact
                        changed |= self._set(tl, "start", i, t0, INFERRED, ex)
                elif pe is not None and i > 0 and nxt is not None:
                    # Arrival unknown, but the upstream station started the
                    # *next* vehicle at some time, so this one had left it by
                    # then. If that is no later than the previous vehicle
                    # leaving this station, this one was waiting and started
                    # exactly when the station freed up.
                    ub = nxt.start.get(i - 1)
                    if ub is not None and ub.t <= pe.t + self._tol[i]:
                        changed |= self._set(tl, "start", i, pe.t, INFERRED, pe.exact)
            # R3
            if i not in tl.blocked:
                sees_exit = self._reliable(i, EXIT) if i == self.n - 1 else self._reliable(i, MOVE)
                if (self._lacks(tl.exit, i) and i in tl.finish and not sees_exit
                        and not self._next_full(i)):
                    f = tl.finish[i]
                    exact = f.exact and not self._congested_downstream(i)
                    changed |= self._set(tl, "exit", i, f.t, INFERRED, exact)
                if self._lacks(tl.finish, i) and i in tl.exit and not self._reliable(i, FINISH):
                    e = tl.exit[i]
                    changed |= self._set(tl, "finish", i, e.t, INFERRED, e.exact)
            # cycle sample: only from exact stamps
            if (i not in tl.sampled and i in tl.start and i in tl.finish
                    and tl.start[i].exact and tl.finish[i].exact):
                tl.sampled.add(i)
                self._sample(tl, i)
        return changed

    def _next_full(self, i: int) -> bool:
        if i + 1 >= self.n:
            return False
        return len(self._pending[i + 1]) >= self.cfg.stations[i + 1].buffer_before

    def _sample(self, tl: VehicleTL, i: int) -> None:
        sid = self.cfg.ids[i]
        s, f = tl.start[i], tl.finish[i]
        c = f.t - s.t
        nominal = self.cfg.stations[i].cycle_s
        if not (0.2 * nominal < c < 5 * nominal):
            return                                   # jitter garbage
        source = MEASURED if (s.source == MEASURED and f.source == MEASURED) else INFERRED
        b = self.stations[sid]
        if source == MEASURED:
            b.measured_samples += 1
        else:
            b.inferred_samples += 1
        self.samples[sid].append((tl.vid, c, source))
        self.forecaster.observe(sid, f.t, c, source)
        fit = self.forecaster.fit(sid, f.t)
        if fit is not None:
            b.cycle_s = Tagged(round(fit.c_now, 1), INFERRED, f.t)
        cap = self.cfg.stations[i].buffer_before
        alert = self.forecaster.assess(sid, f.t, len(self._pending[i]), cap)
        self._track(sid, f.t, alert)

    def _downstream_root(self, station: str) -> str | None:
        """An active alert downstream explains slowness here (blocking)."""
        i = self.cfg.index(station)
        for j in range(i + 1, self.n):
            if self.cfg.ids[j] in self.active:
                return self.cfg.ids[j]
        return None

    def _track(self, station: str, t: float, alert: Alert | None) -> None:
        if alert is not None:
            self._misses[station] = 0
            self._hits[station] = self._hits.get(station, 0) + 1
            if station in self.active:
                self.active[station] = alert
            elif self._hits[station] >= self.RAISE_AFTER:
                root = self._downstream_root(station)
                if root is not None:
                    if station not in self._grouped:
                        self._grouped.add(station)
                        self.log.append(AlertLog(t, "grouped", alert, root))
                else:
                    self.active[station] = alert
                    self.log.append(AlertLog(t, "raised", alert))
        else:
            self._hits[station] = 0
            self._grouped.discard(station)
            if station in self.active:
                self._misses[station] = self._misses.get(station, 0) + 1
                if self._misses[station] >= self.CLEAR_AFTER:
                    self.log.append(AlertLog(t, "cleared", self.active.pop(station)))

    def _health(self) -> None:
        if self.t < 20 * self.cfg.takt_s:
            return
        for i, s in enumerate(self.cfg.stations):
            b = self.stations[s.id]
            if b.health == "inconsistent":
                continue
            instrumented = s.sensors.passes(START) or s.sensors.passes(FINISH)
            silent = instrumented and self.t - b.last_measured_t > self.SILENT_AFTER_TAKTS * self.cfg.takt_s
            new = "silent" if silent else "ok"
            if new != b.health:
                b.health = new
                if silent:
                    # Absence of events is no longer evidence: fill the gap
                    # for everything still on the line.
                    for tl in self.tl.values():
                        if not tl.exited:
                            self._propagate(tl.vid)

    # -- views ----------------------------------------------------------
    def station_state(self, i: int) -> tuple[Tagged, Tagged]:
        sid = self.cfg.ids[i]
        ls = self._last_started[i]
        if ls is None:
            return Tagged("idle", MEASURED, self.t), Tagged(None, MEASURED, self.t)
        tl = self.tl[ls]
        st = tl.start[i]
        ex = tl.exit.get(i)
        if ex is not None:
            src = MEASURED if (ex.source == MEASURED and st.source == MEASURED) else INFERRED
            return Tagged("idle", src, ex.t), Tagged(None, src, ex.t)
        sees_exit = self._sees[i].passes(MOVE) or self._sees[i].passes(EXIT)
        src = MEASURED if (st.source == MEASURED and sees_exit) else INFERRED
        state = "blocked" if i in tl.blocked else "busy"
        return Tagged(state, src, st.t), Tagged(ls, src, st.t)

    def buffer_count(self, i: int) -> Tagged:
        pend = self._pending[i]
        measured = self._sees[i].passes(START) and all(
            self.tl[v].arrive[i].source == MEASURED for v in pend)
        return Tagged(len(pend), MEASURED if measured else INFERRED, self.t)

    def in_transit(self) -> int:
        """Vehicles on the line whose position the twin cannot pin down."""
        placed = sum(len(p) for p in self._pending)
        placed += sum(1 for i in range(self.n) if self.station_state(i)[0].value != "idle")
        on_line = sum(1 for tl in self.tl.values() if not tl.exited)
        return max(0, on_line - placed)

    def refresh(self) -> None:
        for i, s in enumerate(self.cfg.stations):
            b = self.stations[s.id]
            b.state, b.vehicle = self.station_state(i)

    @property
    def buffers(self) -> dict[str, Tagged]:
        return {s.id: self.buffer_count(i) for i, s in enumerate(self.cfg.stations)}

    def snapshot(self) -> dict:
        """Same shape as Plant.truth() so the evaluator can diff them."""
        self.refresh()
        return {
            "t": self.t,
            "stations": {sid: {"state": b.state.value, "vehicle": b.vehicle.value}
                         for sid, b in self.stations.items()},
            "provenance": {sid: b.state.source for sid, b in self.stations.items()},
            "buffer_counts": {sid: tb.value for sid, tb in self.buffers.items()},
            "buffer_provenance": {sid: tb.source for sid, tb in self.buffers.items()},
        }
