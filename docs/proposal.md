# Loom: a digital twin for the assembly line you actually have

*Accenture Innovation Challenge 2026 · Round 2 · Track 4 DigitalTwin.ai · Team SRsync (Rujula Ganesh Rahate, Shivansh Gupta, IIT Bombay) · 29 August 2026*

Most vehicle assembly lines are partly blind — legacy stations on manual checklists next to fully
instrumented cells — and every twin on the market assumes they are not. Loom is a live twin that says
what it measured, what it inferred and what it forecast; warns minutes before a bottleneck forms even
when the failing station has no sensors; holds the exact vehicles a silent drift put at risk; and keeps
a public score of its own accuracy.

> The published, designed version of this proposal is the artifact page; this file is the same content
> in plain text for the repository. Numbers are from `docs/benchmark.md` (20 seeds per scenario),
> `docs/baselines.md`, `docs/ablation.md` and `docs/coverage.md` — all against ground truth the
> twin never saw. `python -m loom.numbers docs/proposal.md` checks that every figure below appears
> in one of them.

## Summary

A software product, deployed read-only beside an existing line, giving four roles — floor supervisor,
quality engineer, maintenance, plant manager — one consistent picture of the line and of what is about
to go wrong, from whatever sensors the line already has. It ships with its own scorekeeper, and an AI
layer that writes the briefings, proposes what to try and tunes the system through a gate it cannot
bypass.

| | |
|---|---|
| 7.9 min | warning before a wearing station blocks the line, fully instrumented |
| 6.0 min | same warning with the failing station dark — reconstructed from its neighbours |
| 12.8 min | hold before end-of-line inspection sees the first weak weld; 81 % precision, 99 % recall |
| 0.30 / 8 h | false bottleneck alerts on a healthy line over 160 simulated hours |
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

Also on the board, and closer than the tiers above: **Augury Process Health (ex-Seebo)** does AI
root-cause on multi-causal quality problems — but for continuous and batch process lines with dense
historian coverage, with no flow model of a discrete serial line and no answer for a station that
reports nothing. **Sight Machine, Braincube, Cognite, PTC ThingWorx, Azure Digital Twins, AWS IoT
TwinMaker** are substrates: you bring the model, the analytics and the integrator. **Siemens
Opcenter, Rockwell FactoryTalk/Plex** are systems of record, and changing them is exactly the
live-production risk the brief warns about.

**Gap:** two axes decide this purchase — how much sensor coverage a tool requires, and whether it
predicts or only reports. Everything that predicts assumes it can see the line; everything that
tolerates a partly blind line only reports what already happened. That quadrant is empty, and it is
where the brief's plant lives.

**What it costs to be in the other quadrants.** Real-time OEE is priced per monitored point, and
the sensor is the cost — which is a problem when the constraint moves. Offline DES is seat licences
plus a simulation engineer to keep the model true. An industrial-metaverse twin is a capital
project. Loom is **$60k/line/yr plus ~$500 a station, and only for the stations the twin asks for**.

**Who signs.** The plant manager or head of manufacturing engineering, from **OT operating budget
rather than capital** — which is precisely why a read-only tap clears where a retrofit waits for a
window. Also on the paper: IT/OT security, where read-only, on-prem and no PLC writes is the whole
answer; and in Europe a works council, because anything that times operators is a consultation
item. Loom times *stations*, and we say so first.

**Why now.** Downtime per automotive hour has roughly doubled since 2019. Siemens on Omniverse has
raised both the ceiling and the price of entry, which widens the brownfield middle rather than
narrowing it. And 64 % of twin projects are still in pilot: the market has proved it wants a twin
and proved it cannot afford the greenfield one.

**Why not build it.** A Tier-1 could build the simulator in a quarter. What they will not build is
the evaluator, the published false-alarm budget and the calibration discipline — those only pay off
if you are willing to publish your own error rate, and an internal tool is never asked to.

## How Loom works

Four layers — plant → sensors (per-station profile) → twin → views — with the evaluator seeing both
plant and twin. The wall between plant and twin is architectural: the twin can only use what the
sensor layer passes.

- **Flow.** Timelines reconstructed from serial-line rules; every value tagged measured / inferred /
  simulated. Dark stations: when the next station measurably starts a vehicle after sitting idle, that
  instant is when the dark station released it — exact samples precisely when the dark station is the
  bottleneck. Trend fit with standard-error and persistence guards + forward buffer simulation → "B2
  blocks in ~7 min, confidence 70 %, 100 % inferred". Naïve trend test: 49 false alarms/shift; with
  guards: 0.30 per 8 h.
