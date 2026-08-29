"""Wire the layers together and run a shift.

    python -m loom.run configs/line_basic.yaml --hours 2 [--trace]
"""
from __future__ import annotations

import argparse

from .config import load_line
from .evaluator import state_agreement
from .plant import BLOCKED_STATE, BUSY, IDLE, Plant
from .sensors import SensorLayer
from .twin import Twin


def build(cfg_path: str, sensor_profiles: dict[str, str] | None = None):
    cfg = load_line(cfg_path)
    plant = Plant(cfg)
    sensors = SensorLayer(sensor_profiles)
    twin = Twin(cfg)
    plant.listeners.append(sensors.observe)
    sensors.subscribers.append(twin.ingest)
    return cfg, plant, sensors, twin


def report(cfg, plant: Plant, sensors: SensorLayer, twin: Twin) -> str:
    hours = plant.t / 3600
    lines = [f"line {cfg.name}: takt {cfg.takt_s:.0f}s, {len(cfg.stations)} stations, ran {hours:.2f} h"]
    done = plant.exited
    lines.append(f"  throughput {len(done) / hours:6.1f} veh/h   (takt ceiling {3600 / cfg.takt_s:.1f})")
    lines.append(f"  WIP now    {plant.wip():6d} veh")
    if done:
        lt = sum(v.exited_t - v.released_t for v in done) / len(done)
        lines.append(f"  lead time  {lt / 60:6.1f} min (mean of exited)")
    lost = sum(1 for e in plant.events if e.kind == "lost_slot")
    lines.append(f"  lost slots {lost:6d}")
    lines.append("")
    lines.append(f"  {'station':<8}{'zone':<8}{'cycle':>6}  {'busy':>6}{'block':>7}{'idle':>6}   buf  twin")
    for st, buf in zip(plant.stations, plant.buffers):
        tot = sum(st.time_in.values()) or 1.0
        b = twin.stations[st.cfg.id]
        lines.append(
            f"  {st.cfg.id:<8}{st.cfg.zone:<8}{st.cfg.cycle_s:>5.0f}s "
            f"{st.time_in[BUSY] / tot:6.0%}{st.time_in[BLOCKED_STATE] / tot:7.0%}"
            f"{st.time_in[IDLE] / tot:6.0%}   {len(buf):>2}   {b.state!r} {b.vehicle!r}"
        )
    lines.append("")
    agree = state_agreement(plant, twin)
    lines.append(f"  sensors passed {sensors.passed} events, dropped {sensors.dropped}; "
                 f"twin saw {twin.seen}")
    lines.append(f"  twin vs truth: {len(agree['mismatches'])} mismatches in {agree['checked']} checks")
    for m in agree["mismatches"]:
        lines.append(f"    {m}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--trace", action="store_true", help="print every event")
    args = ap.parse_args()

    cfg, plant, sensors, twin = build(args.config)
    plant.run(args.hours * 3600)
    if args.trace:
        for e in plant.events:
            print(e)
        print()
    print(report(cfg, plant, sensors, twin))


if __name__ == "__main__":
    main()
