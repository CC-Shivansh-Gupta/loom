"""Bottleneck forecaster: the twin's first predictive mechanism.

Per station it keeps a short window of *measured* cycle times, fits a
line through them, and asks: if this trend continues, when does the
buffer feeding this station fill up (i.e. when does the upstream
station block)? The forward projection is a tiny simulation, so the
resulting ETA is tagged SIMULATED, not measured.

Alerts are only raised when the evidence is statistically solid: the
station is already over takt, or the upward slope is significant
(t-stat above `min_tstat`). That is the false-alarm guard.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass


@dataclass
class Alert:
    t: float
    station: str
    eta_s: float            # seconds until upstream station blocks
    cycle_now: float        # fitted cycle at time t
    slope: float            # s of cycle per s of wall time
    confidence: float       # 0..1
    basis: str              # "over_takt" | "trend"
    inferred_share: float = 0.0   # fraction of samples that were inferred

    def __str__(self) -> str:
        src = "" if self.inferred_share == 0 else f", {self.inferred_share:.0%} inferred"
        return (f"[{self.t:8.1f}] ALERT {self.station}: upstream blocks in "
                f"~{self.eta_s / 60:.1f} min  (cycle {self.cycle_now:.1f}s, "
                f"{self.slope * 60:+.2f}s/min, conf {self.confidence:.2f}, {self.basis}{src})")


@dataclass
class Fit:
    n: int
    c_now: float
    c_now_se: float         # standard error of the fitted value at `now`
    slope: float
    slope_se: float
    resid_sd: float

    @property
    def tstat(self) -> float:
        return self.slope / self.slope_se if self.slope_se > 0 else 0.0

    @property
    def over_z(self) -> float:
        """How many SEs the fitted cycle sits above `takt` (set by caller)."""
        return self._over_z

    _over_z: float = 0.0


def linfit(ts: list[float], cs: list[float], at: float) -> Fit:
    n = len(ts)
    mt = sum(ts) / n
    mc = sum(cs) / n
    sxx = sum((t - mt) ** 2 for t in ts)
    if sxx == 0:
        return Fit(n, mc, float("inf"), 0.0, float("inf"), 0.0)
    slope = sum((t - mt) * (c - mc) for t, c in zip(ts, cs)) / sxx
    resid = [c - (mc + slope * (t - mt)) for t, c in zip(ts, cs)]
    resid_sd = math.sqrt(sum(r * r for r in resid) / max(n - 2, 1))
    slope_se = resid_sd / math.sqrt(sxx)
    c_now_se = resid_sd * math.sqrt(1 / n + (at - mt) ** 2 / sxx)
    return Fit(n, mc + slope * (at - mt), c_now_se, slope, slope_se, resid_sd)


class Forecaster:
    # Defaults chosen by sweep (see docs/forecaster_tuning.md): 0 false
    # alarms over 5 x 8h healthy shifts at 5% CV, 6-11 min lead on a
    # 56->80s ramp, with the twin's RAISE_AFTER=3 persistence rule.
    def __init__(self, takt_s: float, *, window: int = 20, min_samples: int = 8,
                 min_tstat: float = 4.0, min_over_z: float = 2.0,
                 horizon_s: float = 1800.0, step_s: float = 5.0) -> None:
        self.takt = takt_s
        self.window = window
        self.min_samples = min_samples
        self.min_tstat = min_tstat
        self.min_over_z = min_over_z
        self.horizon = horizon_s
        self.step = step_s
        self.samples: dict[str, deque[tuple[float, float, str]]] = {}

    def observe(self, station: str, t: float, cycle_s: float, source: str = "measured") -> None:
        self.samples.setdefault(station, deque(maxlen=self.window)).append((t, cycle_s, source))

    def fit(self, station: str, t: float) -> Fit | None:
        s = self.samples.get(station)
        if not s or len(s) < self.min_samples:
            return None
        return linfit([x for x, _, _ in s], [y for _, y, _ in s], t)

    def inferred_share(self, station: str) -> float:
        s = self.samples.get(station)
        if not s:
            return 0.0
        return sum(1 for _, _, src in s if src != "measured") / len(s)

    def assess(self, station: str, t: float, queue: int, capacity: int) -> Alert | None:
        fit = self.fit(station, t)
        if fit is None:
            return None

        fit._over_z = ((fit.c_now - self.takt) / fit.c_now_se
                       if fit.c_now_se > 0 else 0.0)
        over = fit.over_z >= self.min_over_z
        trending = fit.slope > 0 and fit.tstat >= self.min_tstat
        if not (over or trending):
            return None

        # Forward-project queue growth. Upstream can deliver at most one
        # vehicle per takt; this station drains at 1/cycle(t').
        slope = fit.slope if trending else 0.0
        q, elapsed = float(queue), 0.0
        while elapsed < self.horizon:
            c = fit.c_now + slope * elapsed
            growth = 1 / self.takt - 1 / c          # veh/s, may be negative
            q = max(0.0, q + growth * self.step)
            elapsed += self.step
            if q >= capacity:
                break
        else:
            return None                             # never fills within horizon

        if over:
            conf = min(1.0, fit.over_z / (2 * self.min_over_z))
            basis = "over_takt"
        else:
            conf = min(1.0, fit.tstat / (2 * self.min_tstat))
            basis = "trend"
        # Inferred samples rest on flow assumptions; discount accordingly.
        share = self.inferred_share(station)
        conf *= 1.0 - 0.3 * share
        return Alert(t, station, elapsed, fit.c_now, fit.slope, conf, basis, share)
