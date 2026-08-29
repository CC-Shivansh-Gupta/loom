"""Single event schema used at every layer.

The plant emits these, the sensor layer filters/mutates them, the twin
consumes them. Keeping one flat type end-to-end is what lets us later
prove the twin only ever saw what the sensors let through.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Event kinds emitted by the plant. Kept as plain strings so the YAML
# sensor profiles can reference them by name later.
RELEASE = "release"        # vehicle placed on the line (enters buffer 0)
LOST_SLOT = "lost_slot"    # takt slot skipped because buffer 0 was full
START = "start"            # station began processing a vehicle
FINISH = "finish"          # station finished processing (may now block)
BLOCKED = "blocked"        # station finished but downstream buffer full
MOVE = "move"              # vehicle moved from a station into next buffer
EXIT = "exit"              # vehicle left the last station
PARAM = "param"            # process parameter reading at start: {param, value}
INSPECT = "inspect"        # inspection outcome at finish: {result, defects}
REWORK = "rework"          # failed vehicle re-enters the inspection station's buffer: {new_id, pass}


@dataclass(order=True)
class Event:
    t: float
    seq: int
    kind: str = field(compare=False)
    station: str | None = field(compare=False, default=None)
    vehicle: int | None = field(compare=False, default=None)
    payload: dict[str, Any] = field(compare=False, default_factory=dict)

    def __str__(self) -> str:
        v = f" v{self.vehicle}" if self.vehicle is not None else ""
        s = f" @{self.station}" if self.station else ""
        p = f" {self.payload}" if self.payload else ""
        return f"[{self.t:8.1f}] {self.kind:<9}{s}{v}{p}"
