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
| `parallel.yaml` | F2 is a two-operator station (`capacity: 2`, 100 s each) — not a bottleneck, and the twin knows |
| `parallel_rework.yaml` | + F5 sends 70 % of fails to a 6-min rework bay; re-entry overloads F5 and the forecaster names it |
| `shifts.yaml` | two breaks, a slower night crew, a real B3 ramp at 5 h — 0 false alarms |

Views: `--view operator:<station>`, `supervisor`, `quality`, `maintenance`, `manager`, `leadership` (ROI with stated assumptions; **Exec** tab in the control room).


Benchmark over many seeds (every number against ground truth the twin never saw):

```
python -m loom.bench     --seeds 20 --out docs/benchmark.md    # absolute performance (+ benchmark.json)
python -m loom.baseline  --seeds 10 --out docs/baselines.md    # vs no twin / threshold alarm / detection-only
python -m loom.ablate    --seeds 10 --out docs/ablation.md     # what each mechanism buys
python -m loom.coverage  --seeds 5  --out docs/coverage.md     # lead time vs share of stations dark
python -m loom.trace                --out docs/traces.md       # the single runs the docs quote
python -m loom.numbers docs/proposal.md                        # every figure in the prose is in one of the above
```

`baseline` is the answer to "compared to what?": every comparator sees the same sensor-filtered
event stream the twin saw, so the difference is the mechanism and not the data. A plain threshold
alarm gets more lead time than Loom on an instrumented station — and raises **145 alarms per 8 h**
on a healthy line against Loom's **0.3**, and never warns at all when the failing station is dark.
`ablate` turns one mechanism off at a time; without inferred samples the dark ramp is missed 5/5,
without the pair search the two-condition cause is never found. `coverage` darkens a growing share
of stations, the failing one first: the warning survives to 30 % dark, and reconstruction error
holds at 0.2 s even at 50 % — inference and forecasting fail separately, and it matters which.

`numbers` applies the AI layer's grounding rule to our own writing: a figure in the proposal must
appear in a document a run produced, or be declared in `docs/exempt_numbers.md` with a source. It
runs in the test suite. We are in no position to demand grounding from a language model and not
from ourselves.

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
- **Floor / Quality / Mgr / Exec** — designed persona views, not text dumps: stat tiles, an andon
  grid coloured by state, drift and hold cards, ranked hypotheses, the prediction ledger scored
  against what happened, a coverage map, the retrofit roadmap, and the investment case with every
  input printed and a sensitivity at a tenth of the biggest assumption. All render from
  `/api/pack` — the same evidence pack the AI layer is handed — so a briefing and the screen a
  supervisor is looking at cannot disagree. **Maint.** and **Log** are the remaining text views.
- **AI** — briefings, what-if (the model proposes from a menu, the simulator ranks), the gated
  improvement loop showing its refusals, onboarding from a sentence, and a red-team panel where the
  grounding check catches fabricated numbers on screen.
- **▶ Story** — ten scripted scenes with captions, driving the same public API a person would
  click: healthy line, tool wear, the same fault with the station dark, the silent weld drift, the
  AI layer. It is the video's spine and the failure plan if a live demo misbehaves.
- The scorecard strip fills in as outcomes happen: warning lead once the line actually blocks, false
  alarms, holds with how many held vehicles are truly defective.
- **Store** — everything persists to SQLite (`web/loom.db`): every event the twin received, its
  alerts/holds/drifts, a belief snapshot per minute, an audit trail of every action (loads, resets,
  injections, alert confirm/dismiss with actor and note), and every AI report with the content hash
  of the evidence pack it was written from plus a mechanical grounding check. `Store.replay()`
  rebuilds the twin from stored events and compares it to the snapshots. See `docs/data_and_audit.md`.
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

All four are live in the control room's **AI** tab, not just the CLI: briefings, what-if with the
simulator's ranking, the improvement loop showing the gate refusing proposals, onboarding from a
sentence, and a red-team panel where the grounding check catches fabricated numbers on screen.

```
python -m loom.aieval --out docs/ai_eval.md    # groundedness, abstention, persona fit, red team
```

Runs on deterministic templates by default; with `pip install anthropic` and credentials (or
`LOOM_LLM=claude`) the same calls go to `claude-opus-5` and `aieval` rescores the model's output
unchanged. See `docs/ai_layer.md`.

Sensor profiles carry noise: timestamp jitter, clock offset, dropouts and reporting latency
(`loom/sensors.py`). Every twin value is tagged ● measured / ◐ inferred / ○ simulated, and inferred
timestamps are further marked exact or bound so no cycle time is ever estimated from a bound.
