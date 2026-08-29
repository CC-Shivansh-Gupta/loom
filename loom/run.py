"""Wire the layers together and run a shift.

    python -m loom.run configs/line_basic.yaml --hours 2 [--trace] [--view operator:B3|supervisor|manager]
"""
from __future__ import annotations

import argparse

from . import views
from .config import load_line
from .evaluator import bottleneck_scorecard, state_agreement
from .plant import BLOCKED_STATE, BUSY, IDLE, Plant
from .sensors import SensorLayer
from .twin import Twin


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


def report(cfg, plant: Plant, sensors: SensorLayer, twin: Twin) -> str:
    hours = plant.t / 3600
    lines = [f"line {cfg.name}: takt {cfg.takt_s:.0f}s, {len(cfg.stations)} stations, "
             f"cv {cfg.cv:.0%}, {len(cfg.variants) or 1} variant(s), ran {hours:.2f} h"]
    done = plant.exited
    lines.append(f"  throughput {len(done) / hours:6.1f} veh/h   (takt ceiling {3600 / cfg.takt_s:.1f})")
    lines.append(f"  WIP now    {plant.wip():6d} veh")
    if done:
        lt = sum(v.exited_t - v.released_t for v in done) / len(done)
        lines.append(f"  lead time  {lt / 60:6.1f} min (mean of exited)")
    lost = sum(1 for e in plant.events if e.kind == "lost_slot")
    lines.append(f"  lost slots {lost:6d}")
    lines.append("")
    lines.append(f"  {'station':<5}{'zone':<7}{'type':<12}{'sensors':<11}{'cycle':>6}{'now':>6}  {'busy':>6}{'block':>7}{'idle':>6}   buf  twin")
    for st, buf in zip(plant.stations, plant.buffers):
        tot = sum(st.time_in.values()) or 1.0
        b = twin.stations[st.cfg.id]
        lines.append(
            f"  {st.cfg.id:<5}{st.cfg.zone:<7}{st.cfg.type.name:<12}{st.cfg.sensors.name:<11}"
            f"{st.cfg.cycle_s:>5.0f}s{st.nominal_cycle(plant.t):>5.0f}s "
            f"{st.time_in[BUSY] / tot:6.0%}{st.time_in[BLOCKED_STATE] / tot:7.0%}"
            f"{st.time_in[IDLE] / tot:6.0%}   {len(buf):>2}   {b.state!r} {b.vehicle!r} cyc{b.cycle_s!r}"
        )
    lines.append("")
    agree = state_agreement(plant, twin)
    lines.append(f"  sensors passed {sensors.passed} events, dropped {sensors.dropped}; "
                 f"twin saw {twin.seen}")
    lines.append(f"  twin vs truth: {len(agree['mismatches'])} mismatches in {agree['checked']} checks")
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
                     f"{'lead':>7}{'eta err':>9}")
        for s in sc["scores"]:
            lines.append(f"    {s.station:<8}{_mins(s.t_ramp_start):>7}{_mins(s.t_over_takt):>7}"
                         f"{_mins(s.t_upstream_blocked):>7}{_mins(s.t_alert):>7}"
                         f"{_mins(s.lead_s):>7}{_mins(s.eta_error_s):>9}")
        lines.append(f"    false alarms: {len(sc['false_alarms'])} of {sc['alerts_raised']} raised")
    return "\n".join(lines)


def render_view(spec: str, cfg, plant, sensors, twin) -> str:
    role, _, arg = spec.partition(":")
    if role == "operator":
        return views.operator(twin, arg or cfg.ids[0])
    if role == "supervisor":
        return views.supervisor(twin)
    if role == "manager":
        return views.manager(twin, bottleneck_scorecard(plant, twin), sensors.coverage())
    raise SystemExit(f"unknown view {spec!r}; use operator:<station>, supervisor, manager")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--trace", action="store_true", help="print every event")
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
        print(report(cfg, plant, sensors, twin))


if __name__ == "__main__":
    main()
