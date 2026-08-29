# Loom — assembly-line digital twin prototype

Four layers, one event schema, a hard wall between truth and belief.

| layer | module | sees |
|---|---|---|
| 1 Plant (ground truth) | `loom/plant.py` | everything |
| 2 Sensors | `loom/sensors.py` | plant events → forwards what each station's sensor profile allows |
| 3 Loom (twin) | `loom/twin.py`, `loom/forecast.py` | only what sensors forward; tags every value ● measured / ◐ inferred / ○ simulated |
| 4 Evaluator | `loom/evaluator.py` | plant **and** twin; scores predictions against outcomes |
| Views | `loom/views.py` | the twin, rendered per role |

The line is data (`configs/*.yaml`, ISA-95-shaped, with `extends:` for scenarios).
See `docs/research.md` for landscape and differentiators, `docs/forecaster_tuning.md` for
how the alert thresholds were chosen.

```
make test
make demo            # baseline + B3 tool-wear ramp with forecast scorecard
make views           # operator / supervisor / manager views of the same twin
python -m loom.run configs/ramp_b3_dark.yaml --hours 2   # B3 dark: what step 3 must solve
```

## Configs

| file | what |
|---|---|
| `plant_demo.yaml` | base plant: 12 stations / 3 zones, deterministic, balanced to takt |
| `slow_b3.yaml` | B3 at 80 s from t=0 → blocking upstream, starvation downstream |
| `healthy.yaml` | 5 % cycle noise, 2 model variants, no faults (false-alarm baseline) |
| `ramp_b3.yaml` | B3 tool wear 56→80 s over 20 min, starting at 30 min |
| `ramp_b3_dark.yaml` | same, but B3 has no sensors and B2 only reports cycle timestamps |
