from loom.live import LiveSim


def test_live_sim_runs_and_injects():
    sim = LiveSim("healthy.yaml")
    sim.playing = True
    sim.speed = 600
    for _ in range(20):
        sim.step(0.1)                       # 20 x 60 s = 20 min of line time
    f = sim.frame()
    assert f["t"] >= 1100 and f["out"][0] > 5
    assert len(f["st"]) == 12

    sim.inject("slow", "B3", cycle_s=80, ramp_s=0)
    sim.inject("sensor", "B3", profile="dark")
    assert sim.meta()["coverage"]["B3"] == "dark"
    for _ in range(60):
        sim.step(0.1)                       # +60 min
    f = sim.frame()
    b3 = f["st"][2]
    assert b3[3] == 1                       # belief provenance: inferred
    assert any(x.action == "raised" and x.alert.station == "B3" for x in sim.twin.log)
    sc = sim.scorecard()
    assert sc["bottleneck"] and sc["bottleneck"][0]["station"] == "B3"

    d = sim.station_detail("B3")
    assert d["sensors"] == "dark" and d["belief"]["inferred_samples"] > 0
    assert d["truth_cycles"] and d["belief_cycles"]
    assert sim.view("supervisor").startswith("SUPERVISOR")


def test_live_config_reload_and_drift():
    sim = LiveSim("weld_drift_b2.yaml")
    sim.load_yaml(sim.yaml_text.replace("id: L1-weld-drift-b2", "id: L1-edited"))
    assert sim.meta()["id"] == "L1-edited"
    sim.playing = True
    sim.speed = 600
    sim.inject("drift", "B1", param="torque", ramp_s=0)
    for _ in range(40):
        sim.step(0.1)
    d = sim.station_detail("B1")
    tq = next(q for q in d["params"] if q["name"] == "torque")
    assert tq["drift"] is not None and tq["true_mean_now"] < tq["lsl"]
    assert any(e["kind"] == "drift" for e in sim.injections) or sim.twin.quality.drift_log


def test_bad_yaml_is_rejected_without_breaking_the_sim():
    import pytest
    sim = LiveSim("healthy.yaml")
    with pytest.raises(Exception):
        sim.load_yaml("line:\n  takt_s: 60\n  zones: []\n")
    assert sim.meta()["id"] == "L1-healthy"


def test_recording_produces_replay_page(tmp_path, monkeypatch):
    from loom import live
    monkeypatch.setattr(live, "RECORDINGS", tmp_path)
    sim = LiveSim("ramp_b3.yaml")
    sim.playing = True
    sim.speed = 600
    for _ in range(10):
        sim.step(0.1)
    sim.start_recording("demo run")
    sim.inject("sensor", "B3", profile="dark")
    for _ in range(40):
        sim.step(0.1)
    r = sim.stop_recording()
    assert r["frames"] >= 20 and r["minutes"] > 30
    html = (tmp_path / "demo_run.html").read_text()
    assert "Loom Control Room" in html and "demo run" in html
    import json
    data = json.loads((tmp_path / "demo_run.json").read_text())
    assert data["meta"]["recorded_from_s"] > 0
    assert any(e["kind"] == "inject" for e in data["events"])
    assert data["frames"][-1]["st"][2][3] == 1          # B3 inferred in the replay too
