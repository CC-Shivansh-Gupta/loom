from loom.bench import _run
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


def test_sampled_profile_reports_one_reading_in_five():
    """Audit-sample parameter logging: cycle timestamps are complete, process
    parameters arrive for one body in five, and the sampling is deterministic in
    the count rather than random — a plant reads every fifth body, it does not
    flip a coin per body."""
    cfg, plant, sensors, twin = build("configs/weld_drift_b2_sampled.yaml")
    seen = []
    sensors.subscribers.append(lambda e: seen.append(e))
    plant.run(1 * H)
    params = [e for e in seen if e.kind == "param" and e.station == "B2"
              and e.payload["param"] == "weld_current"]
    starts = [e for e in seen if e.kind == "start" and e.station == "B2"]
    assert starts, "B2 reported no cycle events, so this measures nothing"
    # one in five of what the station produced, and cycle data is untouched
    assert 0.15 < len(params) / len(starts) < 0.25, (len(params), len(starts))
    # the control: the same drift with everything reported
    _, plant2, sensors2, _ = build("configs/weld_drift_b2.yaml")
    seen2 = []
    sensors2.subscribers.append(lambda e: seen2.append(e))
    plant2.run(1 * H)
    full = [e for e in seen2 if e.kind == "param" and e.station == "B2"
            and e.payload["param"] == "weld_current"]
    assert len(full) > 4 * len(params)


def test_backfill_starts_at_the_spec_crossing_not_the_drift_onset():
    """A hold is for out-of-spec product. The CUSUM onset is when the parameter
    began moving, which on sampled data precedes the drift itself; back-filling
    from there swallows the silent-but-in-spec stretch."""
    moved = 0
    for seed in range(6):
        cfg, plant, twin = _run("configs/weld_drift_b2_sampled.yaml", 2.0, seed)
        q = twin.quality
        drift = [h for h in q.holds if h.reason == "drift"]
        if not drift:
            continue
        alert = next(a for a in q.drift_log if a.station == drift[0].station)
        assert drift[0].onset_t is not None
        # the contract: never earlier than the CUSUM onset, never later than the
        # hold. An estimate outside the interval the drift is known to live in is
        # not evidence, and falls back to the onset.
        assert alert.onset_t <= drift[0].onset_t <= drift[0].t
        moved += drift[0].onset_t > alert.onset_t
    assert moved, "the crossing estimate never moved off the onset on any seed"
