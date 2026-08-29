"""Wire the layers together and run a shift.

    python -m loom.run configs/ramp_b3.yaml --hours 2 [--trace] [--view operator:B3|supervisor|manager] [--voi]
"""
from __future__ import annotations

import argparse

from . import views, voi
from .config import load_line
from .evaluator import (bottleneck_scorecard, containment_scorecard, hold_precision,
                        inference_accuracy, state_agreement)
from .plant import BLOCKED_STATE, BUSY, IDLE, Plant
from .sensors import SensorLayer
from .twin import INFERRED, MEASURED, Twin


def build(cfg_path: str):
    cfg = load_line(cfg_path)
    plant = Plant(cfg)
    sensors = SensorLayer(cfg, cfg.seed)
    twin = Twin(cfg)
    plant.listeners.append(sensors.observe)
    sensors.subscribers.append(twin.ingest)
    return cfg, plant, sensors, twin


def _mins(x: float | None) -> str:
    return "   -  " if x is None else f"{x / 60:6.1f}"


def _mae(x: float | None) -> str:
    return "  -  " if x is None else f"{x:5.1f}"


def report(cfg, plant: Plant, sensors: SensorLayer, twin: Twin, with_voi: bool = False) -> str:
    hours = plant.t / 3600
    twin.refresh()
    lines = [f"line {cfg.name}: takt {cfg.takt_s:.0f}s, {len(cfg.stations)} stations, "
             f"cv {cfg.cv:.0%}, {len(cfg.variants) or 1} variant(s), ran {hours:.2f} h"]
    done = plant.exited
    lines.append(f"  throughput {len(done) / hours:6.1f} veh/h   (takt ceiling {3600 / cfg.takt_s:.1f})")
    lines.append(f"  WIP now    {plant.wip():6d} veh   (twin cannot place {twin.in_transit()})")
    if done:
        lt = sum(v.exited_t - v.released_t for v in done) / len(done)
        lines.append(f"  lead time  {lt / 60:6.1f} min (mean of exited)")
    lost = sum(1 for e in plant.events if e.kind == "lost_slot")
    lines.append(f"  lost slots {lost:6d}")
    lines.append("")
    acc = inference_accuracy(plant, twin)
    lines.append(f"  {'stn':<5}{'zone':<7}{'type':<12}{'sensors':<11}{'cycle':>6}{'now':>6}  "
                 f"{'busy':>5}{'blk':>5}{'idle':>5}  buf  twin state      cyc      samples ●n/mae ◐n/mae  health")
    for i, (st, buf) in enumerate(zip(plant.stations, plant.buffers)):
        tot = sum(st.time_in.values()) or 1.0
        b = twin.stations[st.cfg.id]
        a = acc[st.cfg.id]
        lines.append(
            f"  {st.cfg.id:<5}{st.cfg.zone:<7}{st.cfg.type.name:<12}{st.cfg.sensors.name:<11}"
            f"{st.cfg.cycle_s:>5.0f}s{st.nominal_cycle(plant.t):>5.0f}s "
            f"{st.time_in[BUSY] / tot:5.0%}{st.time_in[BLOCKED_STATE] / tot:5.0%}"
            f"{st.time_in[IDLE] / tot:5.0%}  {len(buf):>2}   {b.state!r:<9}{b.vehicle!r:<6} "
            f"{b.cycle_s!r:<8} {a[MEASURED]['n']:>4}/{_mae(a[MEASURED]['mae'])} "
            f"{a[INFERRED]['n']:>4}/{_mae(a[INFERRED]['mae'])}  {b.health}"
        )
    lines.append("")
    agree = state_agreement(plant, twin)
    lines.append(f"  sensors passed {sensors.passed}, filtered {sensors.dropped}, silenced {sensors.silenced}; "
                 f"twin saw {twin.seen}")
    lines.append(f"  twin vs truth now: {len(agree['measured_wrong'])} measured beliefs wrong, "
                 f"{len(agree['inferred_wrong'])} inferred beliefs wrong, of {agree['checked']}")
    for m in agree["mismatches"]:
        lines.append(f"    {m}")

    if twin.log:
        lines.append("")
        lines.append("  alert log:")
        for x in twin.log:
            lines.append(f"    {x}")

    sc = bottleneck_scorecard(plant, twin)
    if cfg.perturbations or sc["alerts_raised"]:
        lines.append("")
        lines.append("  bottleneck scorecard (minutes):")
        lines.append(f"    {'station':<8}{'ramp@':>7}{'>takt@':>7}{'block@':>7}{'alert@':>7}"
                     f"{'lead':>7}{'eta err':>9}  conf  inferred")
        for s in sc["scores"]:
            conf = "  -  " if s.alert_conf is None else f"{s.alert_conf:5.2f}"
            inf = "  -  " if s.alert_inferred_share is None else f"{s.alert_inferred_share:5.0%}"
            lines.append(f"    {s.station:<8}{_mins(s.t_ramp_start):>7}{_mins(s.t_over_takt):>7}"
                         f"{_mins(s.t_upstream_blocked):>7}{_mins(s.t_alert):>7}"
                         f"{_mins(s.lead_s):>7}{_mins(s.eta_error_s):>9}  {conf} {inf}")
        lines.append(f"    false alarms: {len(sc['false_alarms'])} of {sc['alerts_raised']} raised")

    q = twin.quality
    if q.drift_log or q.holds or cfg.defects:
        lines.append("")
        lines.append("  quality:")
        for a in q.drift_log:
            lines.append(f"    {a}")
        for h in q.hypotheses[:3]:
            lines.append(f"    hypothesis: {h}")
        for h, tp_sure, tp_unc in hold_precision(plant, twin):
            lines.append(f"    {h}")
            lines.append(f"      truth: {tp_sure}/{len(h.sure)} of sure and "
                         f"{tp_unc}/{len(h.uncertain)} of uncertain are actually defective")
        for c in containment_scorecard(plant, twin):
            lines.append(f"    containment scorecard '{c.defect}' (cause {c.cause_station}): "
                         f"{c.n_defective} truly defective; {c.detected_at_inspection} caught at inspection")
            if c.t_first_fail is not None:
                lines.append(f"      first inspection catch at {c.t_first_fail / 60:.0f} min (the no-twin baseline)")
            if c.t_first_hold is not None:
                lag = "" if c.lag_s is None else f", {c.lag_s / 60:.0f} min after drift start"
                lines.append(f"      hold at {c.t_first_hold / 60:.0f} min{lag}: {c.hold_size} vehicles "
                             f"({c.hold_sure} sure + {c.hold_uncertain} uncertain), precision "
                             f"{'-' if c.precision is None else f'{c.precision:.0%}'}, recall "
                             f"{'-' if c.recall is None else f'{c.recall:.0%}'}; "
                             f"blanket hold would be {c.blanket_size}")
            else:
                lines.append("      no hold issued")
            lines.append(f"      escaped (defective, exited, never caught or held): {c.escaped}")

    if with_voi:
        ranking = voi.rank(cfg, plant, twin)
        if ranking:
            lines.append("")
            lines.append("  next sensor to buy (replayed history with one station upgraded to cycle_only):")
            lines.append(f"    {'station':<8}{'from':<11}{'+samples/h':>11}{'+lead min':>11}")
            for r in ranking:
                lines.append(f"    {r['station']:<8}{r['from']:<11}{r['d_samples_per_h']:>11.1f}"
                             f"{_mins(r['d_lead_s']):>11}")
    return "\n".join(lines)


