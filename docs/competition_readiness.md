# Competition readiness — where Loom stands, judged the way a panel judges

Round 2 asks for three things: a detailed business proposal, a working prototype, and a pitch. A
panel of Accenture practitioners, VCs and academics will score, in practice, five questions. This
document answers each one honestly: what we have, the evidence, and what is still missing.

## 1. Did we understand the problem statement?

**What the brief actually says** (Track 4, Round 2): a digital twin for a vehicle assembly line that
shows where bottlenecks form and predicts defects before they happen — on a line that is *a
patchwork of legacy and modern equipment with uneven sensor coverage*. Seven real-world complexities
and seven solutioning areas are listed. Reference parameters: 30–50 stations, majority instrumented,
a meaningful minority on manual checks, rare maintenance windows.

**How Loom reads it.** The sentence that matters is "uneven sensor coverage". Most twins assume they
can see the line; the brief says you cannot. So the architecture puts a wall between the plant and
the twin (`sensors.py` is the only path), and every number the twin holds is tagged measured /
inferred / simulated. Everything else — forecasting, containment, the AI layer — is built on top of
that stance.

**Evidence, item by item** (`solution_design.md` §1–§3 maps all seven complexities, all seven
solutioning areas, and eleven the brief does not list, each to a mechanism and a measured result).

| brief item | built | measured |
|---|---|---|
| uneven sensor coverage | sensor profiles, dark-station reconstruction R1–R5, sensor-health, VOI ranking | dark-B3 ramp caught 5/5 seeds, 6.1 min lead, 0.3 s inferred-cycle error |
| multi-causal, intermittent root causes | contribution analysis, singles + pairs, Fisher exact | true pair ranked first 5/5 |
| no live-system changes, rare maintenance windows | read-only by construction; Factory I/O run over Modbus with zero writes; retrofit = config diff | integration test through a real socket |
| early defect surfaces late | build-record trace, drift-onset back-fill, targeted hold | hold 11 min before first end-of-line catch, 80 % precision, 99 % recall, 0 escaped |
| different stakeholder views | operator, supervisor, quality, maintenance, manager, leadership | six live tabs on one twin |
| extend to other lines/plants | ISA-95 YAML, libraries, `extends`; plant B (30 stations) unchanged code | 5/5 caught, 10.6 min lead, 0 false alarms |
| validate predictions over time | evaluator ships in product; trust ledger; calibration; benchmark | 0.2 false alarms / 8 h; stated 0.9–1.0 confidence → 100 % hit rate |
| reference parameters (30–50 stations, mixed maturity) | `plant_b.yaml`: 30 stations, 4 zones, 3 variants, 9 partial/dark | — |

**Beyond the brief, now built:** parallel stations (`capacity`), rework loops that re-enter
inspection out of order, shift calendars with breaks and crew-specific cycle multipliers, and a
leadership ROI view with every assumption printed. The rework scenario shows something a judge from
automotive will recognise: a quality problem becoming a flow problem — the inspection station turns
into the bottleneck because it is now processing fails twice — and the forecaster names it.

## 2. Did we study the market and find the gap?

Sources in `research.md`. The numbers that frame the proposal:

- **The cost.** Siemens *True Cost of Downtime 2024*: an idle automotive line costs **$2.3 M per
  hour** (~$38 k/min), more than double 2019; the average restart takes 81 min. Unplanned downtime
  drains **$1.4 T/yr** from the world's 500 largest companies (11 % of revenue).
- **The failure mode.** **64 % of digital-twin projects never leave pilot** (integration complexity,
  data quality, unclear ROI); Gartner 2024: only ~1 in 3 twin initiatives deploy beyond pilot; a
  2025 IoT Analytics survey traces **58 % of twin delays to OT/IT integration**. Adoption is highest
  in automotive (>70 % piloting or deploying) — so the buyer has already tried something.
- **The incumbents.** Offline DES (Siemens Plant Simulation, Simul8, AnyLogic, FlexSim, Simio):
  expert-built models, not live. Real-time OEE (Vorne, MachineMetrics, Fabrico): live but need a
  sensor at every point — Vorne's own reviewers: "wire sensors to the bottleneck; moving them is a
  hassle if the constraint shifts". Tulip: manual-station capture, no flow model. **Top of market,
  2026:** Siemens Digital Twin Composer on NVIDIA Omniverse (mid-2026), physics-accurate factory
  twins with AI agents — PepsiCo reports +20 % throughput. Siemens Industrial Copilot for Operations
  runs on-prem on Blackwell GPUs.
