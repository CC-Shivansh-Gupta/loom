"""Quality side of the twin: drift, latent defects, containment.

Three mechanisms, all statistical and explainable at a hold decision:

  ParamMonitor   EWMA + CUSUM per (station, parameter), baselined on the
                 spec. Raises a DriftAlert with an onset estimate (the
                 last time the CUSUM was at zero) and a projection of
                 when the mean reaches the spec limit.

  contribution   When inspections fail, every (station, parameter, band)
                 condition the vehicles passed through is scored by lift
                 and a Fisher exact test against the good vehicles; pairs
                 of conditions are scored too. Ranked hypotheses, not a
                 verdict.

  Hold           A targeted containment set: vehicles built under the
                 offending condition, split into sure / uncertain (built
                 inside the onset-estimate margin, or parameter unknown
                 because the station does not report it).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .config import LineCfg, ParamSpec
from .events import INSPECT, PARAM, Event

if TYPE_CHECKING:
    from .twin import Twin

MEASURED, INFERRED, SIMULATED = "measured", "inferred", "simulated"


# -- drift ------------------------------------------------------------------

@dataclass
class DriftAlert:
    t: float
    station: str
    param: str
    direction: str              # "low" | "high"
    mean_now: float             # EWMA-based estimate
    onset_t: float              # last time CUSUM was at zero
    t_to_limit_s: float | None  # projected seconds until mean crosses spec limit
    cusum: float

    def __str__(self) -> str:
        eta = "already out" if self.t_to_limit_s is not None and self.t_to_limit_s <= 0 else (
            "no crossing in horizon" if self.t_to_limit_s is None else f"limit in ~{self.t_to_limit_s / 60:.0f} min")
        return (f"[{self.t:8.1f}] DRIFT {self.station}.{self.param} {self.direction}: mean "
                f"{self.mean_now:.3f}, onset ~{self.onset_t / 60:.0f} min, {eta} (cusum {self.cusum:.1f})")


class ParamMonitor:
    # h=8 (not the textbook 5): a plant runs dozens of charts at once, so
    # the per-chart in-control run length must be very long (~10^4 samples).
    def __init__(self, spec: ParamSpec, *, k: float = 0.5, h: float = 8.0,
                 lam: float = 0.2, window: int = 30, horizon_s: float = 7200.0) -> None:
        self.spec = spec
        self.k, self.h, self.lam = k, h, lam
        self.ewma = 0.0
        self.c_hi = self.c_lo = 0.0
        self.hi_zero_t = self.lo_zero_t = 0.0
        self.n = 0
        self.hist: deque[tuple[float, float]] = deque(maxlen=window)
        self.active: DriftAlert | None = None
        self.horizon = horizon_s

    def update(self, t: float, x: float) -> DriftAlert | None:
        z = self.spec.z(x)
        self.n += 1
        self.ewma = z if self.n == 1 else self.lam * z + (1 - self.lam) * self.ewma
        self.hist.append((t, x))
        self.c_hi = max(0.0, self.c_hi + z - self.k)
        self.c_lo = max(0.0, self.c_lo - z - self.k)
        if self.c_hi == 0.0:
            self.hi_zero_t = t
        if self.c_lo == 0.0:
            self.lo_zero_t = t

        if self.active is None:
            if self.c_lo > self.h:
                self.active = self._alert(t, "low", self.lo_zero_t, self.c_lo)
                return self.active
            if self.c_hi > self.h:
                self.active = self._alert(t, "high", self.hi_zero_t, self.c_hi)
                return self.active
        else:
            back = (self.c_lo == 0.0) if self.active.direction == "low" else (self.c_hi == 0.0)
            if back:
                self.active = None
        return None

    def mean_now(self) -> float:
        return self.spec.nominal + self.ewma * self.spec.sd

    def _alert(self, t: float, direction: str, onset: float, cusum: float) -> DriftAlert:
        # project the mean with a line through the recent window
        ts = [a for a, _ in self.hist]
        xs = [b for _, b in self.hist]
        n = len(ts)
        mt, mx = sum(ts) / n, sum(xs) / n
        sxx = sum((a - mt) ** 2 for a in ts)
        slope = sum((a - mt) * (b - mx) for a, b in zip(ts, xs)) / sxx if sxx else 0.0
        m = self.mean_now()
        limit = self.spec.lsl if direction == "low" else self.spec.usl
        eta = None
        if (direction == "low" and m <= limit) or (direction == "high" and m >= limit):
            eta = 0.0
        elif (direction == "low" and slope < 0) or (direction == "high" and slope > 0):
            eta = (limit - m) / slope
            if eta > self.horizon:
                eta = None
        return DriftAlert(t, "", self.spec.name, direction, m, onset, eta, cusum)


# -- contribution analysis ----------------------------------------------------

@dataclass
class Condition:
    station: str
    param: str
    band: str                   # "<lsl" | ">usl" | "low" | "high"

    def holds(self, x: float, spec: ParamSpec) -> bool:
        if self.band == "<lsl":
            return x < spec.lsl
        if self.band == ">usl":
            return x > spec.usl
        if self.band == "low":
            return spec.z(x) < -1.5
        return spec.z(x) > 1.5

    def __str__(self) -> str:
        return f"{self.station}.{self.param} {self.band}"


@dataclass
class Hypothesis:
    conditions: tuple[Condition, ...]
    a: int              # defective & condition
    b: int              # defective & not
    c: int              # good & condition
    d: int              # good & not
    lift: float
    p_value: float

    @property
    def p_defect_given(self) -> float:
        return self.a / (self.a + self.c) if self.a + self.c else 0.0

    def __str__(self) -> str:
        cond = " AND ".join(str(c) for c in self.conditions)
        return (f"{cond}: lift {self.lift:.1f}x, {self.a}/{self.a + self.c} defective under it "
                f"vs {self.b}/{self.b + self.d} otherwise, p={self.p_value:.3g}")


def _log_comb(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_right(a: int, b: int, c: int, d: int) -> float:
    """P(X >= a) for the 2x2 table under independence (one-sided)."""
    n = a + b + c + d
    r1, c1 = a + b, a + c
    lo, hi = max(0, r1 + c1 - n), min(r1, c1)
    denom = _log_comb(n, c1)
    p = 0.0
    for x in range(a, hi + 1):
        p += math.exp(_log_comb(r1, x) + _log_comb(n - r1, c1 - x) - denom)
    return min(1.0, p)


# -- containment -------------------------------------------------------------

@dataclass
class Hold:
    id: int
    t: float
    reason: str                 # "drift" | "inspection"
    station: str
    param: str
    sure: list[int]
    uncertain: list[int]        # inside onset margin or parameter unknown
    exited: list[int]           # already left the line -- needs a yard check
    hypothesis: Hypothesis | None = None
    onset_t: float | None = None

    @property
    def size(self) -> int:
        return len(self.sure) + len(self.uncertain)

    def __str__(self) -> str:
        why = (f"drift at {self.station}.{self.param}" if self.reason == "drift"
               else f"inspection fails traced to {self.hypothesis}")
        return (f"[{self.t:8.1f}] HOLD #{self.id}: {self.size} vehicles "
                f"({len(self.sure)} sure, {len(self.uncertain)} uncertain, {len(self.exited)} already exited) -- {why}")


class QualityTwin:
    ONSET_MARGIN_TAKTS = 3
    MIN_FAILS = 3
    # Ablation knob (docs/ablation.md): with this off a hold starts at the
    # first out-of-spec reading instead of being back-filled to the
    # estimated drift onset -- i.e. every vehicle built during the silent
    # part of the drift is missed.
    backfill = True
    # Ablation knob: 0 disables the pair search in contribution analysis,
    # leaving single-condition hypotheses only.
    MAX_PAIRS = 5

    def __init__(self, cfg: LineCfg, twin: "Twin") -> None:
        self.cfg = cfg
        self.twin = twin
        self.specs: dict[tuple[str, str], ParamSpec] = {
            (s.id, p.name): p for s in cfg.stations for p in s.params}
        self.reports: set[str] = {s.id for s in cfg.stations if s.sensors.params}
        self.monitors: dict[tuple[str, str], ParamMonitor] = {
            key: ParamMonitor(spec) for key, spec in self.specs.items()}
        self.params: dict[int, dict[tuple[str, str], float]] = {}     # vid -> readings
        self.inspected: dict[int, dict[str, str]] = {}                # vid -> station -> result
        self.drift_log: list[DriftAlert] = []
        self.holds: list[Hold] = []
        self.hypotheses: list[Hypothesis] = []
        self._held: set[int] = set()
        self._fails_seen = 0
        self._drift_holds: dict[tuple[str, str], Hold] = {}      # open, growing
        self._insp_hold: Hold | None = None

    # -- ingest -----------------------------------------------------------
    def ingest(self, ev: Event) -> None:
        if ev.kind == PARAM:
            key = (ev.station, ev.payload["param"])
            x = float(ev.payload["value"])
            self.params.setdefault(ev.vehicle, {})[key] = x
            mon = self.monitors[key]
            alert = mon.update(ev.t, x)
            if alert is not None:
                alert.station = ev.station
                self.drift_log.append(alert)
            if mon.active is not None:
                hold = self._drift_holds.get(key)
                if hold is None:
                    # A drift is a warning until the process is actually out
                    # of spec; the first out-of-spec reading opens the hold
                    # and back-fills it from the estimated onset.
                    if self._classify(x, self.specs[key], mon.active.direction) == "sure":
                        self._hold_from_drift(mon.active, ev.t)
                else:
                    self._add_by_reading(hold, ev.vehicle, x, mon.active.direction)
            elif key in self._drift_holds:
                del self._drift_holds[key]                        # drift over, hold closed
            if self._insp_hold is not None:
                self._extend_inspection_hold(ev.vehicle)
        elif ev.kind == INSPECT:
            self.inspected.setdefault(ev.vehicle, {})[ev.station] = ev.payload["result"]
            if ev.payload["result"] == "fail":
                self._fails_seen += 1
                if self._fails_seen >= self.MIN_FAILS:
                    self._hold_from_inspection(ev.t, ev.station)

    # -- reading-based membership ------------------------------------------
    @staticmethod
    def _classify(x: float | None, spec: ParamSpec, direction: str) -> str | None:
        """'sure' (reading out of spec), 'uncertain' (no reading -- the
        station does not report), or None (reading in spec)."""
        if x is None:
            return "uncertain"
        out = x < spec.lsl if direction == "low" else x > spec.usl
        return "sure" if out else None

    def _add_by_reading(self, hold: Hold, vid: int, x: float | None, direction: str) -> None:
        if vid in self._held:
            return
        cls = self._classify(x, self.specs[(hold.station, hold.param)], direction)
        if cls is None:
            return
        (hold.sure if cls == "sure" else hold.uncertain).append(vid)
        self._held.add(vid)
        tl = self.twin.tl.get(vid)
        if tl is not None and tl.exited:
            hold.exited.append(vid)

    # -- who passed a station when --------------------------------------
    def _started_at(self, station: str) -> list[tuple[int, float, bool]]:
        """(vid, t, exact) for every vehicle believed to have started at station."""
        i = self.cfg.index(station)
        out = []
        for vid, tl in self.twin.tl.items():
            st = tl.start.get(i)
            if st is not None:
                out.append((vid, st.t, st.exact))
        return out

    def _hold_from_drift(self, a: DriftAlert, t: float) -> None:
        """Vehicles built since the estimated onset, decided by their own
        reading where the station reports one; the onset margin and
        unknown readings go to `uncertain`. The hold keeps growing while
        the drift is on."""
        key = (a.station, a.param)
        spec = self.specs[key]
        margin = self.ONSET_MARGIN_TAKTS * self.cfg.takt_s
        onset = a.onset_t if self.backfill else t
        hold = Hold(len(self.holds) + 1, t, "drift", a.station, a.param, [], [], [], onset_t=onset)
        for vid, t0, exact in sorted(self._started_at(a.station), key=lambda r: r[1]):
            if t0 < onset - margin or vid in self._held:
                continue
            x = self.params.get(vid, {}).get(key)
            cls = self._classify(x, spec, a.direction)
            if cls is None:
                continue
            if cls == "sure" and (t0 < onset or not exact):
                cls = "uncertain"
            (hold.sure if cls == "sure" else hold.uncertain).append(vid)
            self._held.add(vid)
            if self.twin.tl[vid].exited:
                hold.exited.append(vid)
        self.holds.append(hold)
        self._drift_holds[key] = hold

    def _hold_from_inspection(self, t: float, inspect_station: str) -> None:
        hyps = self.contribution(inspect_station)
        self.hypotheses = hyps
        if not hyps:
            return
        top = hyps[0]
        cur = self._insp_hold
        if cur is not None and cur.hypothesis is not None and \
                [str(c) for c in cur.hypothesis.conditions] == [str(c) for c in top.conditions]:
            cur.hypothesis = top                     # same cause: refresh the evidence
            return
        hold = Hold(len(self.holds) + 1, t, "inspection", top.conditions[0].station,
                    top.conditions[0].param, [], [], [], hypothesis=top)
        self._insp_hold = hold
        self._insp_station = inspect_station
        for vid in list(self.twin.tl):
            self._extend_inspection_hold(vid)
        if hold.size:
            self.holds.append(hold)

    def _extend_inspection_hold(self, vid: int) -> None:
        """Re-evaluated on every new reading: an 'uncertain' member is
        promoted to 'sure' or released once the missing reading arrives."""
        hold = self._insp_hold
        if hold is None or vid in self._held or hold.hypothesis is None:
            return
        if self._insp_station in self.inspected.get(vid, {}):
            return                                   # already inspected
        tl = self.twin.tl.get(vid)
        if tl is None:
            return
        conds = hold.hypothesis.conditions
        # must have passed every cause station already
        if not all(self.cfg.index(c.station) in tl.start for c in conds):
            return
        verdict = self._matches(vid, conds)
        was_uncertain = vid in hold.uncertain
        if verdict is False:
            if was_uncertain:
                hold.uncertain.remove(vid)
            return
        if verdict is None:
            if not was_uncertain:
                hold.uncertain.append(vid)
        else:
            if was_uncertain:
                hold.uncertain.remove(vid)
            hold.sure.append(vid)
            self._held.add(vid)
        if tl.exited and vid not in hold.exited:
            hold.exited.append(vid)
        if hold.size >= 1 and hold not in self.holds:
            self.holds.append(hold)

    def _matches(self, vid: int, conds: tuple[Condition, ...]) -> bool | None:
        """True / False, or None when a needed parameter is unknown."""
        unknown = False
        for c in conds:
            x = self.params.get(vid, {}).get((c.station, c.param))
            if x is None:
                unknown = True
                continue
            if not c.holds(x, self.specs[(c.station, c.param)]):
                return False
        return None if unknown else True

    # -- contribution analysis -------------------------------------------
    def contribution(self, inspect_station: str, max_pairs: int | None = None) -> list[Hypothesis]:
        max_pairs = self.MAX_PAIRS if max_pairs is None else max_pairs
        insp_i = self.cfg.index(inspect_station)
        bad, good = [], []
        for vid, res in self.inspected.items():
            r = res.get(inspect_station)
            if r == "fail":
                bad.append(vid)
            elif r == "pass":
                good.append(vid)
        if len(bad) < self.MIN_FAILS or not good:
            return []
        conds = [Condition(s, p, band)
                 for (s, p) in self.specs if self.cfg.index(s) < insp_i
                 for band in ("<lsl", ">usl", "low", "high")]

        def score(cs: tuple[Condition, ...]) -> Hypothesis | None:
            a = b = c = d = 0
            for vid in bad:
                m = self._matches(vid, cs)
                if m is True:
                    a += 1
                elif m is False:
                    b += 1
            for vid in good:
                m = self._matches(vid, cs)
                if m is True:
                    c += 1
                elif m is False:
                    d += 1
            if a < 2 or a + b == 0 or c + d == 0:
                return None
            p_bad = a / (a + b)
            p_good = (c + 0.5) / (c + d + 1)          # smoothed
            return Hypothesis(cs, a, b, c, d, p_bad / p_good, fisher_right(a, b, c, d))

        singles = [h for h in (score((c,)) for c in conds) if h is not None]
        singles.sort(key=lambda h: (h.p_value, -h.lift))
        top = singles[:max_pairs]
        pairs = []
        for i in range(len(top)):
            for j in range(i + 1, len(top)):
                if top[i].conditions[0].station == top[j].conditions[0].station and \
                        top[i].conditions[0].param == top[j].conditions[0].param:
                    continue
                h = score(top[i].conditions + top[j].conditions)
                if h is not None:
                    pairs.append(h)
        allh = [h for h in singles + pairs if h.p_value < 0.05 and h.lift > 1.5]
        # most significant first; a pair only outranks its singles when the
        # interaction genuinely explains more of the failures
        allh.sort(key=lambda h: (h.p_value, -h.lift))
        return allh[:8]

    # -- views ------------------------------------------------------------
    def first_pass_yield(self, station: str) -> tuple[int, int]:
        n = sum(1 for r in self.inspected.values() if station in r)
        ok = sum(1 for r in self.inspected.values() if r.get(station) == "pass")
        return ok, n
