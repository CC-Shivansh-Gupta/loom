"""Layer 2: instrumentation. The only path from plant to twin.

Each station's `SensorProfile` (from config) decides which plant events
escape and how degraded they are:

  events        which kinds pass (plc_full: all; cycle_only: start/finish;
                checklist: finish; dark: none)
  jitter_s      Gaussian timestamp noise
  clock_offset  fixed skew of that station's clock
  drop_p        per-event loss
  latency_s     reporting delay

Scenario `sensor_faults` silence a station's instrumentation for a window,
so the twin has to notice and fall back to inference.

Nothing here is ever corrected before delivery: the twin gets what a real
edge collector would get.
"""
from __future__ import annotations

import dataclasses
import heapq
import random
from typing import Callable

from .config import LineCfg, SensorProfile
from .events import Event

# Events with no station (release / lost_slot) belong to the line-level
# source, which we treat as always instrumented.
_LINE_PROFILE = SensorProfile("line", None)


class SensorLayer:
    def __init__(self, cfg: LineCfg, seed: int = 0) -> None:
        self.profiles: dict[str, SensorProfile] = {s.id: s.sensors for s in cfg.stations}
        self.faults = list(cfg.sensor_faults)
        self.subscribers: list[Callable[[Event], None]] = []
        self.rng = random.Random(seed ^ 0x5EED)
        self.passed = 0
        self.dropped = 0
        self.silenced = 0
        self._delayed: list[tuple[float, int, Event]] = []
        self._seq = 0
        self._param_seen: dict[tuple, int] = {}      # (station, param) -> readings offered

    def profile_for(self, station: str | None) -> SensorProfile:
        return self.profiles.get(station, _LINE_PROFILE) if station else _LINE_PROFILE

    def _faulted(self, station: str | None, t: float) -> bool:
        return any(f.station == station and f.at_s <= t < f.at_s + f.duration_s
                   for f in self.faults)

    def observe(self, ev: Event) -> None:
        """Called by the plant for every ground-truth event."""
        self._flush(ev.t)
        prof = self.profile_for(ev.station)
        if not prof.passes(ev.kind):
            self.dropped += 1
            return
        if self._faulted(ev.station, ev.t):
            self.silenced += 1
            return
        if prof.drop_p and self.rng.random() < prof.drop_p:
            self.dropped += 1
            return
        # Audit-sample parameter logging: one reading in N, deterministic in the
        # count rather than random, because that is how a plant actually does it
        # -- every tenth body off the line, not a coin flip per body.
        if ev.kind == "param" and prof.param_every > 1:
            k = (ev.station, ev.payload.get("param"))
            n = self._param_seen.get(k, 0)
            self._param_seen[k] = n + 1
            if n % prof.param_every:
                self.dropped += 1
                return
        t = ev.t + prof.clock_offset_s
        if prof.jitter_s:
            t += self.rng.gauss(0.0, prof.jitter_s)
        out = dataclasses.replace(ev, t=t) if t != ev.t else ev
        if prof.latency_s > 0:
            self._seq += 1
            heapq.heappush(self._delayed, (ev.t + prof.latency_s, self._seq, out))
        else:
            self._deliver(out)

    def _flush(self, now: float) -> None:
        while self._delayed and self._delayed[0][0] <= now:
            _, _, ev = heapq.heappop(self._delayed)
            self._deliver(ev)

    def _deliver(self, ev: Event) -> None:
        self.passed += 1
        for fn in self.subscribers:
            fn(ev)

    def coverage(self) -> dict[str, str]:
        return {sid: p.name for sid, p in self.profiles.items()}
