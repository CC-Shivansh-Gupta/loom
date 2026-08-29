"""A fake Factory I/O: a Modbus/TCP server whose discrete inputs are driven
by Loom's own plant simulation, and whose coils are ignored. Lets the
adapter, the twin and the control room be exercised end to end on any
machine, and is what the tests use.

    python -m loom.fakefactory configs/factoryio_map.yaml --speed 30

Sensor semantics match a real scene: the entry photo-eye is high while a
part sits at the station (start -> finish); the exit photo-eye is high
from finish until the part actually leaves (so a blocked part keeps it
high), and never for less than one poll.
"""
from __future__ import annotations

import argparse
import threading
import time

from .events import EXIT, FINISH, MOVE, START
from .factoryio import load_map
from .modbus import ModbusServer
from .plant import Plant


class FakeFactory:
    MIN_HOLD_S = 0.06

    def __init__(self, map_path: str, speed: float = 30.0, host: str = "127.0.0.1", port: int | None = None) -> None:
        self.map, self.cfg = load_map(map_path)
        self.server = ModbusServer(host, port if port is not None else self.map.port)
        self.plant = Plant(self.cfg)
        self.plant.listeners.append(self._on_event)
        self.speed = speed
        self._queue: dict[int, list[bool]] = {}
        self._since: dict[int, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self.server.port

    # Every sensor level (high or low) is held for at least MIN_HOLD_S so a
    # 50 Hz poller sees each edge, as it would with real parts that have a
    # physical gap between them. Wanted values queue up and are applied in
    # order once the current level has dwelled long enough.
    def _want(self, addr: int, value: bool) -> None:
        q = self._queue.setdefault(addr, [])
        if not q and self.server.discrete[addr] == value:
            return
        if q and q[-1] == value:
            return
        q.append(value)

    def _on_event(self, ev) -> None:
        if not ev.station:
            return
        i = self.cfg.index(ev.station)
        sm = self.map.stations[i]
        with self.server.lock:
            if ev.kind == START and sm.entry is not None:
                self._want(sm.entry, True)
            elif ev.kind == FINISH:
                if sm.entry is not None:
                    self._want(sm.entry, False)
                if sm.exit is not None:
                    self._want(sm.exit, True)
            elif ev.kind in (MOVE, EXIT) and sm.exit is not None:
                self._want(sm.exit, False)

    def _settle(self) -> None:
        now = time.perf_counter()
        with self.server.lock:
            for addr, q in self._queue.items():
                if q and now - self._since.get(addr, 0.0) >= self.MIN_HOLD_S:
                    self.server.discrete[addr] = q.pop(0)
                    self._since[addr] = now

    def run(self, duration_s: float | None = None) -> None:
        self.server.start()
        t0 = time.perf_counter()
        while not self._stop.is_set() and (duration_s is None or time.perf_counter() - t0 < duration_s):
            sim_t = (time.perf_counter() - t0) * self.speed
            self.plant.run(sim_t)
            self._settle()
            time.sleep(0.01)

    def start(self, duration_s: float | None = None) -> "FakeFactory":
        self._thread = threading.Thread(target=self.run, args=(duration_s,), daemon=True)
        self._thread.start()
        time.sleep(0.2)
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.server.stop()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("map")
    ap.add_argument("--speed", type=float, default=30.0)
    ap.add_argument("--port", type=int, default=5020,
                    help="Modbus port (502 is privileged on Linux/macOS; point the server at this one with --modbus-port)")
    args = ap.parse_args()
    f = FakeFactory(args.map, args.speed, port=args.port)
    print(f"fake Factory I/O on {f.map.host}:{f.port} at {args.speed}x; ctrl-c to stop")
    try:
        f.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
