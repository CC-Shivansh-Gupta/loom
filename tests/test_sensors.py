from loom.evaluator import state_agreement
from loom.run import build

H = 3600.0


def test_dark_station_drops_all_its_events():
    cfg, plant, sensors, twin = build("configs/ramp_b3_dark.yaml")
    plant.run(2 * H)
    b3_truth = sum(1 for e in plant.events if e.station == "B3")
    assert b3_truth > 100
    assert sensors.dropped >= b3_truth
    # and the twin's picture of B3 is consequently stale/wrong at some point
    mism = state_agreement(plant, twin)["mismatches"]
    assert any(m[0] == "B3" for m in mism)


def test_cycle_only_profile_keeps_start_finish():
    cfg, plant, sensors, twin = build("configs/ramp_b3_dark.yaml")
    seen = []
    sensors.subscribers.append(lambda e: seen.append(e))
    plant.run(H)
    kinds = {e.kind for e in seen if e.station == "B2"}
    assert kinds == {"start", "finish"}
    assert twin.stations["B2"].cycle_s.value is not None
