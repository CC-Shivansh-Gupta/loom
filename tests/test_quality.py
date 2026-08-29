from loom.evaluator import containment_scorecard
from loom.quality import fisher_right
from loom.run import build

H = 3600.0


def test_fisher_exact_sanity():
    assert fisher_right(0, 10, 0, 10) == 1.0
    assert fisher_right(10, 0, 0, 10) < 1e-4
    assert 0.1 < fisher_right(3, 7, 3, 7) < 1.0


def test_params_are_sampled_and_reported_only_where_instrumented():
    cfg, plant, sensors, twin = build("configs/ramp_b3_dark.yaml")
    plant.run(H)
    v = plant.exited[0]
    assert set(v.record[0].params) == {"weld_current", "torque"}      # B1 robot_weld
    q = twin.quality
    assert ("B1", "weld_current") in q.params[v.id]
    assert ("B3", "weld_current") not in q.params[v.id]                # dark
    assert "B3" not in q.reports


def test_silent_weld_drift_is_caught_and_contained():
    cfg, plant, sensors, twin = build("configs/weld_drift_b2.yaml")
    plant.run(2 * H)
    q = twin.quality
    assert twin.log == []                                  # flow twin sees nothing
    drift = [a for a in q.drift_log if a.station == "B2" and a.param == "weld_current"]
    assert drift and drift[0].direction == "low"
    a = drift[0]
    assert 1800 <= a.onset_t <= 1800 + 900, a              # onset estimate within 15 min
    (c,) = containment_scorecard(plant, twin)
    assert c.n_defective >= 15
    assert c.t_first_hold is not None and c.lag_s < 40 * 60
    assert c.t_first_hold < c.t_first_fail - 5 * 60      # ahead of end-of-line inspection
    assert c.precision is not None and c.precision >= 0.6
    assert c.recall is not None and c.recall >= 0.9
    assert c.hold_size <= c.blanket_size
    assert c.escaped <= 2


def test_healthy_line_drift_warnings_never_hold():
    cfg, plant, sensors, twin = build("configs/healthy.yaml")
    plant.run(8 * H)
    q = twin.quality
    assert len(q.drift_log) <= 3            # in-control CUSUM warnings, line-wide, 8 h
    assert q.holds == []                    # never out of spec, never a hold


def test_multi_cause_pair_is_found():
    cfg, plant, sensors, twin = build("configs/multi_cause.yaml")
    plant.run(3 * H)
    q = twin.quality
    assert q.hypotheses, "no hypotheses produced"
    top = q.hypotheses[0]
    names = {(c.station, c.param) for c in top.conditions}
    assert names == {("B4", "torque"), ("P1", "humidity")}, str(top)
    assert top.lift > 5
    insp = [h for h in q.holds if h.reason == "inspection"]
    assert insp and {(c.station, c.param) for c in insp[0].hypothesis.conditions} == names
    (c,) = containment_scorecard(plant, twin)
    assert c.escaped == 0
