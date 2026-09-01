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

  SampleRequest  What the twin does *instead* of a hold when the evidence
                 does not support one: pull k un-inspected vehicles chosen
                 so that their results separate the leading hypotheses.
                 A quality engineer facing two competing causes does not
                 hold the line, they pull a discriminating sample.
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
    released_t: float | None = None     # set when evidence later refuted the basis

    @property
    def size(self) -> int:
        return len(self.sure) + len(self.uncertain)

    def __str__(self) -> str:
        why = (f"drift at {self.station}.{self.param}" if self.reason == "drift"
               else f"inspection fails traced to {self.hypothesis}")
        return (f"[{self.t:8.1f}] HOLD #{self.id}: {self.size} vehicles "
                f"({len(self.sure)} sure, {len(self.uncertain)} uncertain, {len(self.exited)} already exited) -- {why}")


@dataclass
class SampleRequest:
    """What the twin asks for when it will not hold: inspect these
    vehicles next, because their results separate the leading hypotheses.

    `supports[vid]` names the hypothesis a *failure* on that vehicle would
    confirm -- every listed vehicle matches exactly one of the two
    candidate condition sets, so its result moves the evidence one way or
    the other rather than being consistent with both.
    """
    id: int
    t: float
    reason: str                     # "weak_evidence" | "unseparated"
    inspect_at: str
    vehicles: list[int]
    supports: dict[int, str]        # vid -> condition set its failure confirms
    top: Hypothesis
    rival: Hypothesis | None
    fails_seen: int
    # de-duplication key: (top set, rival set, fails seen when issued)
    _key: tuple = field(default=(), repr=False)

    def __str__(self) -> str:
        why = ("evidence too thin to hold" if self.reason == "weak_evidence"
               else "leading hypotheses not separable")
        return (f"[{self.t:8.1f}] SAMPLE #{self.id}: inspect {len(self.vehicles)} vehicles at "
                f"{self.inspect_at} -- {why} after {self.fails_seen} fails; "
                f"top {self.top}")


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

    # -- when to hold, and when to sample instead --------------------------
    # A hold is an action with a price: every vehicle in it is re-inspected,
    # reworked or scrapped. At a posterior below 0.5 the hold destroys more
    # good product than bad, which is exactly where a quality engineer stops
    # containing and starts sampling. The bar is the break-even point, not a
    # tuned constant.
    HOLD_MIN_POSTERIOR = 0.5
    # Two hypotheses are separable when the leader explains failures at least
    # half again as well as its best rival. Below that the inspection data
    # cannot say *which* condition set to contain, and holding the union
    # doubles the scrap without adding recall.
    HOLD_SEPARATION = 1.5
    # ...or the rival is this many times less likely to have produced the
    # data. An order of magnitude in a Fisher p-value is the conventional
    # "strong evidence" step, and it is the axis on which nested hypotheses
    # actually separate.
    HOLD_EVIDENCE_RATIO = 10.0
    # Minimum vehicles seen under a condition before its posterior is worth
    # believing. Under 5 the estimate swings by 20 points on one inspection,
    # so the twin neither holds nor refutes on it.
    MIN_JUDGE = 5
    # One audit basket. Under the true hypothesis roughly two thirds of a
    # discriminating sample fails and under the rival roughly a tenth, so
    # five results move the likelihood ratio by about two orders of
    # magnitude -- decisive -- at a few minutes of one inspector's time.
    # More than that is a hold by another name.
    SAMPLE_K = 5

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
        self.sample_requests: list[SampleRequest] = []
        self.abstained: list[Hold] = []          # opened, then refuted by inspection evidence
        self.precision_curve: list[dict] = []    # one entry per contribution run
        self._held: set[int] = set()
        self._fails_seen = 0
        self._fails_at: dict[str, int] = {}      # inspection station -> fails seen there
        self._drift_holds: dict[tuple[str, str], Hold] = {}      # open, growing
        self._insp_hold: Hold | None = None
        self._insp_station: str | None = None
        self._hyp_cache: tuple[int, list[Hypothesis]] = (-1, [])
        self._ev_cache: dict[tuple, Hypothesis | None] = {}

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
                supported = self._drift_basis_holds(ev.station, mon.active.direction, key)
                if hold is None:
                    # A drift is a warning until the process is actually out
                    # of spec; the first out-of-spec reading opens the hold
                    # and back-fills it from the estimated onset -- unless
                    # inspection evidence already says this condition on its
                    # own does not predict the defect.
                    if self._classify(x, self.specs[key], mon.active.direction) == "sure":
                        if supported:
                            self._hold_from_drift(mon.active, ev.t)
                        else:
                            self._abstain(ev.t, "weak_evidence",
                                          (Condition(ev.station, key[1],
                                                     self._band(mon.active.direction)),))
                elif supported:
                    self._add_by_reading(hold, ev.vehicle, x, mon.active.direction)
                else:
                    cond = Condition(ev.station, key[1], self._band(mon.active.direction))
                    self._refute(hold, ev.t, cond)
                    self._abstain(ev.t, "weak_evidence", (cond,))
                    if hold.hypothesis is not None:
                        self._add_by_reading(hold, ev.vehicle, x, mon.active.direction)
            elif key in self._drift_holds:
                del self._drift_holds[key]                        # drift over, hold closed
            if self._insp_hold is not None:
                self._extend_inspection_hold(ev.vehicle)
        elif ev.kind == INSPECT:
            self.inspected.setdefault(ev.vehicle, {})[ev.station] = ev.payload["result"]
            if ev.payload["result"] == "fail":
                self._fails_seen += 1
                self._fails_at[ev.station] = self._fails_at.get(ev.station, 0) + 1
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
        # a hold re-based on a multi-condition hypothesis only takes vehicles
        # the other conditions do not rule out
        if hold.hypothesis is not None:
            m = self._matches(vid, hold.hypothesis.conditions)
            if m is False:
                return
            if m is None:
                cls = "uncertain"
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

    def _crossed_at(self, a: DriftAlert, t: float) -> float:
        """When the parameter is estimated to have left spec, not when it
        started moving.

        A drift onset and a spec crossing are different instants, and the gap
        between them is silent-but-in-spec production: real parts, built to
        print, that a hold has no business scrapping. Where every vehicle
        reports its own reading the distinction costs nothing, because each
        reading decides its own vehicle and the in-spec ones are skipped. Where
        readings are sampled, the un-sampled vehicles are placed by time alone,
        and back-filling from the CUSUM onset sweeps the whole in-spec stretch
        in -- on `weld_drift_b2_sampled` the onset lands ~14 min before the
        drift actually starts, because at one reading in five each CUSUM step
        stands for five vehicles.

        Estimated from the readings themselves: the same least-squares line the
        ETA projects forward, solved backwards for where it crossed the limit.
        Clamped into [onset, now] -- an estimate outside the interval the drift
        is known to live in is not evidence, and falls back to the onset.
        """
        spec = self.specs[(a.station, a.param)]
        mon = self.monitors.get((a.station, a.param))
        if mon is None or len(mon.hist) < 3:
            return a.onset_t
        limit = spec.lsl if a.direction == "low" else spec.usl
        ts = [x for x, _ in mon.hist]
        xs = [y for _, y in mon.hist]
        n = len(ts)
        mt, mx = sum(ts) / n, sum(xs) / n
        sxx = sum((v - mt) ** 2 for v in ts)
        if sxx == 0:
            return a.onset_t
        slope = sum((v - mt) * (w - mx) for v, w in zip(ts, xs)) / sxx
        if slope == 0 or (slope < 0) != (a.direction == "low"):
            return a.onset_t                    # not moving the way the alert says
        cross = mt + (limit - mx) / slope
        return cross if a.onset_t <= cross <= t else a.onset_t

    def _hold_from_drift(self, a: DriftAlert, t: float) -> None:
        """Vehicles built since the parameter is estimated to have left spec,
        decided by their own reading where the station reports one; the onset
        margin and unknown readings go to `uncertain`. The hold keeps growing
        while the drift is on."""
        key = (a.station, a.param)
        spec = self.specs[key]
        margin = self.ONSET_MARGIN_TAKTS * self.cfg.takt_s
        onset = max(a.onset_t, self._crossed_at(a, t)) if self.backfill else t
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
        rival = self._rival(top, hyps)
        reason = self._why_not_hold(top, rival)
        # The curve the scenario is judged on: what the twin believed the
        # precision of a hold would be, against how many failures it had
        # seen when it believed it. Recorded whether or not it holds, so a
        # rising curve and a late hold are the same story.
        self.precision_curve.append({
            "fails": self._fails_seen, "t": t,
            "conditions": [str(c) for c in top.conditions],
            "posterior": round(top.p_defect_given, 3),
            "n_under": top.a + top.c,
            "rival": None if rival is None else [str(c) for c in rival.conditions],
            "rival_posterior": None if rival is None else round(rival.p_defect_given, 3),
            "action": "hold" if reason is None else "sample",
        })
        if reason is not None:
            self._abstain(t, reason, top.conditions, top=top, rival=rival)
            return
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

    # -- hold-or-sample gate ----------------------------------------------
    @staticmethod
    def _band(direction: str) -> str:
        return "<lsl" if direction == "low" else ">usl"

    def _fail_station(self) -> str | None:
        """Where the failures are being seen. The station with the most is
        the one whose pass/fail record can judge a hypothesis."""
        if not self._fails_at:
            return None
        return max(self._fails_at, key=lambda s: self._fails_at[s])

    def _condition_evidence(self, conds: tuple[Condition, ...]) -> Hypothesis | None:
        """Score `conds` against the inspection results seen so far, or None
        when there is not yet enough of a sample under it to judge -- the
        twin must not refuse a hold merely because nothing has been
        inspected yet.

        Consulted on every reading while a hold is open, so it is memoised
        on the inspection count: nothing in the table can move until another
        vehicle is inspected.
        """
        station = self._fail_station()
        if station is None or self._fails_seen < self.MIN_FAILS:
            return None
        key = (station, len(self.inspected), tuple(str(c) for c in conds))
        if key in self._ev_cache:
            return self._ev_cache[key]
        bad, good = self._bad_good(station)
        h = self._score(conds, bad, good, min_a=0) if len(bad) >= self.MIN_FAILS and good else None
        if h is not None and h.a + h.c < self.MIN_JUDGE:
            h = None
        self._ev_cache[key] = h
        return h

    @staticmethod
    def _posterior_ucb(a: int, n: int, z: float = 1.96) -> float:
        """Wilson upper bound on P(defect | condition).

        Withdrawing containment is itself a decision with a price, so it is
        taken on what the data *rules out*, not on a point estimate. That
        matters here for a structural reason: inspection happens minutes
        downstream of the cause station, so the vehicles that have been
        judged are systematically older than the ones being held, and early
        on the point estimate is both low and worthless. 2 of 5 has a point
        estimate of 0.40 and an upper bound of 0.77 -- the first refutes a
        good hold twenty minutes before it should, the second does not.
        """
        if n <= 0:
            return 1.0
        ph = a / n
        z2 = z * z
        centre = ph + z2 / (2 * n)
        half = z * math.sqrt(ph * (1 - ph) / n + z2 / (4 * n * n))
        return (centre + half) / (1 + z2 / n)

    def _drift_basis_holds(self, station: str, direction: str, key: tuple[str, str]) -> bool:
        """An out-of-spec reading opens a hold unless inspection evidence
        has since *ruled out* this condition on its own predicting the
        defect -- the multi-cause case, where the parameter is only causal
        in combination with another."""
        h = self._condition_evidence((Condition(station, key[1], self._band(direction)),))
        if h is None:
            return True
        return self._posterior_ucb(h.a, h.a + h.c) >= self.HOLD_MIN_POSTERIOR

    @staticmethod
    def _same(h1: Hypothesis, h2: Hypothesis) -> bool:
        return {str(c) for c in h1.conditions} == {str(c) for c in h2.conditions}

    def _rival(self, top: Hypothesis, hyps: list[Hypothesis]) -> Hypothesis | None:
        """The best-supported hypothesis that is not the leader. A nested
        set ("A" against "A AND B") counts: deciding between them is
        exactly the question a sample answers."""
        return next((h for h in hyps if not self._same(h, top)), None)

    def _why_not_hold(self, top: Hypothesis, rival: Hypothesis | None) -> str | None:
        """None when the evidence supports a hold, otherwise the reason it
        does not."""
        if top.p_defect_given < self.HOLD_MIN_POSTERIOR or top.a + top.c < self.MIN_JUDGE:
            return "weak_evidence"
        if rival is None:
            return None
        # Separable on either axis: the leader contains the failures markedly
        # better (posterior), or the data is markedly less likely under the
        # rival (significance). Nested candidates -- "A" against "A AND B" --
        # rarely separate on posterior alone, which is why the second test
        # exists; requiring both would abstain forever.
        if top.p_defect_given >= self.HOLD_SEPARATION * rival.p_defect_given:
            return None
        if rival.p_value >= self.HOLD_EVIDENCE_RATIO * top.p_value:
            return None
        return "unseparated"

    def _abstain(self, t: float, reason: str, conds: tuple[Condition, ...],
                 top: Hypothesis | None = None, rival: Hypothesis | None = None) -> None:
        """Hold refused: ask for the sample that would settle it instead."""
        station = self._fail_station()
        if station is None:
            return
        if top is None:
            top = self._condition_evidence(conds)
            if top is None:
                return
            rival = self._rival(top, self._ranked(station))
        if rival is None:
            return
        # One request per new failure at most: re-ranking runs on every
        # reading, and a request the line has not answered yet is not worth
        # re-issuing.
        key = (tuple(sorted(str(c) for c in top.conditions)),
               tuple(sorted(str(c) for c in rival.conditions)), self._fails_seen)
        if any(r._key == key for r in self.sample_requests):
            return
        vehicles, supports = self._discriminating(top, rival, station)
        if not vehicles:
            return
        req = SampleRequest(len(self.sample_requests) + 1, t, reason, station,
                            vehicles, supports, top, rival, self._fails_seen)
        req._key = key
        self.sample_requests.append(req)

    def _discriminating(self, top: Hypothesis, rival: Hypothesis,
                        inspect_station: str) -> tuple[list[int], dict[int, str]]:
        """Un-inspected vehicles matching exactly one of the two condition
        sets. A vehicle matching both (or neither) is consistent with both
        hypotheses, so inspecting it buys nothing; one that separates them
        moves the posterior of one and not the other.

        Taken alternately from each side so both hypotheses are tested, and
        earliest-built first because those leave the line soonest."""
        cand: dict[str, list[int]] = {"top": [], "rival": []}
        labels = {"top": top.conditions, "rival": rival.conditions}
        for vid, tl in sorted(self.twin.tl.items(), key=lambda kv: kv[0]):
            if inspect_station in self.inspected.get(vid, {}) or tl.exited:
                continue
            m = {k: self._matches(vid, cs) for k, cs in labels.items()}
            if m["top"] is True and m["rival"] is False:
                cand["top"].append(vid)
            elif m["rival"] is True and m["top"] is False:
                cand["rival"].append(vid)
        out: list[int] = []
        supports: dict[int, str] = {}
        while len(out) < self.SAMPLE_K and (cand["top"] or cand["rival"]):
            for side in ("top", "rival"):
                if cand[side] and len(out) < self.SAMPLE_K:
                    vid = cand[side].pop(0)
                    out.append(vid)
                    supports[vid] = " AND ".join(str(c) for c in labels[side])
        return out, supports

    def _ranked(self, station: str) -> list[Hypothesis]:
        """Contribution analysis, memoised on the failure count. Ranking is
        consulted on every reading once a hold is in doubt, and nothing in
        it can change until another inspection result arrives."""
        if self._hyp_cache[0] != self._fails_seen:
            self._hyp_cache = (self._fails_seen, self.contribution(station))
        return self._hyp_cache[1]

    def _best_superset(self, cond: Condition) -> Hypothesis | None:
        """The best-supported hypothesis that *extends* `cond` with a further
        condition -- the multi-cause case, where the parameter is causal only
        in combination."""
        station = self._fail_station()
        if station is None:
            return None
        for h in self._ranked(station):
            # matched on the parameter, not the exact band: the hold's band is
            # "out of spec", while contribution analysis usually names the
            # wider natural-variation band that actually carries the defect.
            if len(h.conditions) > 1 and any((c.station, c.param) == (cond.station, cond.param)
                                             for c in h.conditions) and \
                    h.p_defect_given >= self.HOLD_MIN_POSTERIOR and h.a + h.c >= self.MIN_JUDGE:
                return h
        return None

    def _refute(self, hold: Hold, t: float, cond: Condition) -> None:
        """Inspection evidence says this out-of-spec condition on its own
        does not predict the defect. If a better-supported hypothesis
        extends it, re-base the hold on that and drop the members the extra
        condition excludes; otherwise withdraw containment entirely. Either
        way the vehicles let go are recorded, not quietly forgotten."""
        better = self._best_superset(cond)
        if better is None:
            self._release(hold, t)
            return
        hold.hypothesis = better
        for group in (hold.sure, hold.uncertain):
            for vid in list(group):
                if self._matches(vid, better.conditions) is False:
                    group.remove(vid)
                    self._held.discard(vid)
                    if vid in hold.exited:
                        hold.exited.remove(vid)

    def _release(self, hold: Hold, t: float) -> None:
        """Containment withdrawn: the evidence that has arrived since the
        hold opened refutes its basis. The vehicles go back to the line and
        the hold is kept in `abstained` so the decision is auditable."""
        for vid in hold.sure + hold.uncertain:
            self._held.discard(vid)
        if hold in self.holds:
            self.holds.remove(hold)
        self._drift_holds.pop((hold.station, hold.param), None)
        hold.released_t = t
        self.abstained.append(hold)

    # -- contribution analysis -------------------------------------------
    def _bad_good(self, inspect_station: str) -> tuple[list[int], list[int]]:
        bad, good = [], []
        for vid, res in self.inspected.items():
            r = res.get(inspect_station)
            if r == "fail":
                bad.append(vid)
            elif r == "pass":
                good.append(vid)
        return bad, good

    def _score(self, cs: tuple[Condition, ...], bad: list[int], good: list[int],
               min_a: int = 2) -> Hypothesis | None:
        """The 2x2 table for a condition set. `min_a` is the number of
        failures the set must explain to be worth ranking; the hold gate
        passes 0 because a condition with *no* failures under it is
        precisely the one it wants to refute."""
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
        if a < min_a or a + b == 0 or c + d == 0:
            return None
        p_bad = a / (a + b)
        p_good = (c + 0.5) / (c + d + 1)          # smoothed
        return Hypothesis(cs, a, b, c, d, p_bad / p_good, fisher_right(a, b, c, d))

    def contribution(self, inspect_station: str, max_pairs: int | None = None) -> list[Hypothesis]:
        max_pairs = self.MAX_PAIRS if max_pairs is None else max_pairs
        insp_i = self.cfg.index(inspect_station)
        bad, good = self._bad_good(inspect_station)
        if len(bad) < self.MIN_FAILS or not good:
            return []
        conds = [Condition(s, p, band)
                 for (s, p) in self.specs if self.cfg.index(s) < insp_i
                 for band in ("<lsl", ">usl", "low", "high")]

        def score(cs: tuple[Condition, ...]) -> Hypothesis | None:
            return self._score(cs, bad, good)

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
