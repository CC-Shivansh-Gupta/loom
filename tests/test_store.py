import json

from loom import llm
from loom.live import LiveSim
from loom.store import Store, grounding_check


def _run(tmp_path, config="ramp_b3_dark.yaml", minutes=60):
    store = Store(tmp_path / "loom.db")
    sim = LiveSim(config, store=store)
    sim.playing = True
    sim.speed = 600
    for _ in range(minutes):
        sim.step(0.1)                                   # 60 s of line time per step
    return store, sim


def test_store_captures_events_snapshots_and_audit(tmp_path):
    store, sim = _run(tmp_path)
    sim.inject("sensor", "B2", profile="dark")
    sim.inject("slow", "B4", cycle_s=90, ramp_s=0)
    for _ in range(20):
        sim.step(0.1)
    c = store.counts()
    assert c["events"] > 1000 and c["snapshots"] >= 60 and c["twin_events"] >= 1
    actions = [r["action"] for r in store.audit_rows()]
    assert "load" in actions and "inject:sensor" in actions and "inject:slow" in actions
    # the store holds what the twin saw, not what the plant did: B3 is dark
    assert not any(e.station == "B3" for e in store.events())
    assert any(e.station == "B2" and e.t < 3600 for e in store.events())
    assert not any(e.station == "B2" and e.t > 3700 for e in store.events())


def test_replay_reproduces_the_twin(tmp_path):
    store, sim = _run(tmp_path, minutes=90)
    twin2, agree = store.replay()
    assert agree["snapshots_checked"] >= 80
    assert agree["agreement"] >= 0.97, agree
    assert twin2.exited == sim.twin.exited
    assert [x.alert.station for x in twin2.log] == [x.alert.station for x in sim.twin.log]


def test_reports_are_stored_hashed_and_grounding_checked(tmp_path):
    store, sim = _run(tmp_path, config="weld_drift_b2.yaml", minutes=90)
    out = sim.report("quality", llm.template_provider())
    assert out["grounded"] is True, out["unsupported"]
    (rep,) = store.reports()
    assert rep["persona"] == "quality" and rep["provider"] == "template"
    assert rep["evidence_sha256"] == out["evidence_sha256"]
    pack = store.evidence_pack(out["evidence_id"])
    assert pack["line"]["id"] == "L1-weld-drift-b2"
    assert any(r["action"] == "report" and r["actor"] == "loom" for r in store.audit_rows())


def test_grounding_check_catches_invented_numbers():
    pack = {"output": {"veh_per_h": 45.3, "vehicles_out": 68}, "ledger": {"bottleneck": [{"lead_min": 5.1}]}}
    ok = grounding_check("Output 68 vehicles at 45.3 veh/h; warned 5.1 min ahead.", pack)
    assert ok["grounded"]
    bad = grounding_check("Output 68 vehicles at 47.9 veh/h; warned 12.5 min ahead.", pack)
    assert not bad["grounded"] and set(bad["unsupported"]) == {"47.9", "12.5"}


def test_acknowledgement_is_audited_and_clears(tmp_path):
    store, sim = _run(tmp_path, minutes=70)                # ramp_b3_dark: B3 alert by ~44 min
    assert "B3" in sim.twin.active
    rec = sim.acknowledge("B3", "dismiss", note="planned changeover", actor="supervisor")
    assert "B3" not in sim.twin.active
    row = next(r for r in store.audit_rows() if r["action"] == "ack:dismiss")
    assert row["actor"] == "supervisor" and row["detail"]["note"] == "planned changeover"
