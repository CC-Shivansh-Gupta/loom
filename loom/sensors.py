"""Layer 2: instrumentation. The only path from plant to twin.

Each station's `SensorProfile` (from config) decides which plant events
escape. `plc_full` passes everything; `cycle_only` passes start/finish;
`checklist` passes finish only, late; `dark` passes nothing.

Latency and dropouts are applied here too, so the twin never learns
anything the real instrumentation could not have told it.
"""
from __future__ import annotations

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
        self.subscribers: list[Callable[[Event], None]] = []
        self.rng = random.Random(seed ^ 0x5EED)
        self.passed = 0
        self.dropped = 0
        self._delayed: list[tuple[float, int, Event]] = []
        self._seq = 0

    def profile_for(self, station: str | None) -> SensorProfile:
        return self.profiles.get(station, _LINE_PROFILE) if station else _LINE_PROFILE

    def observe(self, ev: Event) -> None:
        """Called by the plant for every ground-truth event."""
        self._flush(ev.t)
        prof = self.profile_for(ev.station)
        if not prof.passes(ev.kind) or (prof.drop_p and self.rng.random() < prof.drop_p):
            self.dropped += 1
            return
        if prof.latency_s > 0:
            self._seq += 1
            heapq.heappush(self._delayed, (ev.t + prof.latency_s, self._seq, ev))
        else:
            self._deliver(ev)

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
