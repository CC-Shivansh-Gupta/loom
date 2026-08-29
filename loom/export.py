"""Export a scenario as a compact timeline for the web view.

    python -m loom.export configs/ramp_b3_dark.yaml --hours 2 --step 10 --out web/data/ramp_b3_dark.json

Every `step` seconds: plant truth (where every vehicle is), twin belief
(where it thinks they are, with provenance), buffers, cycles, alerts,
momentary bottleneck. Persona texts every 60 s. Scorecards at the end.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import views, voi
from .evaluator import bottleneck_scorecard, containment_scorecard, hold_precision
from .plant import BLOCKED_STATE, BUSY, IDLE
from .run import build

STATE = {"idle": 0, "busy": 1, "blocked": 2}
SRC = {"measured": 0, "inferred": 1, "simulated": 2}
HEALTH = {"ok": 0, "silent": 1, "inconsistent": 2}


def snapshot(cfg, plant, twin, t: float) -> dict:
    twin.refresh()
    bufs = twin.buffers
    st_rows = []
    for i, (st, buf) in enumerate(zip(plant.stations, plant.buffers)):
        b = twin.stations[st.cfg.id]
        a = twin.active.get(st.cfg.id)
        st_rows.append([
            STATE[st.state], st.vehicle.id if st.vehicle else None,
            STATE[b.state.value], SRC[b.state.source], b.vehicle.value,
            len(buf), bufs[st.cfg.id].value, SRC[bufs[st.cfg.id].source],
            None if b.cycle_s.value is None else round(b.cycle_s.value, 1),
            round(st.nominal_cycle(t), 1),
            None if a is None else round(a.eta_s / 60, 1),
            None if a is None else round(a.confidence, 2),
            HEALTH[b.health],
        ])
    # truth: every vehicle's location and progress
    veh = []
    for i, st in enumerate(plant.stations):
        if st.vehicle is not None:
            v = st.vehicle
            prog = min(1.0, (t - v.record[-1].start_t) / max(st.nominal_cycle(t), 1e-9))
            veh.append([v.id, 1, i, round(prog, 2), v.variant])
        for k, v in enumerate(plant.buffers[i]):
            veh.append([v.id, 0, i, k, v.variant])
    # belief: where the twin places vehicles (src), unplaced = -1
    bel = []
    placed = set()
    for i in range(twin.n):
        s, vt = twin.station_state(i)
        if vt.value is not None:
            bel.append([vt.value, 1, i, SRC[vt.source]])
            placed.add(vt.value)
        for k, vid in enumerate(sorted(twin._pending[i])):
            bel.append([vid, 0, i, SRC[twin.buffer_count(i).source]])
            placed.add(vid)
    for v in veh:
        if v[0] not in placed:
            bel.append([v[0], -1, -1, 1])
    bn = twin.bottleneck_now()
    return {
        "t": round(t), "st": st_rows, "veh": veh, "bel": bel,
        "out": [len(plant.exited), twin.exited],
        "bn": None if bn is None else [bn[0], round(bn[1] / 60, 1), SRC[bn[2]]],
        "holds": [[h.id, len(h.sure), len(h.uncertain)] for h in twin.quality.holds],
    }


def export(cfg_path: str, hours: float, step: float, persona_every: float = 60.0) -> dict:
    cfg, plant, sensors, twin = build(cfg_path)
    frames, personas, log_seen = [], {}, 0
    events = []
    t = 0.0
    end = hours * 3600
    while t <= end + 1e-9:
        plant.run(t)
        frames.append(snapshot(cfg, plant, twin, t))
        # newly logged twin events since last frame
        while log_seen < len(twin.log):
            x = twin.log[log_seen]
            events.append({"t": round(x.t), "kind": x.action, "station": x.alert.station,
                           "text": str(x), "eta_min": round(x.alert.eta_s / 60, 1),
                           "conf": round(x.alert.confidence, 2)})
            log_seen += 1
        if int(t) % int(persona_every) == 0:
            personas[str(round(t))] = {
                "supervisor": views.supervisor(twin),
                "quality": views.quality(twin),
                "maintenance": views.maintenance(twin),
                "manager": views.manager(twin, bottleneck_scorecard(plant, twin), sensors.coverage()),
            }
        t += step
    for a in twin.quality.drift_log:
        events.append({"t": round(a.t), "kind": "drift", "station": a.station, "text": str(a)})
    for h, tps, tpu in hold_precision(plant, twin):
        events.append({"t": round(h.t), "kind": "hold", "station": h.station, "text": str(h),
                       "sure": h.sure, "uncertain": h.uncertain, "tp_sure": tps, "tp_unc": tpu})
    events.sort(key=lambda e: e["t"])
    sc = bottleneck_scorecard(plant, twin)
    cont = containment_scorecard(plant, twin)
    ranking = voi.rank(cfg, plant, twin)
    meta = {
        "id": cfg.name, "plant": cfg.plant, "takt_s": cfg.takt_s, "hours": hours, "step": step,
        "stations": [{"id": s.id, "zone": s.zone, "type": s.type.name, "sensors": s.sensors.name,
                      "cycle_s": s.cycle_s, "buffer": s.buffer_before, "inspection": s.type.inspection,
                      "params": [p.name for p in s.params]} for s in cfg.stations],
        "perturbations": [{"station": p.station, "at_s": p.at_s, "ramp_s": p.ramp_s, "cycle_s": p.cycle_s}
                          for p in cfg.perturbations],
        "sensor_faults": [{"station": f.station, "at_s": f.at_s, "duration_s": f.duration_s}
                          for f in cfg.sensor_faults],
        "param_drifts": [{"station": d.station, "param": d.param, "at_s": d.at_s, "ramp_s": d.ramp_s, "to": d.to}
                         for d in cfg.param_drifts],
        "defects": [{"name": d.name, "detected_at": d.detected_at,
                     "causes": [f"{c.station}.{c.param}" for c in d.causes]} for d in cfg.defects],
        "scorecard": [{"station": s.station, "ramp_min": s.t_ramp_start / 60,
                       "over_min": None if s.t_over_takt is None else s.t_over_takt / 60,
                       "block_min": None if s.t_upstream_blocked is None else s.t_upstream_blocked / 60,
                       "alert_min": None if s.t_alert is None else s.t_alert / 60,
                       "lead_min": None if s.lead_s is None else s.lead_s / 60,
                       "eta_err_min": None if s.eta_error_s is None else s.eta_error_s / 60,
                       "conf": s.alert_conf, "inferred": s.alert_inferred_share} for s in sc["scores"]],
        "false_alarms": len(sc["false_alarms"]), "alerts_raised": sc["alerts_raised"],
        "containment": [{"defect": c.defect, "cause": c.cause_station, "n_defective": c.n_defective,
                         "first_fail_min": None if c.t_first_fail is None else c.t_first_fail / 60,
                         "hold_min": None if c.t_first_hold is None else c.t_first_hold / 60,
                         "hold_size": c.hold_size, "precision": c.precision, "recall": c.recall,
                         "blanket": c.blanket_size, "escaped": c.escaped} for c in cont],
        "next_sensor": [{"station": r["station"], "from": r["from"], "d_samples": round(r["d_samples_per_h"], 1),
                         "d_lead_min": None if r["d_lead_s"] is None else round(r["d_lead_s"] / 60, 1)}
                        for r in ranking[:3]],
        "coverage": sensors.coverage(),
    }
    return {"meta": meta, "frames": frames, "events": events, "personas": personas}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--step", type=float, default=10.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    data = export(args.config, args.hours, args.step)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(data, separators=(",", ":")))
    print(f"wrote {args.out}: {len(data['frames'])} frames, {Path(args.out).stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