def render_view(spec: str, cfg, plant, sensors, twin) -> str:
    twin.refresh()
    role, _, arg = spec.partition(":")
    if role == "operator":
        return views.operator(twin, arg or cfg.ids[0])
    if role == "supervisor":
        return views.supervisor(twin)
    if role == "quality":
        return views.quality(twin)
    if role == "maintenance":
        return views.maintenance(twin)
    if role == "manager":
        return views.manager(twin, bottleneck_scorecard(plant, twin), sensors.coverage(),
                             voi.rank(cfg, plant, twin))
    raise SystemExit(f"unknown view {spec!r}; use operator:<station>, supervisor, manager")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--trace", action="store_true", help="print every event")
    ap.add_argument("--voi", action="store_true", help="rank which station to instrument next")
    ap.add_argument("--view", action="append", default=[],
                    help="operator:<station> | supervisor | manager (repeatable)")
    args = ap.parse_args()

    cfg, plant, sensors, twin = build(args.config)
    plant.run(args.hours * 3600)
    if args.trace:
        for e in plant.events:
            print(e)
        print()
    if args.view:
        for spec in args.view:
            print(render_view(spec, cfg, plant, sensors, twin))
            print()
    else:
        print(report(cfg, plant, sensors, twin, with_voi=args.voi))


if __name__ == "__main__":
    main()
