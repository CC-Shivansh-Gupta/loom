from loom import views
from loom.evaluator import bottleneck_scorecard, containment_scorecard, state_agreement
from loom.run import build

H = 3600.0


def test_parallel_station_doubles_capacity_and_twin_stays_consistent():
    cfg, plant, sensors, twin = build("configs/parallel.yaml")
    plant.run(2 * H)
    f2 = plant.stations[cfg.index("F2")]
    assert f2.cfg.capacity == 2
    # two fitters at 100 s each keep up with a 60 s takt: F2 never becomes the bottleneck
    rate = len([v for v in plant.exited if v.exited_t > H]) / 1.0
    assert rate >= 55, rate
    assert not any(x.action == "raised" and x.alert.station == "F2" for x in twin.log)
    # both fitters were busy at the same moment somewhere in the run
    starts = sorted(x.start_t for v in plant.vehicles.values() for x in v.record if x.station == "F2")
    assert any(b - a < 90 for a, b in zip(starts, starts[1:]))
    ag = state_agreement(plant, twin)
    assert ag["measured_wrong"] == [], ag["measured_wrong"]


def test_rework_loop_reenters_repairs_and_overloads_inspection():
    cfg, plant, sensors, twin = build("configs/parallel_rework.yaml")
    plant.run(2 * H)
    reworked = [v for v in plant.vehicles.values() if v.pass_no > 0]
    assert len(reworked) >= 5, len(reworked)
    v = reworked[0]
    assert [x.station for x in v.record].count("F5") >= 2      # inspected twice
    assert any(r == "fail" for _, _, r in v.inspections)
    assert "weak_weld" not in v.defects                         # repaired
    # the twin saw each rework pass as a new thread arriving in F5's buffer
    ids = {ev.vehicle for ev in plant.events if ev.kind == "rework"}
    assert ids and all(i >= 1_000_000 for i in ids) and all(i in twin.tl for i in ids)
    # a quality problem became a flow problem: F5 now processes fails twice and
    # turns into the bottleneck -- and the forecaster says so
    assert any(x.action == "raised" and x.alert.station == "F5" for x in twin.log)
    # containment still works with rework in the loop
    (c,) = containment_scorecard(plant, twin)
    assert c.t_first_hold is not None and c.recall >= 0.9


def test_breaks_and_shift_change_do_not_false_alarm_but_ramp_is_caught():
    cfg, plant, sensors, twin = build("configs/shifts.yaml")
    plant.run(6 * H)
    # no releases during breaks
    assert not any(e.kind == "release" and 7200 <= e.t < 7800 for e in plant.events)
    sc = bottleneck_scorecard(plant, twin)
    assert sc["false_alarms"] == [], [str(x) for x in sc["false_alarms"]]
    (s,) = sc["scores"]
    assert s.lead_s is not None and s.lead_s > 60
    # no station was declared silent because of a break
    assert all(b.health == "ok" for b in twin.stations.values())
    # measured cycles at F1 exclude break time (no 600 s outliers)
    assert all(c < 200 for _, c, _ in twin.samples["F1"])


def test_leadership_view_prints_assumptions_and_payback():
    cfg, plant, sensors, twin = build("configs/weld_drift_b2.yaml")
    plant.run(2 * H)
    from loom import voi
    text = views.leadership(twin, bottleneck_scorecard(plant, twin), containment_scorecard(plant, twin),
                            sensors.coverage(), voi.rank(cfg, plant, twin))
    assert "annual value" in text and "payback" in text and "$" in text
    assert "targeted holds" in text and "escapes prevented" in text
