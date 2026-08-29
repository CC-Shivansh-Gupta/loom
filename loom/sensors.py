"""Layer 2: instrumentation. The only path from plant to twin.

Each station has a profile that decides which plant events escape. For
now the only profile is `full` (everything passes). Dark stations,
noise, latency and dropouts will be added as further profiles.
"""
from __future__ import annotations

from typing import Callable

from .events import Event


class SensorLayer:
    def __init__(self, profiles: dict[str, str] | None = None) -> None:
        self.profiles = profiles or {}       # station id -> profile name
        self.subscribers: list[Callable[[Event], None]] = []
        self.passed = 0
        self.dropped = 0

    def profile_for(self, station: str | None) -> str:
        return self.profiles.get(station, "full")

    def observe(self, ev: Event) -> None:
        """Called by the plant for every ground-truth event."""
        for out in self._filter(ev):
            self.passed += 1
            for fn in self.subscribers:
                fn(out)

    def _filter(self, ev: Event) -> list[Event]:
        profile = self.profile_for(ev.station)
        if profile == "full":
            return [ev]
        raise ValueError(f"unknown sensor profile {profile!r}")