- **The gap.** Nobody in that list is built for the line the brief describes. The high end assumes
  a fully modelled, fully instrumented plant and a capital budget; the OEE tier assumes a sensor
  wherever you want an answer; the academic bottleneck literature (2023 systematic review) lists
  *prediction* and *incomplete data* as open problems. Loom's position: live, config-built, honest
  about what it cannot see, and self-scoring — for the brownfield majority, not the greenfield
  showcase.

## 3. Did we solve it effectively?

Three kinds of evidence, in ascending order of how hard they are to argue with. All against ground
truth the twin never saw, over seeds, with the failures stated.

**(a) Against the alternatives** (`docs/baselines.md`). Absolute numbers invite "compared to what?".
Every comparator below sees the same sensor-filtered event stream the twin saw, so the difference is
the mechanism, not the data.

| method | alarms / 8 h on a healthy line | verdict |
|---|---|---|
| threshold alarm (cycle > takt x 1.05) | 142 | an alarm every three minutes is an alarm nobody reads |
| active-period detection (Roser), same persistence rule | 48 | fine as a dashboard signal, unusable as an alarm |
| **Loom** | **0.2** | inside the published budget |

Read lead time only next to that column. A threshold alarm gets *more* lead than Loom on an
instrumented station (13.5 min vs 7.0) — because it fires 142 times a shift. On the dark station it
never warns at all, because a threshold rule has nothing to threshold. And for containment: 63
defective vehicles escape with end-of-line inspection alone; a blanket hold stops 90 vehicles
starting at minute 54; Loom holds 76 starting at minute 42.

**(b) What each mechanism buys** (`docs/ablation.md`). One knob off per row, everything else fixed:

| mechanism removed | false alarms / 8 h | dark ramp caught | 2-condition cause found |
|---|---|---|---|
| **full system** | **0.2** | **5/5** | **5/5** |
| no persistence rule | 2.0 | 5/5 | 5/5 |
| no standard-error test | 0.4 | 5/5 | 5/5 |
| no inferred samples | 0.2 | **0/5** | 5/5 |
| no pair search | 0.2 | 5/5 | **0/5** |
| no drift back-fill | 0.2 | 5/5 | 5/5 |

The last row is a finding against ourselves: the onset back-fill recovers zero vehicles on the demo
scenario, because B2 reports a reading for every vehicle and hold membership is decided per reading.
Either a scenario exercises it or the claim leaves the proposal (spec item E6a).

**(c) Absolute performance** (`benchmark.md`, `solution_design.md` §6b):

| claim | result (20 seeds) |
|---|---|
| warns before the line blocks | 7.9 min lead fully instrumented; 6.0 with the station dark; 7.7 with a PLC link silent; 11.8 on the 30-station plant |
| stays quiet when nothing is wrong | 0.30 alerts / 8 h; 0 holds on 160 h healthy |
| momentary bottleneck from partial data | 96–99 % agreement with the plant's own active periods during faults |
| catches a silent drift and contains it | hold 12.8 min before the first inspection catch; 81 % precision, 99 % recall; the blanket hold would be 90 vehicles and start later |
| finds a two-condition cause | 17/20 |
| confidence means something | 0.9–1.0 stated → 97 % realised; 0.7–0.9 → 75 %; 0.5–0.7 → 44 % |
| degrades rather than breaks | warning survives to 30 % of stations dark; reconstruction error flat at 0.2 s to 50 % |
| survives real sensor semantics | Factory I/O adapter through a real Modbus socket at 50 Hz; dark and exit-only stations handled |

**What the 20-seed re-run found, and what it cost to fix.** The previous benchmark was 5 seeds and
reported 0 false alarms on `shifting` and `plant_b`. At 20 seeds those were 2.4 and 1.3 per 8 h,
above budget. Two causes, one a real bug and one a bad measurement:

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

(The full account is in the proposal under "What we do not claim".) This is the argument for having
a benchmark at all: 5 seeds said the system was perfect, 20 seeds found a real alert-flooding bug.

Where we are weaker, said plainly: the false-alarm budget holds on a healthy line and not on the
multi-fault scenarios (above); a defect with no upstream signal is only learnable from
inspection fails, so its hold trails the first catch (the multi-cause scenario); two adjacent
checklist stations cannot be told apart, so the twin abstains; the demo line sits at the
false-alarm budget rather than under it; rework breaks FIFO, so inference at a dark station
downstream of a rework loop is exact only between rework points.

