# Loom: a digital twin for the assembly line you actually have

*Accenture Innovation Challenge 2026 · Round 2 · Track 4 DigitalTwin.ai · Team SRsync (Rujula Ganesh Rahate, Shivansh Gupta, IIT Bombay) · 29 August 2026*

Most vehicle assembly lines are partly blind — legacy stations on manual checklists next to fully
instrumented cells — and every twin on the market assumes they are not. Loom is a live twin that says
what it measured, what it inferred and what it forecast; warns minutes before a bottleneck forms even
when the failing station has no sensors; holds the exact vehicles a silent drift put at risk; and keeps
a public score of its own accuracy.

> The published, designed version of this proposal is the artifact page; this file is the same content
> in plain text for the repository. Numbers are from `docs/benchmark.md` (5 seeds per scenario, all
> against ground truth the twin never saw).

## Summary

A software product, deployed read-only beside an existing line, giving four roles — floor supervisor,
quality engineer, maintenance, plant manager — one consistent picture of the line and of what is about
to go wrong, from whatever sensors the line already has. It ships with its own scorekeeper, and an AI
layer that writes the briefings, proposes what to try and tunes the system through a gate it cannot
bypass.

| | |
|---|---|
| 6–11 min | warning before a wearing station blocks the line, fully instrumented |
| 6.1 min | same warning with the failing station dark — reconstructed from its neighbours |
| 11 min | hold before end-of-line inspection sees the first weak weld; 80 % precision, 99 % recall, 0 escaped |
| 0.2 / 8 h | false bottleneck alerts on a healthy line over 40 simulated hours |
| 0 writes | to the plant: read-only, proven against a third-party PLC simulator |

## The problem

An idle automotive line costs $2.3 M/hour (Siemens, *True Cost of Downtime 2024*); restarts average
81 minutes; unplanned downtime drains $1.4 T/yr from the Fortune Global 500. Automotive has tried the
twin: >70 % piloting or deploying — yet 64 % of twin projects never leave pilot, Gartner finds one in
three deployed, and 58 % of delays trace to OT/IT integration. The brief names the reason: uneven
sensor coverage. A twin that needs every station to report is a twin for a plant nobody has.

Three consequences: a local slip has line-wide effects that surface stations away and minutes later;
defects are introduced early and surface late, with every vehicle in between carrying them; and the
person deciding — hold or ship, floater or wait — is working blind under time pressure.

## What exists, and the gap

| segment | examples | strength | why it does not fit |
|---|---|---|---|
| Offline simulation | Siemens Plant Simulation, Simul8, AnyLogic, FlexSim, Simio | what-if design | expert-built, not live |
| Real-time OEE | Vorne, MachineMetrics, Fabrico | live takt vs actual | sensor at every point; detect, not predict |
| Manual capture | Tulip | operator taps | no flow model |
| Industrial metaverse (2026) | Siemens Digital Twin Composer / NVIDIA Omniverse, Industrial Copilot | physics-accurate, agents | fully modelled, fully instrumented, GPU on-prem |
| Academic detection | active-period, turning-point… | rigorous on complete logs | prediction and incomplete data are the open problems |

**Gap:** nothing is built for a line that is partly blind. Loom sits between the tiers: live,
configured rather than modelled, honest about coverage, self-scoring.

## How Loom works

Four layers — plant → sensors (per-station profile) → twin → views — with the evaluator seeing both
plant and twin. The wall between plant and twin is architectural: the twin can only use what the
sensor layer passes.

- **Flow.** Timelines reconstructed from serial-line rules; every value tagged measured / inferred /
  simulated. Dark stations: when the next station measurably starts a vehicle after sitting idle, that
  instant is when the dark station released it — exact samples precisely when the dark station is the
  bottleneck. Trend fit with standard-error and persistence guards + forward buffer simulation → "B2
  blocks in ~7 min, confidence 70 %, 100 % inferred". Naïve trend test: 49 false alarms/shift; with
  guards: 0.2 per 8 h.
- **Quality.** EWMA/CUSUM on process parameters; a drift is a warning until a reading is out of spec,
  which opens a hold back-filled from the onset. Contribution analysis (singles and pairs, lift +
  Fisher exact) ranks hypotheses with evidence. Holds split sure / uncertain / already exited.
- **Beyond serial.** Parallel stations, rework loops (a quality problem becoming a flow problem — the
  inspection station becomes the bottleneck and the forecaster names it), shift calendars with breaks
  and crew multipliers.
- **Integration.** Read-only tap (OPC UA, Modbus, historian, MES, operator app). Ladder: shadow →
  advisory → reversible automatic. Proven against Factory I/O over a real Modbus socket: 0 writes.
