"""Stand-in PLC for a Factory I/O scene: drives the conveyors so parts flow
station to station with a dwell at each one. Loom never runs this -- it
is the plant's controller, kept separate on purpose. Wear a station with
--wear so the twin has something to forecast:

    python -m loom.plc_stub configs/factoryio_map.yaml --wear S2:600:90

drives the scene at the line's takt and, 600 s in, slows S2 to 90 s.

Scene contract (build it in Factory I/O from stock parts):
  - one Emitter at the line entry, coil `emit`
  - per station: a conveyor segment (coil `conveyor`), a diffuse sensor at
    its entry (`entry`) and one at its exit (`exit`); leave sensors out of
    the map to make a station dark or exit-only for the twin
  - a Remover at the end
"""
from __future__ import annotations

import argparse
import time

from .factoryio import load_map
from .modbus import ModbusClient


def run(map_path: str, wear: list[tuple[str, float, float]], client: ModbusClient | None = None,
        duration_s: float | None = None, time_scale: float = 1.0) -> None:
    mp, cfg = load_map(map_path)
    c = client or ModbusClient(mp.host, mp.port, mp.unit)
    act = mp.actuators
    n = len(cfg.stations)
    base, count = mp.input_span
    t0 = time.perf_counter()
    now = lambda: (time.perf_counter() - t0) * time_scale
    # station state machine: free -> (entry seen) dwelling -> done -> (exit seen) free
    state = ["free"] * n
    dwell_until = [0.0] * n
    prev = None
    last_emit = -1e9
    emit_until = 0.0

    def cycle(i: int, t: float) -> float:
        c0 = cfg.stations[i].cycle_s
        for sid, at, to in wear:
            if sid == cfg.stations[i].id and t >= at:
                c0 = to
        return c0

    while duration_s is None or now() < duration_s:
        t = now()
        bits = c.read_discrete_inputs(base, count)
        if prev is None:
            prev = bits
        # emitter: one part per takt while the first station's entry is clear
        if "emit" in act:
            if t - last_emit >= cfg.takt_s and state[0] == "free":
                c.write_coil(act["emit"], True); last_emit = t; emit_until = t + 0.3
            elif t >= emit_until:
                c.write_coil(act["emit"], False)
        for i, s in enumerate(cfg.stations):
            sm = mp.stations[i]
            a = act.get(s.id, {})
            conv = a.get("conveyor")
            entry = None if sm.entry is None else bits[sm.entry - base]
            exit_ = None if sm.exit is None else bits[sm.exit - base]
            if state[i] == "free" and entry:
                state[i] = "dwell"; dwell_until[i] = t + cycle(i, t)
            if state[i] == "dwell" and t >= dwell_until[i]:
                state[i] = "done"
            if state[i] == "done" and exit_ and not prev[sm.exit - base]:
                pass
            if state[i] == "done" and not entry and not exit_:
                state[i] = "free"                    # part has left the segment
            # conveyor runs unless dwelling, or blocked by a busy downstream station
            downstream_busy = i + 1 < n and state[i + 1] != "free"
            run_conv = state[i] != "dwell" and not (state[i] == "done" and exit_ and downstream_busy)
            if conv is not None:
                c.write_coil(conv, run_conv)
        prev = bits
        time.sleep(0.02)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("map")
    ap.add_argument("--wear", action="append", default=[], help="STATION:AT_S:CYCLE_S")
    args = ap.parse_args()
    wear = []
    for w in args.wear:
        sid, at, to = w.split(":")
        wear.append((sid, float(at), float(to)))
    run(args.map, wear)


if __name__ == "__main__":
    main()
