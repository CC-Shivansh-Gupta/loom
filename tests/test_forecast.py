from loom.evaluator import bottleneck_scorecard
from loom.run import build

H = 3600.0


def test_ramp_is_linear_in_plant():
    _, plant, _, _ = build("configs/ramp_b3.yaml")
    assert plant.true_cycle("B3", 0) == 56
    assert plant.true_cycle("B3", 1800) == 56
    assert abs(plant.true_cycle("B3", 2400) - 68) < 1e-9
    assert plant.true_cycle("B3", 4000) == 80
    assert plant.true_cycle("B2", 4000) == 57      # untouched


def test_ramp_warning_arrives_before_upstream_blocks():
    cfg, plant, sensors, twin = build("configs/ramp_b3.yaml")
    plant.run(2 * H)
    sc = bottleneck_scorecard(plant, twin)
    (s,) = sc["scores"]
    assert s.t_upstream_blocked is not None
    assert s.t_alert is not None, "twin never warned"
    assert s.lead_s > 120, f"warned only {s.lead_s:.0f}s ahead"
    assert abs(s.eta_error_s) < 0.5 * s.lead_s + 120, "ETA badly miscalibrated"
    assert sc["false_alarms"] == []


def test_false_alarm_rate_on_healthy_noisy_line():
    # Product spec: fewer than one false alarm per five 8 h shifts on a
    # healthy line with 5 % cycle noise and a 2-variant mix.
    raised = []
    for seed in range(5):
        cfg, plant, sensors, twin = build("configs/healthy.yaml")
        plant.rng.seed(seed)
        plant.run(8 * H)
        raised += [f"seed {seed}: {x}" for x in twin.log if x.action == "raised"]
    assert len(raised) <= 1, raised


def test_alert_clears_after_recovery(tmp_path):
    p = tmp_path / "recover.yaml"
    p.write_text(
        "extends: ramp_b3.yaml\n"
        "scenario:\n  perturbations:\n"
        "    - {station: B3, at_s: 1800, ramp_s: 1200, cycle_s: 80}\n"
        "    - {station: B3, at_s: 4200, ramp_s: 0, cycle_s: 56}\n")
    # extends is resolved relative to the file, so point it at the real configs dir
    p.write_text(p.read_text().replace("extends: ramp_b3.yaml",
                                       f"extends: {__import__('os').path.abspath('configs/ramp_b3.yaml')}"))
    cfg, plant, sensors, twin = build(str(p))
    plant.run(3 * H)
    actions = [x.action for x in twin.log if x.alert.station == "B3"]
    assert actions[:2] == ["raised", "cleared"]
    assert "B3" not in twin.active
