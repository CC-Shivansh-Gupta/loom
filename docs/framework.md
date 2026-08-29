# Framework

## Layers

```
PLANT (truth)  ──events──▶  SENSORS (per-station profile)  ──events──▶  LOOM (belief)  ──▶  VIEWS (per role)
      ▲                                                                       │
      └──────────────────────── EVALUATOR (sees both, scores) ◀───────────────┘
```

- One `Event(t, kind, station, vehicle, payload)` type end to end.
- The twin holds `Tagged(value, source, t)` everywhere; `source` is measured / inferred / simulated.
- Forecasts are produced by running a tiny simulation forward from *believed* state, so they are tagged simulated.
- The evaluator is shipped, not a test fixture: its output is the plant manager's trust ledger.

## Configuration model (ISA-95-shaped)

```yaml
extends: plant_demo.yaml          # optional; deep-merge over another file
plant:      {name, site, area}
libraries:
  station_types:                  # asset templates (AAS-style submodels)
    robot_weld: {sensors: plc_full, params: [weld_current, torque]}
  sensor_profiles:                # what escapes each station
    plc_full:   {events: all}
    cycle_only: {events: [start, finish]}
    checklist:  {events: [finish], latency_s: 120}
    dark:       {events: []}
line:
  id, takt_s, cv, seed, default_buffer
  zones:
    - name: body
      stations:
        - {id: B3, type: robot_weld, cycle_s: 56, buffer_before: 2, sensors: dark}
variants:                         # mixed-model
  suv: {share: 0.4, cycle_mult: {F1: 1.04}}
scenario:
  perturbations:                  # cycle-time faults
    - {station: B3, at_s: 1800, ramp_s: 1200, cycle_s: 80}
  sensor_faults:                  # instrumentation goes silent
    - {station: B2, at_s: 2400, duration_s: 1500}
  param_drifts:                   # process parameter mean shifts
    - {station: B2, param: weld_current, at_s: 1800, ramp_s: 1800, to: 7.4}
  defects:                        # latent, multi-cause, visible only at an inspection
    - name: weak_weld
      causes: [{station: B2, param: weld_current, below: 8.0}]
      p: 0.8
      detected_at: F5
      detect_p: 0.9
libraries:
  params:                         # spec per parameter: nominal, natural sd, limits, sensor noise
    weld_current: {nominal: 8.5, sd: 0.12, lsl: 8.0, usl: 9.0, unit: kA, meas_sd: 0.03}
```

Station types list the parameters they carry (`robot_weld: [weld_current, torque]`); a station can
override with `params:`. Only `plc_full` profiles report parameter readings; other stations' parameters
are unknown to the twin, and any hold that depends on them lists those vehicles as ◐ uncertain.

## Quality mechanism

```
readings ──▶ ParamMonitor (EWMA + CUSUM, k=0.5σ, h=8σ) ──▶ DriftAlert (onset, time-to-limit)  [warning]
                                                               │ first out-of-spec reading
                                                               ▼
                                                    Hold (back-filled from onset, grows per reading)
inspection fails ──▶ contribution analysis (lift, Fisher exact, singles + pairs) ──▶ ranked hypotheses
                                                               │ top hypothesis
                                                               ▼
                                                    Hold (vehicles matching the condition, not yet inspected)
```

## Noisy (finish-only) stations

A manual checklist stamps finishes with ~30 s jitter. Per-vehicle cycles from it are garbage, and
`max(arrive, prev_exit)` biases them upward, so for any station whose jitter exceeds 5 % of its
cycle the twin: (a) takes a **windowed throughput** sample — (t_last − t_first)/n over 12 finishes —
but only when the next vehicle had measurably arrived before each finish (never starved) and no
downstream station is congested (never blocked); (b) excludes the station from the momentary-
bottleneck vote, since its idle gaps are invisible. Two adjacent checklist stations therefore cannot
be separated: the twin abstains. This is what took plant B from 9 false alarms and 3 % bottleneck
agreement to 0 and 95 %.

Why `h=8` rather than the textbook 5: a plant runs dozens of charts at once, so the in-control run
length per chart must be ~10⁴ samples. Measured: 2 drift *warnings* and 0 holds in 8 h on the healthy
line. A drift warning never holds on its own — only an out-of-spec reading does.

Resolution order for a station's sensors: station `sensors:` → its type's default → `plc_full`.
Built-in libraries can be extended or overridden per plant file.

## Extension points (where the next steps plug in)

| concern | hook |
|---|---|
| cycle-time variation, drift, faults | `Station.cycle_time()` / `Station.nominal_cycle()` in `plant.py` |
| new sensor behaviour (noise, sampling) | `SensorProfile` fields + `SensorLayer.observe()` |
| inference for dark stations | `Twin.ingest()` — fill gaps, tag ◐ |
| new predictors | `forecast.py`; register with `Twin._on_cycle()` |
| new outcome metrics | `evaluator.py` |
| new roles | `views.py` |
| topology beyond serial (parallel stations, rework loops) | `Plant._try_push()` routing; config `next:` |

## Build log

1. Deterministic serial line, pass-through sensors, mirror twin, evaluator.
2. Stochastic cycles, ramp perturbations, trend forecaster with false-alarm guards, scorecard.
3. Config libraries + `extends`, sensor profiles that filter, model variants, role views, second plant.
4. Sensor noise model; flow reconstruction (R1–R5 in `twin.py`) for dark / finish-only / silent stations with exact-vs-bound provenance; sensor health; alert grouping; value-of-information ranking.
5. Process parameters with spec limits; EWMA/CUSUM drift monitors with onset estimate and time-to-limit projection; latent multi-cause defects surfacing at inspection stations; contribution analysis (lift + Fisher exact, pairs); targeted holds (sure / uncertain / already-exited) that grow while a drift is on; containment scorecard; quality view.
6. AI layer (`docs/ai_layer.md`): evidence pack, provider boundary with template fallback and telemetry, persona reports, what-if mitigation engine, evaluation harness + gated improvement loop + calibration table, onboarding assistant.
7. Active-period momentary-bottleneck detector (Roser) in the twin, scored against the plant's own active periods; sustained-block truth for lead time (ignores surge transients); shifting-bottleneck scenario; maintenance view; multi-seed benchmark (`loom/bench.py` → `docs/benchmark.md`).
8. Control room: `loom/export.py` timelines (truth + belief every 10 s, persona texts every 60 s, scorecards) → `web/build.py` → one self-contained Three.js page with two lanes, timeline, station cards, persona tabs. Published as a shareable artifact.  ← here
7. Multi-run evaluation harness, calibration curve, active-period cross-check, shifting bottlenecks.
8. Web UI, leadership view with ROI model.

Full mapping to the brief: `docs/solution_design.md`.
