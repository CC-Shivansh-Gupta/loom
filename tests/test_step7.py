from loom import bench, views
from loom.evaluator import active_period_agreement, bottleneck_scorecard
from loom.run import build

H = 3600.0


def test_shifting_bottleneck_both_caught_in_order():
    cfg, plant, sensors, twin = build("configs/shifting.yaml")
    plant.run(2.5 * H)
    sc = bottleneck_scorecard(plant, twin)
    assert [s.station for s in sc["scores"]] == ["B3", "F3"]      # recovery row filtered
    for s in sc["scores"]:
        assert s.t_alert is not None and s.lead_s is not None and s.lead_s > 60, s
    assert sc["scores"][0].t_alert < sc["scores"][1].t_alert
    assert sc["false_alarms"] == []
    # the B3 alert cleared after the repair
    actions = [(x.action, x.alert.station) for x in twin.log]
    assert ("cleared", "B3") in actions


def test_active_period_follows_the_bottleneck():
    cfg, plant, sensors, twin = build("configs/shifting.yaml")
    plant.run(2.5 * H)
    ap = active_period_agreement(plant, twin)
    assert ap["fault_samples"] > 40
    assert ap["fault_agreement"] >= 0.9, ap        # twin (partial data) vs plant (complete data)


def test_active_period_survives_a_dark_bottleneck():
    cfg, plant, sensors, twin = build("configs/ramp_b3_dark.yaml")
    plant.run(2 * H)
    ap = active_period_agreement(plant, twin)
    assert ap["fault_agreement"] >= 0.9, ap


def test_active_period_is_none_on_idle_line():
    cfg, plant, sensors, twin = build("configs/plant_demo.yaml")
    assert twin.bottleneck_now() is None


def test_maintenance_view_flags_wearing_station():
    cfg, plant, sensors, twin = build("configs/ramp_b3.yaml")
    plant.run(0.8 * H)
    text = views.maintenance(twin)
    assert "B3" in text and "SCHEDULE" in text
    assert "next window: B3" in text


def test_bench_runs_and_reports():
    res = bench.run(1, [("configs/healthy.yaml", 1.0, "healthy"),
                        ("configs/ramp_b3.yaml", 1.5, "flow"),
                        ("configs/weld_drift_b2.yaml", 1.5, "quality")])
    md = bench.markdown(res, 1)
    assert "## Bottleneck forecasting" in md and "ramp_b3" in md
    assert "## Containment" in md and "weld_drift_b2" in md
    assert res["configs/ramp_b3.yaml"].leads
