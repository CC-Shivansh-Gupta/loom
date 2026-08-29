# Loom — assembly-line digital twin prototype

Four layers, one event schema, a hard wall between truth and belief.

| layer | module | sees |
|---|---|---|
| 1 Plant (ground truth) | `loom/plant.py` | everything |
| 2 Sensors | `loom/sensors.py` | plant events → forwards what each station's sensor profile allows |
| 3 Loom (twin) | `loom/twin.py` (flow), `loom/forecast.py` (bottlenecks), `loom/quality.py` (drift, defects, containment) | only what sensors forward; tags every value ● measured / ◐ inferred / ○ simulated |
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
| `ramp_b3_dark.yaml` | same, but B3 has no sensors and B2 only reports cycle timestamps — the twin reconstructs B3 from its neighbours |
| `sensor_fault_b2.yaml` | same ramp; B2's PLC link goes silent for 25 min mid-ramp — the twin notices and bridges |
| `plant_b.yaml` | a different plant: 30 stations, 4 zones, 3 variants, mixed sensor maturity; `--voi` ranks which station to instrument next |
| `weld_drift_b2.yaml` | silent drift: B2 weld current sags out of spec, no cycle-time symptom; defect surfaces at F5 — CUSUM catches it at source, targeted hold |
| `multi_cause.yaml` | intermittent defect needing low torque at B4 **and** high humidity at P1; contribution analysis finds the pair |
| `shifting.yaml` | B3 wears, is repaired, then F3 wears — the constraint moves; both forecast, momentary bottleneck tracked |

Views: `--view operator:<station>`, `supervisor`, `quality`, `maintenance`, `manager`.

Benchmark over many seeds (every number against ground truth the twin never saw):

```
python -m loom.bench --seeds 5 --out docs/benchmark.md
```

## Control room (live)

```
python -m loom.server --config healthy.yaml     # then open http://localhost:8000
```

The plant and the twin run live in the server at 10–300× real time; the browser streams frames over
a WebSocket and renders the line in 3D (Three.js). Nothing is precomputed.

- **Scene** — plant truth in the front lane, Loom's belief behind it (solid = measured, dashed =
  inferred, floating outlines = vehicles it cannot place; orange cone = forecast, purple ring =
  momentary bottleneck). Click any station box or card for its panel: configuration, plant vs Loom
  state, cycle history (plant solid, Loom dashed), every process parameter against its spec limits
  with the CUSUM state, the forecast and any hold it is in.
- **Inject** — wear a station (cycle + ramp), repair it, switch its instrumentation between
  `plc_full` / `cycle_only` / `checklist` / `dark`, silence its sensor for N minutes, drift a
  parameter out of spec. The plant changes at once; Loom only sees what the sensors pass.
- **Line** — edit the YAML (stations, zones, buffers, sensors, variants, scheduled faults) and apply;
  the plant and twin rebuild from zero. Any file in `configs/` loads from the picker.
- **Floor / Quality / Maint. / Mgr / Log** — the persona views and the event log, live.
- The scorecard strip fills in as outcomes happen: warning lead once the line actually blocks, false
  alarms, holds with how many held vehicles are truly defective.
- **Record** — captures the live run (frames every 10 s, persona views every minute, every alert,
  hold and injection) and, on stop, writes `web/recordings/<name>.html`: a self-contained replay page
  you can send to anyone, plus the raw JSON.

## Factory I/O (real third-party equipment, read-only)

```
python -m loom.plc_stub configs/factoryio_map.yaml --wear S2:600:30   # the "PLC", drives the scene
python -m loom.server  --factoryio configs/factoryio_map.yaml          # Loom, reads Modbus inputs only
```

Loom polls Factory I/O's Modbus TCP/IP Server driver, turns photo-eye edges into its event schema
and runs the same twin and control room. No Windows box handy: `python -m loom.fakefactory
configs/factoryio_map.yaml --speed 30` is a Modbus server (port 5020) driven by Loom's own plant —
then `--time-scale 30 --modbus-port 5020` on the server. Setup, scene contract and limits:
`docs/factoryio.md`.

## Control room (3D replay, static)

```
python -m loom.export configs/ramp_b3_dark.yaml --hours 2 --out web/data/ramp_b3_dark.json   # one per scenario
python web/build.py                                                                          # -> web/dist/index.html
```

Single self-contained page (Three.js from cdnjs, scenario data gzipped inline). Plant truth in the
front lane, Loom's belief behind it: solid = measured, dashed = inferred, floating outlines = vehicles
the twin cannot place; orange cone = bottleneck forecast, purple ring = momentary bottleneck. Deep link
with `#s=<scenario>&t=<seconds>`.

## AI layer

```
python -m loom.ai report  configs/weld_drift_b2.yaml --persona quality     # grounded persona report
python -m loom.ai whatif  configs/ramp_b3.yaml --hours 0.75                  # simulate mitigations, rank, explain
python -m loom.ai improve --iterations 3                                     # propose → backtest → gate
python -m loom.ai onboard "18 stations, takt 72 s, 4 manual, 2 dark, paint buffer 10"
```

Runs on deterministic templates by default; with `pip install anthropic` and credentials (or
`LOOM_LLM=claude`) the same calls go to `claude-opus-5`. See `docs/ai_layer.md`.

Sensor profiles carry noise: timestamp jitter, clock offset, dropouts and reporting latency
(`loom/sensors.py`). Every twin value is tagged ● measured / ◐ inferred / ○ simulated, and inferred
timestamps are further marked exact or bound so no cycle time is ever estimated from a bound.
