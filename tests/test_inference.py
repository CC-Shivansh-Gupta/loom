from loom import voi
from loom.evaluator import bottleneck_scorecard, inference_accuracy, state_agreement
from loom.run import build
from loom.twin import INFERRED, MEASURED

H = 3600.0


def test_fully_instrumented_noisy_line_measured_beliefs_are_right():
    cfg, plant, sensors, twin = build("configs/healthy.yaml")
    plant.run(2 * H + 17)
    ag = state_agreement(plant, twin)
    assert ag["measured_wrong"] == [], ag["measured_wrong"]


def test_dark_station_is_reconstructed_and_forecast():
    cfg, plant, sensors, twin = build("configs/ramp_b3_dark.yaml")
    plant.run(2 * H)
    b3 = twin.stations["B3"]
    assert b3.measured_samples == 0 and b3.inferred_samples > 20
    acc = inference_accuracy(plant, twin)["B3"][INFERRED]
    assert acc["mae"] < 3.0, acc            # exact rule + neighbour jitter only
    sc = bottleneck_scorecard(plant, twin)
    (s,) = sc["scores"]
    assert s.t_alert is not None and s.lead_s is not None and s.lead_s > 60
    assert s.alert_inferred_share == 1.0
    assert sc["false_alarms"] == []
    twin.refresh()
    assert b3.state.source == INFERRED


def test_buffer_counts_never_go_negative():
    cfg, plant, sensors, twin = build("configs/ramp_b3_dark.yaml")
    plant.run(2 * H)
    for i in range(twin.n):
        assert twin.buffer_count(i).value >= 0


def test_checklist_station_gets_windowed_cycles_only_when_queued(tmp_path):
    import os
    # T01 (checklist, measured arrivals from P05) becomes the bottleneck:
    # queued and never blocked, so windowed throughput == its cycle.
    p = tmp_path / "t01_slow.yaml"
    p.write_text(f"extends: {os.path.abspath('configs/plant_b.yaml')}\n"
                 "scenario:\n  perturbations:\n"
                 "    - {station: T01, at_s: 1800, ramp_s: 900, cycle_s: 95}\n")
    cfg, plant, sensors, twin = build(str(p))
    plant.run(2.5 * H)
    t01 = twin.stations["T01"]
    assert t01.inferred_samples > 5 and t01.measured_samples == 0
    assert any(x.action == "raised" and x.alert.station == "T01" for x in twin.log)
    # P01 (checklist) is never queued on a healthy stretch: finish-only
    # timestamps cannot separate work from waiting, so the twin abstains.
    p01 = twin.stations["P01"]
    assert p01.measured_samples == 0 and p01.inferred_samples <= 3
    twin.refresh()
    assert p01.state.source == "inferred"


def test_silent_sensor_is_detected_and_bridged():
    cfg, plant, sensors, twin = build("configs/sensor_fault_b2.yaml")
    health, prov = {}, {}
    orig = twin.ingest

    def spy(ev):
        orig(ev)
        slot = int(ev.t // 300)
        health.setdefault(slot, twin.stations["B2"].health)
        prov.setdefault(slot, twin.station_state(1)[0].source)
    sensors.subscribers[0] = spy
    plant.run(2 * H)
    assert sensors.silenced > 50
    mid = int(3600 // 300)
    assert health[mid] == "silent"                   # noticed
    assert prov[mid] == INFERRED                     # bridged, not frozen
    assert prov[int(600 // 300)] == MEASURED
    assert twin.stations["B2"].health == "ok"        # recovered
    acc = inference_accuracy(plant, twin)["B2"]
    assert acc[MEASURED]["n"] > 50
    sc = bottleneck_scorecard(plant, twin)
    assert sc["scores"][0].lead_s is not None and sc["false_alarms"] == []


def test_voi_ranks_the_dark_bottleneck_first():
    cfg, plant, sensors, twin = build("configs/ramp_b3_dark.yaml")
    plant.run(2 * H)
    ranking = voi.rank(cfg, plant, twin)
    assert ranking[0]["station"] == "B3"
    assert ranking[0]["d_samples_per_h"] > 0
