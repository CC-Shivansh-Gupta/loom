from loom.config import load_line
from loom.evaluator import bottleneck_scorecard
from loom.run import build

H = 3600.0


def test_ramp_is_linear_in_plant():
    cfg = load_line("configs/line_ramp_b3.yaml")
    _, plant, _, _ = build("configs/line_ramp_b3.yaml")
    assert plant.true_cycle("B3", 0) == 56
    assert plant.true_cycle("B3", 1800) == 56
    assert abs(plant.true_cycle("B3", 2400) - 68) < 1e-9
    assert plant.true_cycle("B3", 4000) == 80
    assert plant.true_cycle("B2", 4000) == 57      # untouched


def test_ramp_warning_arrives_before_upstream_blocks():
    cfg, plant, sensors, twin = build("configs/line_ramp_b3.yaml")
    plant.run(2 * H)
    sc = bottleneck_scorecard(plant, twin)
    (s,) = sc["scores"]
    assert s.t_upstream_blocked is not None
    assert s.t_alert is not None, "twin never warned"
    assert s.lead_s > 60, f"warned only {s.lead_s:.0f}s ahead"
    assert abs(s.eta_error_s) < 0.5 * s.lead_s + 120, "ETA badly miscalibrated"


def test_no_false_alarms_on_healthy_noisy_line():
    for seed in range(5):
        cfg, plant, sensors, twin = build("configs/line_stochastic.yaml")
        plant.rng.seed(seed)
        plant.run(8 * H)
        sc = bottleneck_scorecard(plant, twin)
        assert sc["alerts_raised"] == 0, f"seed {seed}: {[str(x) for x in twin.log]}"


def test_alert_clears_after_recovery(tmp_path):
    src = open("configs/line_ramp_b3.yaml").read()
    # slow down, then recover 40 min later
    src += "  - {station: B3, at_s: 4200, ramp_s: 0, cycle_s: 56}\n"
    p = tmp_path / "recover.yaml"
    p.write_text(src)
    cfg, plant, sensors, twin = build(str(p))
    plant.run(3 * H)
    actions = [x.action for x in twin.log if x.alert.station == "B3"]
    assert actions[:2] == ["raised", "cleared"]
    assert "B3" not in twin.active
