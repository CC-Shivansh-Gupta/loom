"""A second, different plant must run on the same code unchanged."""
from loom import views
from loom.evaluator import bottleneck_scorecard
from loom.run import build

H = 3600.0


def test_plant_b_runs_and_forecasts():
    cfg, plant, sensors, twin = build("configs/plant_b.yaml")
    assert len(cfg.stations) == 30 and cfg.takt_s == 75
    assert sensors.coverage()["B04"] == "dark"
    assert sensors.coverage()["P01"] == "checklist"
    plant.run(3 * H)
    assert plant.exited                      # vehicles get through 30 stations
    sc = bottleneck_scorecard(plant, twin)
    (s,) = sc["scores"]
    assert s.station == "T04"
    assert s.t_alert is not None and s.t_upstream_blocked is not None
    assert s.lead_s > 0
    # views render for any topology
    assert "T04" in views.supervisor(twin)
    assert "Plant B" in views.manager(twin, sc, sensors.coverage())
    assert "dark" in views.manager(twin, sc, sensors.coverage())
