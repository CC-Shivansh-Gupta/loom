"""Line definition loaded from YAML.

The line is data, not code: a different plant is a different YAML file.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class StationCfg:
    id: str
    zone: str
    cycle_s: float
    buffer_before: int      # capacity of the buffer feeding this station


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
    takt_s: float
    stations: tuple[StationCfg, ...]
    cv: float = 0.0                     # cycle-time coefficient of variation
    seed: int = 0
    perturbations: tuple[Perturbation, ...] = ()

    @property
    def ids(self) -> list[str]:
        return [s.id for s in self.stations]

    def index(self, station: str) -> int:
        return self.ids.index(station)


def load_line(path: str | Path) -> LineCfg:
    raw = yaml.safe_load(Path(path).read_text())
    default_buf = int(raw.get("default_buffer", 2))
    stations: list[StationCfg] = []
    for zone in raw["zones"]:
        for s in zone["stations"]:
            stations.append(
                StationCfg(
                    id=str(s["id"]),
                    zone=str(zone["name"]),
                    cycle_s=float(s["cycle_s"]),
                    buffer_before=int(s.get("buffer_before", default_buf)),
                )
            )
    if not stations:
        raise ValueError(f"{path}: line has no stations")
    ids = {s.id for s in stations}
    perts = []
    for p in raw.get("perturbations", []) or []:
        if p["station"] not in ids:
            raise ValueError(f"{path}: perturbation on unknown station {p['station']}")
        perts.append(Perturbation(str(p["station"]), float(p["at_s"]),
                                  float(p["cycle_s"]), float(p.get("ramp_s", 0))))
    return LineCfg(
        name=str(raw.get("name", Path(path).stem)),
        takt_s=float(raw["takt_s"]),
        stations=tuple(stations),
        cv=float(raw.get("cv", 0.0)),
        seed=int(raw.get("seed", 0)),
        perturbations=tuple(perts),
    )
