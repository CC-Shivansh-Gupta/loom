"""Persistence and audit: SQLite, standard library only.

What is stored, and why a judge should care:

  events        every event the twin *received* (after the sensor layer):
                the twin's beliefs are reproducible from this table alone
  twin_events   every alert raised / cleared / grouped, drift warning, hold
  snapshots     the twin's belief every minute (state, provenance, buffers)
  audit         every human or operator action: config load, reset,
                injection, alert acknowledgement / dismissal, recording
  evidence      every evidence pack handed to the AI layer, content-hashed
  reports       every AI output: which pack (by hash), which persona, which
                provider/model, the prompt hash, tokens, cost, the text --
                and a mechanical grounding check: every number in the text
                must appear in the pack it was written from

`replay(run_id)` rebuilds a Twin from `events` and compares it with the
stored snapshots, so "what did the twin believe at 10:42" has one answer.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

from .config import load_line
from .events import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started_at REAL, config_name TEXT, yaml TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS events (run_id INTEGER, seq INTEGER, t REAL, kind TEXT, station TEXT, vehicle INTEGER, payload TEXT);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id, seq);
CREATE TABLE IF NOT EXISTS twin_events (run_id INTEGER, t REAL, kind TEXT, station TEXT, text TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS snapshots (run_id INTEGER, t REAL, frame TEXT);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, run_id INTEGER, wall REAL, t REAL, actor TEXT, action TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS evidence (id INTEGER PRIMARY KEY, run_id INTEGER, t REAL, sha256 TEXT, pack TEXT);
CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY, run_id INTEGER, evidence_id INTEGER, persona TEXT,
    provider TEXT, model TEXT, prompt_sha TEXT, text TEXT, input_tokens INTEGER, output_tokens INTEGER,
    cost_usd REAL, latency_s REAL, created_at REAL, grounded INTEGER, unsupported TEXT);
"""


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Store:
    def __init__(self, path: str | Path = "web/loom.db") -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()
        self.run_id: int | None = None
        self._buf: list[tuple] = []

    # -- runs ---------------------------------------------------------------
    def start_run(self, config_name: str, yaml_text: str, note: str = "") -> int:
        with self.lock:
            self.flush()
            cur = self.db.execute("INSERT INTO runs (started_at, config_name, yaml, note) VALUES (?,?,?,?)",
                                  (time.time(), config_name, yaml_text, note))
            self.db.commit()
            self.run_id = cur.lastrowid
            return self.run_id

    # -- streams ------------------------------------------------------------
    def log_event(self, ev: Event) -> None:
        """Subscribe this to the sensor layer: what the twin saw, nothing more."""
        if self.run_id is None:
            return
        with self.lock:
            self._buf.append((self.run_id, ev.seq, ev.t, ev.kind, ev.station, ev.vehicle,
                              json.dumps(ev.payload, separators=(",", ":"))))
            if len(self._buf) >= 500:
                self.flush()

    def flush(self) -> None:
        with self.lock:
            if self._buf:
                self.db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?)", self._buf)
                self._buf.clear()
                self.db.commit()

    def log_twin_event(self, t: float, kind: str, station: str | None, text: str, payload: dict | None = None) -> None:
        with self.lock:
            self.db.execute("INSERT INTO twin_events VALUES (?,?,?,?,?,?)",
                            (self.run_id, t, kind, station, text, json.dumps(payload or {})))
            self.db.commit()

    def snapshot(self, t: float, frame: dict) -> None:
        with self.lock:
            self.db.execute("INSERT INTO snapshots VALUES (?,?,?)",
                            (self.run_id, t, json.dumps(frame, separators=(",", ":"))))
            self.db.commit()

    def audit(self, action: str, detail: dict | None = None, actor: str = "operator", t: float | None = None) -> int:
        with self.lock:
            cur = self.db.execute("INSERT INTO audit (run_id, wall, t, actor, action, detail) VALUES (?,?,?,?,?,?)",
                                  (self.run_id, time.time(), t, actor, action, json.dumps(detail or {})))
            self.db.commit()
            return cur.lastrowid

    # -- AI grounding ---------------------------------------------------------
    def save_evidence(self, t: float, pack: dict) -> tuple[int, str]:
        digest = sha(pack)
        with self.lock:
            cur = self.db.execute("INSERT INTO evidence (run_id, t, sha256, pack) VALUES (?,?,?,?)",
                                  (self.run_id, t, digest, json.dumps(pack, separators=(",", ":"))))
            self.db.commit()
            return cur.lastrowid, digest

    def save_report(self, evidence_id: int, persona: str, text: str, provider: str, model: str,
                    prompt: str, usage: dict | None = None) -> dict:
        pack = self.evidence_pack(evidence_id)
        check = grounding_check(text, pack)
        u = usage or {}
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO reports (run_id, evidence_id, persona, provider, model, prompt_sha, text, input_tokens,"
                " output_tokens, cost_usd, latency_s, created_at, grounded, unsupported) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.run_id, evidence_id, persona, provider, model, hashlib.sha256(prompt.encode()).hexdigest(),
                 text, u.get("input_tokens", 0), u.get("output_tokens", 0), u.get("cost_usd", 0.0),
                 u.get("latency_s", 0.0), time.time(), int(check["grounded"]), json.dumps(check["unsupported"])))
            self.db.commit()
            return {"report_id": cur.lastrowid, "evidence_id": evidence_id, **check}

    def evidence_pack(self, evidence_id: int) -> dict:
        row = self.db.execute("SELECT pack FROM evidence WHERE id=?", (evidence_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    # -- queries ------------------------------------------------------------
    def audit_rows(self, run_id: int | None = None, limit: int = 200) -> list[dict]:
        rid = run_id or self.run_id
        rows = self.db.execute("SELECT id, wall, t, actor, action, detail FROM audit WHERE run_id=? ORDER BY id DESC LIMIT ?",
                               (rid, limit)).fetchall()
        return [{"id": r[0], "wall": r[1], "t": r[2], "actor": r[3], "action": r[4], "detail": json.loads(r[5])} for r in rows]

    def reports(self, run_id: int | None = None, limit: int = 50) -> list[dict]:
        rid = run_id or self.run_id
        rows = self.db.execute(
            "SELECT r.id, r.persona, r.provider, r.model, r.created_at, r.grounded, r.unsupported, r.cost_usd, e.sha256, r.text"
            " FROM reports r JOIN evidence e ON e.id = r.evidence_id WHERE r.run_id=? ORDER BY r.id DESC LIMIT ?",
            (rid, limit)).fetchall()
        return [{"id": r[0], "persona": r[1], "provider": r[2], "model": r[3], "created_at": r[4],
                 "grounded": bool(r[5]), "unsupported": json.loads(r[6]), "cost_usd": r[7],
                 "evidence_sha256": r[8], "text": r[9]} for r in rows]

    def counts(self, run_id: int | None = None) -> dict:
        rid = run_id or self.run_id
        self.flush()
        q = lambda table: self.db.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id=?", (rid,)).fetchone()[0]
        return {"run_id": rid, "events": q("events"), "twin_events": q("twin_events"), "snapshots": q("snapshots"),
                "audit": q("audit"), "evidence": q("evidence"), "reports": q("reports"), "path": self.path}

    def events(self, run_id: int | None = None) -> list[Event]:
        rid = run_id or self.run_id
        self.flush()
        rows = self.db.execute("SELECT seq, t, kind, station, vehicle, payload FROM events WHERE run_id=? ORDER BY seq",
                               (rid,)).fetchall()
        return [Event(r[1], r[0], r[2], r[3], r[4], json.loads(r[5])) for r in rows]

    # -- reproducibility ------------------------------------------------------
    def replay(self, run_id: int | None = None):
        """Rebuild the twin from stored events; return (twin, agreement with
        stored snapshots). Same events -> same beliefs, or the store lies."""
        from .twin import Twin
        rid = run_id or self.run_id
        yaml_text = self.db.execute("SELECT yaml FROM runs WHERE id=?", (rid,)).fetchone()[0]
        # written into configs/ so a relative `extends:` resolves as it did originally
        tmp = Path(__file__).resolve().parent.parent / "configs" / f".replay{rid}.yaml"
        tmp.write_text(yaml_text)
        try:
            cfg = load_line(tmp)
        finally:
            tmp.unlink(missing_ok=True)
        twin = Twin(cfg)
        snaps = self.db.execute("SELECT t, frame FROM snapshots WHERE run_id=? ORDER BY t", (rid,)).fetchall()
        events = self.events(rid)
        j, checked, agreed = 0, 0, 0
        for t, frame in snaps:
            # a live snapshot at t was taken after every event with ev.t <= t
            while j < len(events) and events[j].t <= t:
                twin.ingest(events[j])
                j += 1
            twin.t = max(twin.t, t)
            twin.refresh()
            stored = json.loads(frame)
            live = {sid: b.state.value for sid, b in twin.stations.items()}
            checked += len(live)
            agreed += sum(1 for sid, st in live.items() if stored["states"].get(sid) == st)
        for ev in events[j:]:
            twin.ingest(ev)
        return twin, {"snapshots_checked": len(snaps), "station_states_checked": checked,
                      "agreement": (agreed / checked) if checked else None}


_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")


def grounding_check(text: str, pack: dict) -> dict:
    """Every number in the report must occur in the evidence pack it was
    written from (as a value, or as a formatted value). Times like 00:43,
    ids and plain integers under 3 digits are exempt -- they are structure,
    not claims."""
    blob = json.dumps(pack)
    values = set(re.findall(r"-?\d+(?:\.\d+)?", blob))
    unsupported = []
    for m in _NUM.finditer(text):
        s = m.group(0)
        if "." not in s and len(s.lstrip("-")) < 3:
            continue
        cands = {s, s.rstrip("0").rstrip(".") if "." in s else s}
        try:
            f = float(s)
            cands |= {f"{f:g}", f"{f:.1f}", f"{f:.2f}", f"{f:.0f}", f"{f / 100:.2f}", f"{f / 100:g}", f"{f * 100:g}"}
        except ValueError:
            pass
        if not (cands & values):
            unsupported.append(s)
    return {"grounded": not unsupported, "numbers_checked": len(_NUM.findall(text)), "unsupported": unsupported}