- **Quality.** EWMA/CUSUM on process parameters; a drift is a warning until a reading is out of spec,
  which opens a hold classifying each vehicle on its own reading from the station. Contribution analysis (singles and pairs, lift +
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

**Against the alternatives** (`docs/baselines.md`) — each comparator sees the same sensor-filtered
event stream the twin saw, so the difference is the mechanism, not the data.

| method | alarms / 8 h, healthy line | lead on an instrumented ramp | lead with the station dark | escapes on the drift scenario |
|---|---|---|---|---|
| no twin | 0 | 0 — you find out when it blocks | 0 | 63 |
| threshold alarm | 142 | 13.5 min | never warns | — |
| active-period detection | 48 | 6.1 min | 6.5 min | — |
| **Loom** | **0.2** | **7.0 min** | **5.9 min** | **0** |

Lead time is only meaningful next to the alarm rate that bought it: a trigger-happy rule always wins
on lead and is always ignored by the floor within a week.

**What each mechanism buys** (`docs/ablation.md`) — without inferred samples the dark ramp is missed
5/5; without the pair search the two-condition cause is found 0/5; without the persistence rule
false alarms go from 0.2 to 2.0 per 8 h.

**Absolute performance** (20 seeds per scenario, `docs/benchmark.md`)

| claim | result |
|---|---|
| warns before the line blocks | 20/20, 7.9 min lead, ETA error 1.4 min |
| …failing station dark | 20/20, 6.0 min, inferred cycle error 0.3 s |
| …PLC link silent mid-fault | 20/20, 7.7 min |
| moving constraint | 37/40 caught, 0.2 false alarms / 8 h |
| another plant, unchanged code | 20/20, 11.8 min, 0.1 false alarms / 8 h |
| momentary bottleneck from partial data | 96–99 % agreement |
| healthy line, 160 h | 0.30 alerts / 8 h, 0 holds |
| silent drift contained | hold 12.8 min before first catch, 81 % precision, 99 % recall, 1 escaped of 20 runs |
| two-condition cause | 17/20 pair ranked first |
| calibration | 0.9–1.0 stated → 97 % hit; 0.7–0.9 → 75 %; 0.5–0.7 → 44 % |
| third-party equipment | 0 writes, dark station reconstructed, wear forecast |

**Graceful degradation** (`docs/coverage.md`) — darkening a growing share of stations, the failing
one first: the warning survives at 10, 20 and 30 % dark for about a minute of lead. Reconstruction
error holds at 0.2 s even at 50 %, where the forecast has already stopped firing — the twin still
knows what the dark stations are doing, it runs out of the measured samples the trend fit needs.
The fix is one more timestamp source, which is a purchase order.

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

**Our false-alarm budget now holds on faulting lines too, and how we got there matters.** At 20
seeds `shifting` was producing 2.4 false alarms per 8 h and `plant_b` 1.3, against a budget of 0.2.
Two causes, one a real bug and one a bad measurement:

- **The bug.** Alerts were grouped under a downstream root only while that root held an *active
  alert*. But a root alert clears the moment its station stops testing over takt, and the queue it
  built does not drain at the same instant — so every station still physically blocked by it
  raised an alert of its own naming itself as the problem. On `shifting` that produced an alert on
  B4 **0.2 minutes after** the F3 alert blocking it cleared. Grouping now keys on the physical
  test: a downstream station whose believed cycle sits `min_over_z` standard errors above takt is a
  constraint whether or not it currently holds an alert. Same statistic that raises an alert, so no
  new threshold. `shifting` fell from 2.4 to 0.3 per 8 h with lead time and catch rate unchanged.
- **The measurement.** The evaluator counted every alert after the first one matched to a fault as
  a false alarm — including a correct re-raise on a station whose injected fault was *still
  running*. On `plant_b` it scored an alert on T04 at 93.8 s as false while T04's fault was holding
  it at 95 s against a 75 s takt. A false alarm is now an alert no fault explains, tested against
  the plant's true cycle at that instant. This cannot excuse an alert on a healthy line, where no
  station is ever over takt — and the healthy-line floor is **unchanged at 0.30 per 8 h**, which is
  how you can tell the definition change did not launder the headline number.

Every scenario now sits at 0.1–0.2 false alarms per 8 h. `shifting` still misses 3 of 40 faults.

The drift onset back-fill recovers no vehicles on our demo scenario — the cause station reports a
reading for every vehicle, so the onset window never binds; we found this by building the ablation
table and we have not yet built the sparse-reading scenario where it would matter. Rework breaks FIFO (inference exact only between rework points); adjacent finish-only stations are
inseparable; the demo line sits at the false-alarm budget; the Factory I/O run used a stand-in
controller; process parameters are proven on the simulated plant only.

## The ask

One line, one shadow period, four weeks. We bring the tap, the twin and the scorekeeper; the plant
brings the sensors it already has.

Sources: `docs/research.md`.