## 4. Did we use AI well — and presentably?

The rule, and every module enforces it: **statistics and simulation produce every number; the LLM
turns numbers into decisions, proposes hypotheses for the simulator to test, and drives the
improvement loop through a gate it cannot bypass** (`ai_layer.md`).

| use | what a judge sees |
|---|---|
| persona reports | a supervisor handover, a quality memo, a manager summary — same evidence pack, three audiences; every number traceable; provenance words used |
| what-if | "floater at B3 → 56 veh/h, 0 blocking; rebalance → 54; buffer → 50; recommended: floater" — the LLM proposed from a menu, the simulator measured |
| gated improvement loop | two proposals that raised lead time were *refused* because they broke the false-alarm budget; one accepted. The gate decides |
| onboarding | "18 stations, takt 72 s, 4 manual, 2 dark, paint buffer 10" → a valid plant file with stated assumptions |
| cost telemetry | tokens, latency, dollars per call on the manager view |
| grounding and audit | every report row stores the SHA-256 of the evidence pack it was written from, the prompt hash, provider/model, cost, and a mechanical check that every number in the text occurs in the pack; every human action (injection, confirm/dismiss with a note, config load) is in an audit table with actor and line time (`data_and_audit.md`) |

This matches where the market is going (2026: copilots → agents "entering the decision loop",
Deloitte: agentic adoption 6 % → 24 %) while answering the trust question the same sources raise:
guardrails, traceability, permission boundaries are in the mechanism, not the slide.

**Now measured, not asserted** (`docs/ai_eval.md`): groundedness 100 % (15/15 reports), abstention
100 %, persona fit 100 %, and a red-team set of four reports carrying invented throughput, lead
time, money and precision figures — **4/4 caught, 0 clean reports wrongly flagged**. On the
template path the first three are a statement about the renderers, which are grounded by
construction and serve as the control arm; the red-team column is what shows the check has teeth
regardless of who wrote the text. Building the suite found a real defect: the renderers raised on a
sparse pack, i.e. failed exactly when there was nothing to report.

All four AI features are now in the control room's **AI** tab — three of them previously had no
HTTP route at all, so a judge clicking through the prototype found no AI.

Still to do here: run the same calls through `claude-opus-5` once the key is wired; `aieval`
rescores model output with no change to the file, and the template path stays as the offline
fallback and the control arm.

## 5. Is it presentable?

- **Live control room** (`loom.server`): real-time plant and twin, 3D line with the plant's truth in
  front and the twin's belief behind, clickable stations with cycle history and parameter charts
  against spec limits, fault injection, YAML line editor, six persona tabs, scorecard, recording to
  a shareable replay page.
- **Factory I/O clip**: third-party equipment, Loom read-only, dark station reconstructed.
- **Demo script**: five scenes in `solution_design.md` §5.

## 6. What is missing for submission

| item | status | owner |
|---|---|---|
| Business proposal document (client-facing) | `solution_design.md` is the skeleton; needs rewriting as a proposal with the market numbers above, the ROI model from the leadership view, phased roadmap, risks | next |
| Pitch deck + video | not started; storyboard = the five demo scenes + Factory I/O clip + AI side-by-side | next |
| Claude key wired; LLM side-by-side | template path only | you |
| Factory I/O run on the real scene | adapter tested against the fake; `plc_stub` unverified on real conveyor geometry | you |
| Benchmark re-run after topology changes | **done — 20 seeds; found two claims that did not survive** | — |
| Baselines, ablation, coverage curve | **done** — `baselines.md`, `ablation.md`, `coverage.md` | — |
| Prose grounding check on our own numbers | **done** — `python -m loom.numbers` | — |
| False-alarm rate on multi-fault scenarios | **done** — 0.1–0.2 per 8 h, inside budget | — |

## 7. The pitch in one paragraph

Most assembly lines are partly blind, and every twin on the market assumes they are not. Loom is a
live digital twin built for the line you actually have: it says what it measured, what it inferred
and what it forecast, warns minutes before a bottleneck forms even when the failing station has no
sensors, holds the exact vehicles a silent drift put at risk before end-of-line inspection sees the
first one, and keeps a public score of its own accuracy. AI writes the briefings, proposes what to
try, and tunes the system — through a gate it cannot bypass. Onboarding a new plant is a YAML file;
integrating is a read-only tap. We proved it against ground truth over dozens of simulated shifts
and against a third-party PLC simulator we did not write.
