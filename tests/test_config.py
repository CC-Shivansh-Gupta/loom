import pytest

from loom.config import load_line


def test_extends_merges_and_overrides():
    base = load_line("configs/plant_demo.yaml")
    ramp = load_line("configs/ramp_b3.yaml")
    assert base.cv == 0.0 and ramp.cv == 0.05
    assert ramp.ids == base.ids
    assert ramp.perturbations[0].station == "B3"
    assert {v.name for v in ramp.variants} == {"sedan", "suv"}
    assert abs(sum(v.share for v in ramp.variants) - 1.0) < 1e-9


def test_station_types_and_sensor_profiles_resolve():
    cfg = load_line("configs/plant_demo.yaml")
    b1, f1, f2 = cfg.station("B1"), cfg.station("F1"), cfg.station("F2")
    assert b1.type.name == "robot_weld" and b1.sensors.name == "plc_full"
    assert b1.type.params == ("weld_current", "torque")
    assert f1.type.name == "door_fit"                # plant-defined type
    assert f1.type.params == ("gap_mm",)
    assert f1.sensors.name == "plc_full"             # station override wins
    assert f2.type.sensors == "checklist"            # type default...
    assert f2.sensors.name == "plc_full"             # ...overridden per station
    assert cfg.station("B4").type.inspection


def test_dark_and_partial_profiles_filter_events():
    cfg = load_line("configs/ramp_b3_dark.yaml")
    assert cfg.station("B3").sensors.name == "dark"
    assert not cfg.station("B3").sensors.passes("start")
    b2 = cfg.station("B2").sensors
    assert b2.passes("start") and b2.passes("finish") and not b2.passes("blocked")


def test_bad_references_fail_loudly(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("line:\n  takt_s: 60\n  zones:\n    - name: z\n      stations:\n"
                 "        - {id: A, cycle_s: 50}\nscenario:\n  perturbations:\n"
                 "    - {station: ZZZ, at_s: 0, cycle_s: 70}\n")
    with pytest.raises(ValueError, match="unknown station ZZZ"):
        load_line(p)
