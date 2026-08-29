from loom.evaluator import state_agreement
from loom.plant import BLOCKED_STATE, IDLE
from loom.run import build

H = 3600.0


def steady_rate(plant, warmup=H, total=4 * H):
    """Exits per hour after the line has filled."""
    n = sum(1 for v in plant.exited if v.exited_t >= warmup)
    return n / ((total - warmup) / H)


def test_balanced_line_hits_takt():
    cfg, plant, sensors, twin = build("configs/plant_demo.yaml")
    plant.run(4 * H)
    assert 59 <= steady_rate(plant) <= 60          # takt ceiling is 60/h
    assert not any(e.kind == "lost_slot" for e in plant.events)
    assert all(s.time_in[BLOCKED_STATE] == 0 for s in plant.stations)


def test_slow_station_propagates():
    cfg, plant, sensors, twin = build("configs/slow_b3.yaml")
    plant.run(4 * H)
    assert 44 <= steady_rate(plant) <= 45          # 3600/80 = 45/h
    by_id = {s.cfg.id: s for s in plant.stations}
    assert by_id["B2"].time_in[BLOCKED_STATE] > 0.2 * 4 * H   # upstream blocks
    assert by_id["F5"].time_in[IDLE] > 0.2 * 4 * H            # downstream starves
    assert any(e.kind == "lost_slot" for e in plant.events)


def test_twin_mirrors_truth_when_fully_instrumented():
    cfg, plant, sensors, twin = build("configs/slow_b3.yaml")
    plant.run(2 * H + 17)                         # odd time: mid-cycle state
    assert state_agreement(plant, twin)["mismatches"] == []
    assert twin.seen == len(plant.events)


def test_build_record_is_complete():
    cfg, plant, sensors, twin = build("configs/plant_demo.yaml")
    plant.run(2 * H)
    v = plant.exited[0]
    assert [x.station for x in v.record] == cfg.ids
    for a, b in zip(v.record, v.record[1:]):
        assert a.start_t <= a.finish_t <= a.exit_t <= b.start_t


def test_variant_mix_and_multipliers():
    cfg, plant, sensors, twin = build("configs/healthy.yaml")
    plant.run(8 * H)
    counts = {}
    for v in plant.vehicles.values():
        counts[v.variant] = counts.get(v.variant, 0) + 1
    share = counts["suv"] / sum(counts.values())
    assert 0.33 <= share <= 0.47
    # SUVs take longer at F1; sedans do not
    def mean_cycle(variant, station):
        xs = [x.finish_t - x.start_t for v in plant.exited if v.variant == variant
              for x in v.record if x.station == station]
        return sum(xs) / len(xs)
    assert mean_cycle("suv", "F1") > mean_cycle("sedan", "F1") * 1.02
    assert abs(mean_cycle("suv", "F2") - mean_cycle("sedan", "F2")) < 1.0
