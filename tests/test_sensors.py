from loom.run import build
from loom.twin import INFERRED

H = 3600.0


def test_dark_station_drops_all_its_events():
    cfg, plant, sensors, twin = build("configs/ramp_b3_dark.yaml")
    seen = []
    sensors.subscribers.append(lambda e: seen.append(e))
    plant.run(2 * H)
    assert not any(e.station == "B3" for e in seen)
    twin.refresh()
    assert twin.stations["B3"].state.source == INFERRED


def test_cycle_only_profile_keeps_start_finish_with_jitter():
    cfg, plant, sensors, twin = build("configs/ramp_b3_dark.yaml")
    seen = []
    sensors.subscribers.append(lambda e: seen.append(e))
    plant.run(H)
    b2 = [e for e in seen if e.station == "B2"]
    assert {e.kind for e in b2} == {"start", "finish"}
    truth = {(e.kind, e.vehicle): e.t for e in plant.events if e.station == "B2"}
    errs = [abs(e.t - truth[(e.kind, e.vehicle)]) for e in b2]
    assert 0 < sum(errs) / len(errs) < 2.0            # ~1 s jitter, not zero
    assert twin.stations["B2"].cycle_s.value is not None


def test_checklist_profile_is_late_and_noisy():
    cfg, plant, sensors, twin = build("configs/plant_b.yaml")
    seen = []
    sensors.subscribers.append(lambda e: (seen.append((plant.t, e))))
    plant.run(H)
    p01 = [(now, e) for now, e in seen if e.station == "P01"]
    assert p01 and all(e.kind == "finish" for _, e in p01)
    delays = [now - e.t for now, e in p01]
    assert 100 < sum(delays) / len(delays) < 140        # ~120 s late, ±30 s jitter
