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
  perturbations:
    - {station: B3, at_s: 1800, ramp_s: 1200, cycle_s: 80}
```

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
3. Config libraries + `extends`, sensor profiles that filter, model variants, role views, second plant.  ← here
4. Dark-station interval inference (soft sensor for flow state), value-of-information ranking.
5. Process parameters, EWMA/CUSUM drift, latent defects, inspection outcomes, build-record trace, targeted hold, quality view.
6. Mitigations as recommendations with expected effect, alert grouping, maintenance view.
7. Multi-run evaluation harness, calibration curve, active-period cross-check, shifting bottlenecks.
8. Web UI, leadership view with ROI model.

Full mapping to the brief: `docs/solution_design.md`.
