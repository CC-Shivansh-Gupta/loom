from loom.evaluator import containment_scorecard
from loom.quality import QualityTwin, fisher_right
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


def test_hold_survives_an_early_unlucky_sample():
    """A hold is withdrawn on what the evidence *rules out*, not on a point
    estimate. Inspection sits minutes downstream of the cause station, so the
    judged vehicles are systematically older than the held ones and the early
    point estimate is both low and worthless: 2 of 5 reads 0.40 and would
    refute a good hold, while its upper bound is 0.77 and does not."""
    ucb = QualityTwin._posterior_ucb
    assert ucb(2, 5) > QualityTwin.HOLD_MIN_POSTERIOR      # too early to refute
    assert ucb(0, 3) > QualityTwin.HOLD_MIN_POSTERIOR      # three good ones prove nothing
    assert ucb(2, 40) < QualityTwin.HOLD_MIN_POSTERIOR     # 5% over 40 does
    assert ucb(0, 0) == 1.0                                # no evidence never refutes
    # and it is a bound, never below the point estimate it wraps
    for a, n in ((1, 4), (7, 9), (20, 40)):
        assert ucb(a, n) >= a / n


def test_multi_cause_samples_before_it_holds():
    """The scenario must not end in a bad hold. While the leading hypothesis is
    a single condition with a posterior under the break-even bar, the twin asks
    for a discriminating sample; it holds only once the pair overtakes it."""
    cfg, plant, sensors, twin = build("configs/multi_cause.yaml")
    plant.run(3 * H)
    q = twin.quality
    curve = q.precision_curve
    assert curve, "no contribution run recorded"
    holds = [r for r in curve if r["action"] == "hold"]
    samples = [r for r in curve if r["action"] == "sample"]
    assert samples, "the twin never abstained, so it never faced the multi-cause case"
    assert holds, "the twin never resolved the pair"
    # every abstention precedes every hold, and the posterior rises across it
    assert max(r["fails"] for r in samples) < max(r["fails"] for r in holds)
    assert holds[-1]["posterior"] > samples[0]["posterior"]
    # what it holds on is the pair; what it declined to hold on was not
    assert len(holds[-1]["conditions"]) == 2
    assert holds[-1]["posterior"] >= QualityTwin.HOLD_MIN_POSTERIOR
    # and the abstention names vehicles to inspect rather than doing nothing
    assert q.sample_requests
    sr = q.sample_requests[0]
    assert sr.vehicles and len(sr.vehicles) <= QualityTwin.SAMPLE_K
    assert all(v in sr.supports for v in sr.vehicles)
