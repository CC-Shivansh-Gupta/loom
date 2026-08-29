"""Factory I/O adapter: read-only, over Modbus/TCP.

Factory I/O's "Modbus TCP/IP Server" driver exposes every sensor as a
discrete input and every actuator as a coil. Loom polls the inputs,
turns sensor edges into its own event schema, and feeds the twin. It
never writes. A separate `plc_stub` drives the scene, playing the plant's
PLC; Loom does not talk to it.

Map file (YAML):

    host: 192.168.0.20        # the Windows machine running Factory I/O
    port: 502
    unit: 1
    poll_hz: 50
    time_scale: 1.0           # sim seconds per real second (1 for a real scene)
    line: factoryio_line.yaml # Loom line config: stations, nominal cycles, takt
    stations:                 # discrete-input addresses; omit what the scene lacks
      S1: {entry: 0, exit: 1}
      S2: {exit: 3}           # finish-only
      S3: {}                  # dark -- no sensors, the twin infers it
    actuators:                # coils, used only by plc_stub
      emit: 0
      S1: {conveyor: 1}
      S2: {conveyor: 2}
      S3: {conveyor: 3}

Vehicle identity: parts are anonymous, so ids are assigned in FIFO order
at the first sensor -- the same assumption the twin already makes.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import voi
from .config import LineCfg, load_line
from .events import EXIT, FINISH, MOVE, RELEASE, START, Event
from .export import HEALTH, SRC, STATE
from .modbus import ModbusClient
from .twin import Twin

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"


@dataclass
class StationMap:
    id: str
    entry: int | None = None
    exit: int | None = None

    @property
    def profile(self) -> str:
        if self.entry is not None and self.exit is not None:
            return "photo_eyes"
        if self.exit is not None:
            return "exit_eye"
        return "dark"


@dataclass
class Map:
    host: str
    port: int
    unit: int
    poll_hz: float
    time_scale: float
    line_path: Path
    stations: list[StationMap]
    actuators: dict = field(default_factory=dict)

    @property
    def input_span(self) -> tuple[int, int]:
        addrs = [a for s in self.stations for a in (s.entry, s.exit) if a is not None]
        return (min(addrs), max(addrs) - min(addrs) + 1) if addrs else (0, 1)


def load_map(path: str | Path) -> tuple[Map, LineCfg]:
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    line_path = (path.parent / raw["line"]) if not Path(raw["line"]).is_absolute() else Path(raw["line"])
    cfg = load_line(line_path)
    stations = []
    for s in cfg.stations:
        m = raw.get("stations", {}).get(s.id, {}) or {}
        stations.append(StationMap(s.id, m.get("entry"), m.get("exit")))
    mp = Map(str(raw.get("host", "127.0.0.1")), int(raw.get("port", 502)), int(raw.get("unit", 1)),
             float(raw.get("poll_hz", 50)), float(raw.get("time_scale", 1.0)), line_path, stations,
             raw.get("actuators", {}) or {})
    return mp, cfg


class Feed:
    """Polls the inputs, detects rising edges, emits Loom events into the twin."""

    def __init__(self, mp: Map, cfg: LineCfg, client: ModbusClient | None = None) -> None:
        self.map, self.cfg = mp, cfg
        self.client = client or ModbusClient(mp.host, mp.port, mp.unit)
        self.twin = Twin(cfg)
        for sm in mp.stations:                       # the twin knows what each station reports
            self.twin.set_profile(sm.id, voi._profile(sm.profile))
        self.n = len(cfg.stations)
        self.pending: list[list[int]] = [[] for _ in range(self.n)]   # vids waiting before station i
        self.current: list[int | None] = [None] * self.n
        self.started_at: list[float] = [0.0] * self.n
        self.prev_bits: list[bool] | None = None
        self.bits: list[bool] = []
        self._next_vid = 1
        self._seq = 0
        self.t0 = time.perf_counter()
        self.t = 0.0
        self.events = 0
        self.polls = 0
        self.errors = 0
        self.lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- clock ----------------------------------------------------------------
    def now(self) -> float:
        return (time.perf_counter() - self.t0) * self.map.time_scale

    # -- polling ----------------------------------------------------------------
    def poll_once(self) -> None:
        base, count = self.map.input_span
        try:
            bits = self.client.read_discrete_inputs(base, count)
        except Exception:
            self.errors += 1
            self.client.close()
            return
        with self.lock:
            self.polls += 1
            self.t = self.now()
            prev = self.prev_bits
            self.bits = bits
            self.prev_bits = bits
            if prev is None:
                return
            for i, sm in enumerate(self.map.stations):
                if sm.entry is not None and self._rose(prev, bits, sm.entry - base):
                    self._on_entry(i)
                if sm.exit is not None and self._rose(prev, bits, sm.exit - base):
                    self._on_exit(i)

    @staticmethod
    def _rose(prev: list[bool], cur: list[bool], k: int) -> bool:
        return cur[k] and not prev[k]

    def _emit(self, kind: str, station: str | None, vid: int, **payload) -> None:
        self._seq += 1
        self.events += 1
        self.twin.ingest(Event(self.t, self._seq, kind, station, vid, payload))

    def _take(self, i: int) -> int:
        """Next vehicle for station i: the head of its queue, looking back
        across dark stations whose exits are invisible; else a new id."""
        j = i
        while True:
            if self.pending[j]:
                return self.pending[j].pop(0)
            if j == 0 or self.map.stations[j - 1].exit is not None:
                break
            j -= 1
        vid = self._next_vid
        self._next_vid += 1
        self._emit(RELEASE, None, vid, variant="-")
        return vid

    def _on_entry(self, i: int) -> None:
        if self.current[i] is not None:
            return                                   # double edge: still occupied
        vid = self._take(i)
        self.current[i] = vid
        self.started_at[i] = self.t
        self._emit(START, self.cfg.ids[i], vid)

    def _on_exit(self, i: int) -> None:
        vid = self.current[i]
        if vid is None:
            vid = self._take(i)                      # exit-only station: start was never seen
        self._emit(FINISH, self.cfg.ids[i], vid)
        if i == self.n - 1:
            self._emit(EXIT, self.cfg.ids[i], vid)
        else:
            self._emit(MOVE, self.cfg.ids[i], vid, to=self.cfg.ids[i + 1])
            self.pending[i + 1].append(vid)
        self.current[i] = None

    def run(self) -> None:
        period = 1.0 / self.map.poll_hz
        while not self._stop.is_set():
            t = time.perf_counter()
            self.poll_once()
            time.sleep(max(0.0, period - (time.perf_counter() - t)))

    def start(self) -> "Feed":
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.client.close()

    # -- frame for the control room (no ground truth: the "plant" lane shows raw sensors)
    def frame(self) -> dict:
        with self.lock:
            twin = self.twin
            twin.t = max(twin.t, self.t)
            twin.refresh()
            bufs = twin.buffers
            base, _ = self.map.input_span
            st_rows, veh = [], []
            for i, s in enumerate(self.cfg.stations):
                sm = self.map.stations[i]
                b = twin.stations[s.id]
                a = twin.active.get(s.id)
                occupied = self.current[i] is not None
                st_rows.append([
                    STATE["busy" if occupied else "idle"], self.current[i],
                    STATE[b.state.value], SRC[b.state.source], b.vehicle.value,
                    len(self.pending[i]), bufs[s.id].value, SRC[bufs[s.id].source],
                    None if b.cycle_s.value is None else round(b.cycle_s.value, 1), s.cycle_s,
                    None if a is None else round(a.eta_s / 60, 1),
                    None if a is None else round(a.confidence, 2),
                    HEALTH[b.health],
                ])
                if occupied:
                    prog = min(1.0, (self.t - self.started_at[i]) / max(s.cycle_s, 1e-9))
                    veh.append([self.current[i], 1, i, round(prog, 2), "-"])
                for k, vid in enumerate(self.pending[i][:6]):
                    veh.append([vid, 0, i, k, "-"])
            bel, placed = [], set()
            for i in range(twin.n):
                s_, vt = twin.station_state(i)
                if vt.value is not None:
                    bel.append([vt.value, 1, i, SRC[vt.source]]); placed.add(vt.value)
                for k, vid in enumerate(sorted(twin._pending[i])):
                    bel.append([vid, 0, i, SRC[twin.buffer_count(i).source]]); placed.add(vid)
            for v in veh:
                if v[0] not in placed:
                    bel.append([v[0], -1, -1, 1])
            bn = twin.bottleneck_now()
            return {"t": round(self.t), "st": st_rows, "veh": veh, "bel": bel,
                    "out": [twin.exited, twin.exited],
                    "bn": None if bn is None else [bn[0], round(bn[1] / 60, 1), SRC[bn[2]]],
                    "holds": [], "sensors": {"base": base, "bits": self.bits, "polls": self.polls,
                                             "errors": self.errors, "events": self.events}}


class ExternalSim:
    """LiveSim-compatible facade over a Feed, so `loom.server --factoryio`
    serves the same control room. No ground truth, no injections: the
    plant is someone else's."""

    def __init__(self, map_path: str, time_scale: float | None = None, port: int | None = None) -> None:
        self.map_path = map_path
        self.time_scale = time_scale
        self.port = port
        self.playing = True
        self.speed = 1.0
        self.recording = None
        self.injections: list[dict] = []
        self._start()

    def _start(self) -> None:
        mp, cfg = load_map(self.map_path)
        if self.time_scale:
            mp.time_scale = self.time_scale
        if self.port:
            mp.port = self.port
        self.mp, self.cfg = mp, cfg
        self.feed = Feed(mp, cfg).start()
        self.speed = mp.time_scale
        self._events_sent = 0

    @property
    def twin(self) -> Twin:
        return self.feed.twin

    @property
    def plant(self):
        return self.feed                       # has .t, which is all the server reads

    def step(self, real_dt: float) -> None:
        pass                                   # the feed thread owns time

    def reset(self) -> None:
        self.feed.stop()
        self._start()

    def load_named(self, name: str) -> None:
        raise ValueError("external source: the line is defined by the Factory I/O scene and map")

    def load_yaml(self, text: str, source: str = "editor") -> None:
        raise ValueError("external source: edit configs/factoryio_map.yaml and restart")

    def inject(self, kind: str, station: str, **kw) -> dict:
        raise ValueError("external source: faults happen in Factory I/O (or plc_stub --wear), not here")

    def start_recording(self, name=None) -> dict:
        raise ValueError("recording is not available for an external source yet")

    def stop_recording(self) -> dict:
        raise ValueError("not recording")

    def frame(self) -> dict:
        f = self.feed.frame()
        f["playing"] = True
        f["speed"] = self.speed
        f["recording"] = None
        f["new_events"] = self._new_events()
        return f

    def _new_events(self) -> list[dict]:
        out = []
        log = self.twin.log
        while self._events_sent < len(log):
            x = log[self._events_sent]
            out.append({"t": round(x.t), "kind": x.action, "station": x.alert.station, "text": str(x)})
            self._events_sent += 1
        return out

    def meta(self) -> dict:
        cfg = self.cfg
        return {
            "id": cfg.name, "plant": cfg.plant, "takt_s": cfg.takt_s, "source": f"factory-io:{self.mp.host}",
            "stations": [{"id": s.id, "zone": s.zone, "type": s.type.name,
                          "sensors": self.mp.stations[i].profile, "cycle_s": s.cycle_s,
                          "buffer": s.buffer_before, "inspection": s.type.inspection, "params": []}
                         for i, s in enumerate(cfg.stations)],
            "profiles": [], "scenarios": [], "yaml": Path(self.map_path).read_text(),
            "coverage": {s.id: self.mp.stations[i].profile for i, s in enumerate(cfg.stations)},
            "external": True,
        }

    def station_detail(self, sid: str) -> dict:
        i = self.cfg.index(sid)
        s = self.cfg.stations[i]
        sm = self.mp.stations[i]
        twin = self.twin
        twin.refresh()
        b = twin.stations[sid]
        a = twin.active.get(sid)
        fit = twin.forecaster.fit(sid, twin.t)
        base = self.mp.input_span[0]
        bits = self.feed.bits
        return {
            "id": sid, "zone": s.zone, "type": s.type.name, "sensors": sm.profile,
            "nominal_cycle_s": s.cycle_s, "true_cycle_now": None, "buffer_cap": s.buffer_before,
            "truth": {"state": "busy" if self.feed.current[i] is not None else "idle",
                      "vehicle": self.feed.current[i], "buffer": len(self.feed.pending[i]),
                      "busy_pct": None, "blocked_pct": None,
                      "entry_eye": None if sm.entry is None else bool(bits[sm.entry - base]) if bits else None,
                      "exit_eye": None if sm.exit is None else bool(bits[sm.exit - base]) if bits else None},
            "belief": {"state": b.state.value, "state_src": b.state.source, "vehicle": b.vehicle.value,
                       "buffer": twin.buffer_count(i).value, "buffer_src": twin.buffer_count(i).source,
                       "cycle": b.cycle_s.value, "health": b.health,
                       "slope_s_per_min": None if fit is None else round(fit.slope * 60, 2),
                       "tstat": None if fit is None else round(fit.tstat, 1),
                       "measured_samples": b.measured_samples, "inferred_samples": b.inferred_samples},
            "alert": None if a is None else {"eta_min": round(a.eta_s / 60, 1), "confidence": round(a.confidence, 2),
                                             "basis": a.basis, "inferred_share": round(a.inferred_share, 2)},
            "truth_cycles": [], "belief_cycles": [[vid, round(c, 1), SRC[src]] for vid, c, src in twin.samples[sid][-40:]],
            "params": [], "holds": [],
        }

    def view(self, role: str) -> str:
        from . import views
        twin = self.twin
        if role == "supervisor":
            return views.supervisor(twin)
        if role == "quality":
            return views.quality(twin)
        if role == "maintenance":
            return views.maintenance(twin)
        if role == "manager":
            return views.manager(twin, None, self.meta()["coverage"])
        raise ValueError(role)

    def scorecard(self) -> dict:
        raised = [x for x in self.twin.log if x.action == "raised"]
        return {"bottleneck": [{"station": x.alert.station, "lead_min": None, "eta_err_min": None,
                                "alert_min": round(x.t / 60, 1), "block_min": None, "conf": x.alert.confidence}
                               for x in raised],
                "false_alarms": 0, "alerts_raised": len(raised), "containment": [], "holds": [],
                "injections": [], "external": True,
                "sensors": {"polls": self.feed.polls, "errors": self.feed.errors, "events": self.feed.events}}
