"""The evidence pack: everything the AI layer is allowed to talk about.

One JSON-serialisable dict built from the twin (and, when an evaluator is
available, its ledger). The LLM never sees the plant, never computes a
number, and every figure it writes must be traceable to a field here.
"""
from __future__ import annotations

from typing import Any

from .twin import Twin


def _hm(t: float) -> str:
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}"


def pack(twin: Twin, coverage: dict[str, str] | None = None,
         bottleneck_scorecard: dict | None = None,
         containment_scorecard: list | None = None,
         voi_rank: list[dict] | None = None) -> dict[str, Any]:
    twin.refresh()
    cfg = twin.cfg
    hours = max(twin.t / 3600, 1e-9)
    bufs = twin.buffers
    stations = []
    for i, s in enumerate(cfg.stations):
        b = twin.stations[s.id]
        a = twin.active.get(s.id)
        stations.append({
            "id": s.id, "zone": s.zone, "type": s.type.name, "sensors": s.sensors.name,
            "state": b.state.value, "state_source": b.state.source,
            "cycle_s": b.cycle_s.value, "cycle_source": b.cycle_s.source if b.cycle_s.value is not None else None,
            "nominal_cycle_s": s.cycle_s, "takt_s": cfg.takt_s,
            "buffer": bufs[s.id].value, "buffer_cap": s.buffer_before,
            "buffer_source": bufs[s.id].source,
            "sensor_health": b.health,
            "active_alert": None if a is None else {
                "eta_min": round(a.eta_s / 60, 1), "confidence": round(a.confidence, 2),
                "basis": a.basis, "inferred_share": round(a.inferred_share, 2)},
        })
    alerts = []
    for x in twin.log:
        alerts.append({"t": _hm(x.t), "action": x.action, "station": x.alert.station,
                       "eta_min": round(x.alert.eta_s / 60, 1), "confidence": round(x.alert.confidence, 2),
                       "cycle_s": round(x.alert.cycle_now, 1), "basis": x.alert.basis,
                       "inferred_share": round(x.alert.inferred_share, 2), "cause": x.cause})
    q = twin.quality
    quality = {
        "first_pass_yield": {
            s.id: {"ok": ok, "n": n, "pct": (round(100 * ok / n, 1) if n else None)}
            for s in cfg.stations if s.type.inspection
            for ok, n in [q.first_pass_yield(s.id)]},
        "drift_alerts": [{"t": _hm(a.t), "station": a.station, "param": a.param, "direction": a.direction,
                          "mean_now": round(a.mean_now, 3), "onset": _hm(a.onset_t),
                          "min_to_limit": None if a.t_to_limit_s is None else round(a.t_to_limit_s / 60)}
                         for a in q.drift_log],
        "hypotheses": [{"conditions": [str(c) for c in h.conditions], "lift": round(h.lift, 1),
                        "defective_under": [h.a, h.a + h.c], "defective_otherwise": [h.b, h.b + h.d],
                        "p_value": float(f"{h.p_value:.2g}")} for h in q.hypotheses[:5]],
        "holds": [{"id": h.id, "t": _hm(h.t), "reason": h.reason, "station": h.station, "param": h.param,
                   "sure": h.sure, "uncertain": h.uncertain, "already_exited": h.exited,
                   "hypothesis": None if h.hypothesis is None else str(h.hypothesis)} for h in q.holds],
        "unreported_params": [f"{s.id}.{p.name}" for s in cfg.stations for p in s.params
                              if s.id not in q.reports],
    }
    out: dict[str, Any] = {
        "line": {"id": cfg.name, "plant": cfg.plant.get("name", cfg.name), "takt_s": cfg.takt_s,
                 "now": _hm(twin.t), "hours_run": round(hours, 2), "stations": len(cfg.stations)},
        "output": {"vehicles_out": twin.exited, "veh_per_h": round(twin.exited / hours, 1),
                   "takt_ceiling_per_h": round(3600 / cfg.takt_s, 1),
                   "vehicles_unplaced": twin.in_transit()},
        "stations": stations,
        "alerts": alerts,
        "quality": quality,
        "provenance_legend": {"measured": "read from a sensor", "inferred": "reconstructed from neighbours",
                              "simulated": "forecast from believed state"},
    }
    if coverage:
        out["coverage"] = coverage
    if bottleneck_scorecard is not None:
        out["ledger"] = {
            "bottleneck": [{"station": s.station, "lead_min": None if s.lead_s is None else round(s.lead_s / 60, 1),
                            "eta_error_min": None if s.eta_error_s is None else round(s.eta_error_s / 60, 1),
                            "confidence": s.alert_conf, "warned_at": None if s.t_alert is None else _hm(s.t_alert),
                            "blocked_at": None if s.t_upstream_blocked is None else _hm(s.t_upstream_blocked)}
                           for s in bottleneck_scorecard["scores"]],
            "false_alarms": len(bottleneck_scorecard["false_alarms"]),
            "alerts_raised": bottleneck_scorecard["alerts_raised"],
        }
    if containment_scorecard:
        out.setdefault("ledger", {})["containment"] = [
            {"defect": c.defect, "cause_station": c.cause_station, "truly_defective": c.n_defective,
             "hold_at": None if c.t_first_hold is None else _hm(c.t_first_hold),
             "first_inspection_catch_at": None if c.t_first_fail is None else _hm(c.t_first_fail),
             "hold_size": c.hold_size, "precision": c.precision, "recall": c.recall,
             "blanket_hold_size": c.blanket_size, "escaped": c.escaped}
            for c in containment_scorecard]
    if voi_rank:
        out["next_sensor"] = [{"station": r["station"], "from": r["from"], "to": r["to"],
                               "extra_samples_per_h": round(r["d_samples_per_h"], 1),
                               "extra_lead_min": None if r["d_lead_s"] is None else round(r["d_lead_s"] / 60, 1),
                               "cost_usd": r["cost"]} for r in voi_rank[:5]]
    return out