- **Onboarding is a file.** ISA-95-shaped YAML with libraries and `extends:`; a 30-station second
  plant runs unchanged; an AI assistant drafts the file from one sentence.

## Where AI belongs

Rule: statistics and simulation produce every number; the LLM turns numbers into decisions, proposes
hypotheses for the simulator to test, and drives the improvement loop through a gate it cannot bypass.

| use | mechanism | guard |
|---|---|---|
| persona briefings | one evidence pack, three audiences | every number must be in the pack; pack content-hashed before the model sees it; grounding check stored per report |
| improvement suggestions | LLM proposes from a menu; what-if engine simulates; LLM explains | hypothesis generator, not judge (floater at B3 → 56 veh/h, 0 blocking vs 50 and 10 min) |
| self-improvement | propose → backtest → gate | two lead-raising proposals refused for breaking the false-alarm budget; one accepted |
| onboarding | sentence → plant file | loader validates |
| predicting, scoring, deciding a hold, changing a live threshold | **never** | |

## Evidence

| claim | result (5 seeds) |
|---|---|
| warns before the line blocks | 5/5, 7.0 min lead, ETA error 0.6 min |
| …failing station dark | 5/5, 6.1 min, inferred cycle error 0.3 s |
| …PLC link silent mid-fault | 5/5, 6.8 min |
| moving constraint | 10/10, 0 false alarms |
| another plant, unchanged code | 5/5, 10.6 min, 0 false alarms |
| momentary bottleneck from partial data | 97–100 % agreement |
| healthy line, 40 h | 0.2 alerts / 8 h, 0 holds |
| silent drift contained | hold 11 min before first catch, 80 % precision, 99 % recall, 0 escaped |
| two-condition cause | 5/5 pair ranked first |
| calibration | 0.9–1.0 stated → 100 % hit; 0.5–0.7 → 40 % |
| third-party equipment | 0 writes, dark station reconstructed, wear forecast |

Plus: live 3D control room with fault injection and a YAML line editor; persistence of every event the
twin received, beliefs per minute, every human action, every AI report with the evidence hash; stored
runs replay to the same beliefs.

## Users

Operator (now, my station) · supervisor (this shift) · quality engineer (today) · maintenance (this
week) · plant manager (month; trust ledger, coverage, next sensor) · leadership (quarter; value with
assumptions printed).

## Business case

Runs live in the Exec view from measured lead, hold sizes and escapes plus printed assumptions.

| line | formula | illustrative |
|---|---|---|
| bottlenecks avoided | lead × share acted on × events/wk × weeks × $/min | 6.3 × 50 % × 3 × 48 × $8,000 = $3.6 M |
| targeted holds | (blanket − targeted) × $/vehicle × events | 13 × $250 × 12 = $39 k |
| escapes prevented | defects caught upstream × field cost × events | 19 × $5,000 × 12 = $1.1 M |
| cost | licence + retrofit | $60 k/yr + ~$500/station |

$8,000/min is about a fifth of the plant-wide Siemens figure; at a tenth of the bottleneck line alone,
payback is inside a quarter. The claim is not the number; it is that after two weeks of shadow the
plant has its own number.

## Roadmap

1. Pilot line, shadow mode (weeks 0–4): read-only tap, alerts hidden, evaluator scores → the plant's own trust ledger.
2. Advisory (weeks 4–10): alerts and holds with evidence; confirm/dismiss recalibrates; first VOI retrofits.
3. Reversible automation (weeks 10–16): flag for inspection, open a hold; physical actions stay human.
4. Second line, second plant (Q2): a new line is a plant file.
5. Product (Q3+): historian-backed store, SSO audit, IATF 16949 retention, Claude on-prem/VPC, fleet view.

## Risks

Circularity of a simulated plant → architectural wall, healthy-line floor, third-party run, shadow
mode on real data. Alert fatigue → published budget, guards, grouping, feedback, calibration. Wrong
inference at dark stations → intervals, provenance, abstention. No-upstream-signal defects → stated;
VOI says what to measure next. OT security → read-only, on-prem, audited. Ungrounded AI → hashed
evidence, grounding check, no LLM in the prediction path.

## What we do not claim

Rework breaks FIFO (inference exact only between rework points); adjacent finish-only stations are
inseparable; the demo line sits at the false-alarm budget; the Factory I/O run used a stand-in
controller; process parameters are proven on the simulated plant only.

## The ask

One line, one shadow period, four weeks. We bring the tap, the twin and the scorekeeper; the plant
brings the sensors it already has.

Sources: `docs/research.md`.
