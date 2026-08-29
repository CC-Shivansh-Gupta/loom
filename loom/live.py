"""Live simulation: the plant and the twin advancing in real time under
user control. The web server wraps this; nothing here knows about HTTP.
"""
from __future__ import annotations

import dataclasses
import threading
from pathlib import Path

import yaml

from . import views, voi
from .config import (BUILTIN_SENSOR_PROFILES, ParamDrift, Perturbation, SensorFault,
                     load_line)
from .evaluator import bottleneck_scorecard, containment_scorecard, hold_precision
from .export import HEALTH, SRC, STATE, snapshot
from .plant import Plant
from .sensors import SensorLayer
from .twin import Twin

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"


class LiveSim:
    def __init__(self, config: str = "healthy.yaml") -> None:
        self.lock = threading.RLock()
        self.speed = 30.0
        self.playing = False
        self.load_named(config)

    # -- lifecycle --------------------------------------------------------
    def load_named(self, name: str) -> None:
        path = CONFIG_DIR / name
        self.load_yaml(path.read_text(), source=name)

    def load_yaml(self, text: str, source: str = "editor") -> None:
        tmp = CONFIG_DIR / ".live.yaml"
        tmp.write_text(text)
        cfg = load_line(tmp)                      # raises ValueError on a bad config
        with self.lock:
            self.yaml_text = text
            self.source = source
            self.cfg = cfg
            self.plant = Plant(cfg)
            self.sensors = SensorLayer(cfg, cfg.seed)
            self.twin = Twin(cfg)
            self.plant.listeners.append(self.sensors.observe)
            self.sensors.subscribers.append(self.twin.ingest)
            self.t = 0.0
            self._events_sent = 0
            self._holds_sent = 0
            self._drifts_sent = 0
            self.injections: list[dict] = []

    def reset(self) -> None:
        self.load_yaml(self.yaml_text, self.source)

    def step(self, real_dt: float) -> None:
        if not self.playing:
            return
        with self.lock:
            self.t += real_dt * self.speed
            self.plant.run(self.t)

    # -- live injections ----------------------------------------------------
    def _station(self, sid: str):
        return self.plant.stations[self.cfg.index(sid)]

    def inject(self, kind: str, station: str, **kw) -> dict:
        with self.lock:
            now = self.plant.t
            st = self._station(station)
            if kind == "slow":
                p = Perturbation(station, now, float(kw.get("cycle_s", st.cfg.cycle_s * 1.4)),
                                 float(kw.get("ramp_s", 0.0)))
                st.perturbations = st.perturbations + (p,)
                self.cfg = dataclasses.replace(self.cfg, perturbations=self.cfg.perturbations + (p,))
                self.plant.cfg = self.cfg
                text = f"{station}: cycle -> {p.cycle_s:.0f}s over {p.ramp_s:.0f}s"
            elif kind == "restore":
                p = Perturbation(station, now, st.cfg.cycle_s, 0.0)
                st.perturbations = st.perturbations + (p,)
                self.cfg = dataclasses.replace(self.cfg, perturbations=self.cfg.perturbations + (p,))
                self.plant.cfg = self.cfg
                text = f"{station}: repaired, cycle back to {st.cfg.cycle_s:.0f}s"
            elif kind == "sensor":
                prof = voi._profile(kw["profile"])
                self.sensors.profiles[station] = prof
                self.twin.set_profile(station, prof)
                i = self.cfg.index(station)
                stations = list(self.cfg.stations)
                stations[i] = dataclasses.replace(stations[i], sensors=prof)
                self.cfg = dataclasses.replace(self.cfg, stations=tuple(stations))
                text = f"{station}: instrumentation -> {prof.name}"
            elif kind == "sensor_fault":
                f = SensorFault(station, now, float(kw.get("duration_s", 900)))
                self.sensors.faults.append(f)
                text = f"{station}: sensor link silent for {f.duration_s / 60:.0f} min"
            elif kind == "drift":
                param = kw["param"]
                spec = next(p for p in st.cfg.params if p.name == param)
                to = float(kw.get("to", spec.lsl - spec.sd))
                d = ParamDrift(station, param, now, to, float(kw.get("ramp_s", 1200)))
                st.drifts = st.drifts + (d,)
                self.cfg = dataclasses.replace(self.cfg, param_drifts=self.cfg.param_drifts + (d,))
                self.plant.cfg = self.cfg
                text = f"{station}.{param}: mean -> {to:g} over {d.ramp_s / 60:.0f} min"
            elif kind == "drift_stop":
                for p in st.cfg.params:
                    d = ParamDrift(station, p.name, now, p.nominal, 0.0)
                    st.drifts = st.drifts + (d,)
                text = f"{station}: parameters back to nominal"
            else:
                raise ValueError(f"unknown injection {kind!r}")
            rec = {"t": round(now), "kind": kind, "station": station, "text": text}
            self.injections.append(rec)
            return rec

    # -- outputs -------------------------------------------------------------
    def frame(self) -> dict:
        with self.lock:
            f = snapshot(self.cfg, self.plant, self.twin, self.plant.t)
            f["playing"] = self.playing
            f["speed"] = self.speed
            f["new_events"] = self._new_events()
            return f

    def _new_events(self) -> list[dict]:
        out = []
        log = self.twin.log
        while self._events_sent < len(log):
            x = log[self._events_sent]
            out.append({"t": round(x.t), "kind": x.action, "station": x.alert.station, "text": str(x)})
            self._events_sent += 1
        q = self.twin.quality
        while self._drifts_sent < len(q.drift_log):
            a = q.drift_log[self._drifts_sent]
            out.append({"t": round(a.t), "kind": "drift", "station": a.station, "text": str(a)})
            self._drifts_sent += 1
        while self._holds_sent < len(q.holds):
            h = q.holds[self._holds_sent]
            out.append({"t": round(h.t), "kind": "hold", "station": h.station, "text": str(h)})
            self._holds_sent += 1
        return out

    def meta(self) -> dict:
        with self.lock:
            cfg = self.cfg
            return {
                "id": cfg.name, "plant": cfg.plant, "takt_s": cfg.takt_s, "source": self.source,
                "stations": [{"id": s.id, "zone": s.zone, "type": s.type.name, "sensors": s.sensors.name,
                              "cycle_s": s.cycle_s, "buffer": s.buffer_before, "inspection": s.type.inspection,
                              "params": [p.name for p in s.params]} for s in cfg.stations],
                "profiles": list(BUILTIN_SENSOR_PROFILES),
                "scenarios": sorted(p.name for p in CONFIG_DIR.glob("*.yaml") if not p.name.startswith(".")),
                "yaml": self.yaml_text,
                "coverage": self.sensors.coverage(),
            }

    def station_detail(self, sid: str) -> dict:
        with self.lock:
            i = self.cfg.index(sid)
            s = self.cfg.stations[i]
            st = self.plant.stations[i]
            b = self.twin.stations[sid]
            self.twin.refresh()
            truth_cycles = []
            for v in list(self.plant.vehicles.values())[-80:]:
                for x in v.record:
                    if x.station == sid and x.finish_t is not None:
                        truth_cycles.append([v.id, round(x.finish_t - x.start_t, 1)])
            belief_cycles = [[vid, round(c, 1), SRC[src]] for vid, c, src in self.twin.samples[sid][-40:]]
            q = self.twin.quality
            params = []
            for spec in s.params:
                readings = [[vid, q.params[vid][(sid, spec.name)]] for vid in sorted(q.params)
                            if (sid, spec.name) in q.params[vid]][-40:]
                truth = [[v.id, round(v.param(sid, spec.name), 3)] for v in list(self.plant.vehicles.values())[-40:]
                         if v.param(sid, spec.name) is not None]
                m = q.monitors[(sid, spec.name)]
                params.append({"name": spec.name, "unit": spec.unit, "nominal": spec.nominal, "lsl": spec.lsl,
                               "usl": spec.usl, "reported": sid in q.reports,
                               "true_mean_now": round(st.param_mean(spec.name, self.plant.t), 3),
                               "readings": readings, "truth": truth,
                               "ewma": round(m.ewma, 2), "cusum_lo": round(m.c_lo, 1), "cusum_hi": round(m.c_hi, 1),
                               "drift": None if m.active is None else str(m.active)})
            a = self.twin.active.get(sid)
            fit = self.twin.forecaster.fit(sid, self.twin.t)
            held = [h.id for h in q.holds if h.station == sid]
            return {
                "id": sid, "zone": s.zone, "type": s.type.name, "sensors": s.sensors.name,
                "nominal_cycle_s": s.cycle_s, "true_cycle_now": round(st.nominal_cycle(self.plant.t), 1),
                "buffer_cap": s.buffer_before,
                "truth": {"state": st.state, "vehicle": st.vehicle.id if st.vehicle else None,
                          "buffer": len(self.plant.buffers[i]),
                          "busy_pct": round(100 * st.time_in["busy"] / max(sum(st.time_in.values()), 1e-9)),
                          "blocked_pct": round(100 * st.time_in["blocked"] / max(sum(st.time_in.values()), 1e-9))},
                "belief": {"state": b.state.value, "state_src": b.state.source, "vehicle": b.vehicle.value,
                           "buffer": self.twin.buffer_count(i).value, "buffer_src": self.twin.buffer_count(i).source,
                           "cycle": b.cycle_s.value, "health": b.health,
                           "slope_s_per_min": None if fit is None else round(fit.slope * 60, 2),
                           "tstat": None if fit is None else round(fit.tstat, 1),
                           "measured_samples": b.measured_samples, "inferred_samples": b.inferred_samples},
                "alert": None if a is None else {"eta_min": round(a.eta_s / 60, 1), "confidence": round(a.confidence, 2),
                                                 "basis": a.basis, "inferred_share": round(a.inferred_share, 2)},
                "truth_cycles": truth_cycles[-40:], "belief_cycles": belief_cycles, "params": params,
                "holds": held,
            }

    def view(self, role: str) -> str:
        with self.lock:
            if role == "supervisor":
                return views.supervisor(self.twin)
            if role == "quality":
                return views.quality(self.twin)
            if role == "maintenance":
                return views.maintenance(self.twin)
            if role == "manager":
                sc = bottleneck_scorecard(self.plant, self.twin)
                return views.manager(self.twin, sc, self.sensors.coverage())
            raise ValueError(role)

    def scorecard(self) -> dict:
        with self.lock:
            sc = bottleneck_scorecard(self.plant, self.twin)
            cont = containment_scorecard(self.plant, self.twin)
            return {
                "bottleneck": [{"station": s.station, "lead_min": None if s.lead_s is None else round(s.lead_s / 60, 1),
                                "eta_err_min": None if s.eta_error_s is None else round(s.eta_error_s / 60, 1),
                                "alert_min": None if s.t_alert is None else round(s.t_alert / 60, 1),
                                "block_min": None if s.t_upstream_blocked is None else round(s.t_upstream_blocked / 60, 1),
                                "conf": s.alert_conf} for s in sc["scores"]],
                "false_alarms": len(sc["false_alarms"]), "alerts_raised": sc["alerts_raised"],
                "containment": [{"defect": c.defect, "hold_min": None if c.t_first_hold is None else round(c.t_first_hold / 60, 1),
                                 "first_fail_min": None if c.t_first_fail is None else round(c.t_first_fail / 60, 1),
                                 "hold_size": c.hold_size, "precision": c.precision, "recall": c.recall,
                                 "blanket": c.blanket_size, "escaped": c.escaped} for c in cont],
                "holds": [{"id": h.id, "reason": h.reason, "station": h.station, "param": h.param,
                           "sure": len(h.sure), "uncertain": len(h.uncertain), "tp_sure": tps, "tp_unc": tpu}
                          for h, tps, tpu in hold_precision(self.plant, self.twin)],
                "injections": self.injections,
            }
