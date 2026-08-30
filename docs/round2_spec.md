# Round 2 build spec — from "works" to "wins"

Companion to `competition_readiness.md`. That document scores where Loom stands; this one
specifies the work that closes the gap, as buildable items with acceptance criteria.

**Organising principle.** Loom already measures itself against ground truth. What it does not do
is measure itself *against alternatives*, expose its AI, or look like six products for six people.
Every item below serves one of those three.

Priority: **P0** blocks submission · **P1** separates a good entry from a winning one · **P2** product credibility.

| # | item | priority | surface | status |
|---|---|---|---|---|
| E1 | Baseline comparators (no-twin / threshold alarm / detection-only) | P0 | `loom/baseline.py` | **done** → `docs/baselines.md` |
| E2 | Ablation table (what each mechanism buys) | P0 | `loom/ablate.py` | **done** → `docs/ablation.md` |
| E3 | Coverage degradation curve | P1 | `loom/coverage.py` | **done** → `docs/coverage.md` |
| E4 | 20-seed re-run, numbers as single source of truth | P0 | `loom/bench.py`, `loom/numbers.py` | **done** — and it found two claims that did not survive |
| E5 | Restore the forecaster tuning sweep | P1 | `loom/sweep.py` | — |
| E6 | Multi-cause: discriminating sample instead of a bad hold | P1 | `loom/quality.py` | — |
| E6a | Back-fill: a scenario that exercises it, or drop the claim | P1 | `configs/`, `docs/proposal.md` | **open** — claim removed from the proposal for now |
| E7 | False-alarm rate on multi-fault scenarios (1.3–2.4 / 8 h vs a 0.2 budget) | **P0** | `loom/twin.py`, `loom/evaluator.py` | **done** — 0.1–0.2 / 8 h, healthy floor unchanged |
| A1 | Claude wired; live run of all four AI features | P0 | env + `loom/llm.py` | — |
| A2 | AI tab: reports, what-if, improve, onboard in the control room | P0 | `loom/server.py`, `web/app.html` | **done** |
| A3 | Grounding catch — the model caught reaching for a number | P0 | `loom/aieval.py`, AI tab | **done** — red-team fixtures, 4/4 caught |
| A4 | LLM eval suite (groundedness, abstention, persona fit) | P1 | `loom/aieval.py` | **done** → `docs/ai_eval.md` |
| A5 | Model tiering and measured cost per insight | P1 | `loom/llm.py` | — |
| A6 | Operator notes treated as data, not instructions | P2 | `loom/evidence.py` | — |
| U1 | Exec view never renders $0 | P0 | `loom/views.py` | — |
| U2 | Camera fit; default panel shows live alerts | P0 | `web/app.html` | — |
| U3 | Supervisor and exec views as designed UI, not `<pre>` | P1 | `web/app.html`, `loom/server.py` | — |
| U4 | Story mode — scripted demo scenes with captions | P1 | `loom/live.py`, `web/app.html` | — |
| U5 | VOI as a clickable retrofit roadmap | P1 | `web/app.html` | — |
| M1 | Complete the competitive landscape | P0 | `docs/research.md`, `proposal.md` | — |
| M2 | Positioning map, cost wedge, buyer and budget, why-now | P1 | `docs/proposal.md` | — |

---

## E1 · Baseline comparators

**Why.** Every number Loom reports today is absolute. A panel asks "compared to what?". The
comparison must be *fair*: each comparator sees exactly the same sensor-filtered event stream the
twin saw, so the difference is the mechanism, not the data.

**Comparators**

| name | rule | what it stands for |
|---|---|---|
| `no_twin` | you learn when the upstream station actually blocks | the plant today |
| `threshold` | alarm when a station's reported cycle exceeds `takt x k` (k = 1.05), no persistence, no trend | the alarm every PLC/OEE tool already has |
| `detection` | alarm when the active-period method first names the station the momentary bottleneck | the best-validated method in the literature — detection without forecasting |
| `loom` | the forecaster | — |

**Interface.** `baseline.compare(plant, twin, cfg) -> list[MethodScore]` with, per method and per
injected fault: warning time, lead vs the true sustained block, and false alarms over the run.

**Acceptance.** `python -m loom.baseline --seeds 10` prints a table for every flow scenario, and
containment gets the same treatment (end-of-line detection vs blanket hold vs targeted hold).
Every headline claim in the proposal reads "X, against Y for a threshold alarm".

## E2 · Ablation table

**Why.** Proves each mechanism earns its place — the single most persuasive evidence table available.

| ablation | knob | expected effect |
|---|---|---|
| no persistence rule | `twin.RAISE_AFTER = 1` | false alarms explode (~49/shift is the recorded figure) |
| no standard-error test | `Forecaster(min_over_z=0)` | naive over-takt comparison, false alarms rise |
| no inferred samples | `Forecaster(use_inferred=False)` | the dark-station ramp is missed entirely |
| no pair search | `contribution(max_pairs=0)` | the two-condition cause is never found |
| no drift back-fill | `QualityTwin.backfill = False` | hold starts at the first out-of-spec reading; recall falls |

**Acceptance.** `python -m loom.ablate --seeds 10 --out docs/ablation.md` produces a table where
every row is worse than the full system on at least one axis, with no hand-editing.

**Result.** Four of the five mechanisms earn their row: the persistence rule holds false alarms to
0.2/8 h against 2.0 without it; the standard-error test halves them again; without inferred samples
the dark-station ramp is missed **5/5**; without pair search the two-condition cause is found
**0/5**. The fifth — drift onset back-fill — has *no measurable effect*, because on
`weld_drift_b2.yaml` B2 reports a reading for every vehicle, so hold membership is decided per
reading and the onset window never binds. See E6a.

## E3 · Coverage degradation curve

**Why.** The brief's central question, answered as a graph: how gracefully does the twin fail as
the plant gets blinder?

**Method.** Darken k stations of `plant_b.yaml` (deterministic selection, spread across zones) for
k giving 0/10/20/30/40/50 % dark. Measure lead time, catch rate and inferred-cycle MAE per level.

**Acceptance.** `python -m loom.coverage --out docs/coverage.md` emits the table; the deck carries
the chart drawn from it.

## E4 · Numbers as a single source of truth

**Why.** The benchmark is 5 seeds and predates the parallel/rework/shift changes, so every figure
quoted in prose is stale. Worse, they were typed by hand and will drift again.

**Method.** `bench` emits `docs/numbers.json` alongside `benchmark.md`. `loom/numbers.py`
implements `check(markdown_path)` — the same idea as the AI grounding check, turned on our own
prose: every number in the document must appear in `numbers.json`, or be listed as an exempt
constant (prices, station counts, dates). Runs in the test suite.

**Acceptance.** `python -m loom.numbers docs/proposal.md` exits non-zero on an unsupported figure.
No claim in the proposal that a run did not produce.

## E6a · The back-fill claim (opened by the ablation)

The proposal says a drift "opens a hold back-filled from the onset". The ablation shows that on the
demo scenario the back-fill recovers **zero** vehicles: B2 reports a weld-current reading for every
vehicle it builds, so each vehicle is judged on its own reading and the onset window is never the
binding constraint. The mechanism is only load-bearing where parameter readings are *sparse or
sampled* — a station whose quality check is a periodic audit rather than a per-unit measurement.

Two acceptable resolutions, in order of preference:

1. Add a scenario where the cause station samples its parameter (say one vehicle in five) so the
   onset window is what recovers the un-sampled vehicles in between, and report the difference.
2. Failing that, remove back-fill from the proposal's list of mechanisms and describe the hold as
   what it demonstrably is: per-vehicle classification from the station's own readings, opened as
   soon as the drift is detected.

Either way the claim and the evidence must agree before submission.

## E7 · The false-alarm budget on faulting lines (opened by the 20-seed re-run)

The 5-seed benchmark reported **0 false alarms** on `shifting.yaml` and `plant_b.yaml`. At 20 seeds
they are 15 and 10 — **2.4 and 1.3 per 8 h**, against a published budget of 0.2 — and `shifting`
misses 3 of 40 faults. The healthy-line floor is fine at 0.30 per 8 h; the problem is specific to
runs that contain faults.

The likely mechanism, worth checking before tuning anything: when a real bottleneck forms, the
stations downstream of it are starved and the stations upstream are blocked, and both look like
cycle-time anomalies. Alert grouping already collapses *downstream starvation* under the root
alert; `shifting` adds a repair, which releases a backlog and produces a surge that looks like a
second fault. Two candidate fixes:

1. Extend the causal-chain grouping to cover post-repair surges — an alert raised within one buffer
   drain-time of a cleared alert upstream is a consequence, not a cause.
2. Suppress trend alerts while the station's supply interval is itself anomalous, which is already
   measured (`assess(interarrival=...)`) but not used as a guard.

Whichever wins must go through `improve.py`'s gate like any other change: it may not raise the
healthy-line floor or lower mean lead by more than 0.5 min. **This is now the top P0 engineering
item** — a published budget we miss is worse than no budget.

## E5 · Restore the tuning sweep

`scratch/sweep.py` was deliberately kept out of the repo, so the table justifying the forecaster's
thresholds cannot be reproduced. Restore it as `loom/sweep.py` and regenerate
`forecaster_tuning.md` from it.

## E6 · Multi-cause: recommend a discriminating sample

**Why.** 17 % precision on `multi_cause.yaml` is not a tuning problem, it is a missing behaviour.
A quality engineer facing two competing hypotheses does not hold the line — they pull a targeted
sample that separates them.

**Method.** When the top hypothesis's posterior is below a confidence bar, or the top two are
within a margin, the twin abstains from holding and instead emits a `SampleRequest`: the k
un-inspected vehicles whose inspection most reduces uncertainty between the leading hypotheses.
Precision is then reported as a curve against the number of inspection fails observed.

**Acceptance.** The scenario ends in a recommended sample and a rising precision curve, not a bad
hold. The proposal reports the curve.

---

## A1–A3 · Make the AI real and visible

**A1.** `pip install anthropic`, credentials in the environment, all four features run against
`claude-opus-5`. The template path stays as the offline fallback *and* as the control arm in a
side-by-side. Deliverable: a comparison page and real token/latency/cost telemetry.

**A2.** New routes `/api/whatif`, `/api/improve`, `/api/onboard`, plus reports promoted out of the
Log tab into a dedicated **AI** tab. Three of the four AI features currently have no HTTP route at
all, so a judge clicking through the prototype finds no AI.

**A3.** A scripted scene in which the model is handed a deliberately thin evidence pack, reaches
for a number that is not in it, and the grounding check flags the report red and stores it
`grounded = 0`. Acceptance: reproducible on demand, not a live gamble.

## A4 · LLM eval suite

50 generated evidence packs, scored on:

- **groundedness** — share of reports where every number traces to the pack
- **abstention** — share of contradictory / missing-evidence packs where the report says so rather than inventing
- **persona fit** — does the exec report avoid station-level jargon; does the operator report avoid ROI language

Plus a small red-team set (a pack containing an instruction-shaped operator note). Acceptance:
three numbers in the deck, produced by `python -m loom.aieval`.

## A5 · Model tiering and cost

Haiku 4.5 for routine shift handovers, Opus 5 for root-cause narration and the improvement loop;
cache the static half of the evidence pack. Report measured cost per insight and per line-month.

## A6 · Operator notes are data

Confirm/dismiss notes flow into evidence packs and then into a prompt. State and enforce the
boundary — notes are quoted data, never instructions — with a test that plants an injection
attempt and asserts the report ignores it.

---

## U1–U5 · Presentation

- **U1.** The Exec tab currently renders `$0 / year · payback n/a · net -$60,000/yr` on a healthy
  line. Compute from the stored ledger across the session, or from the benchmark, and label the
  basis. No tab may be un-screenshot-able at any moment.
- **U2.** Fit the camera so the line fills the viewport (it uses roughly 15 % today), and make the
  default right panel show live alerts rather than an instruction placeholder.
- **U3.** Supervisor and exec views as designed UI — cards, severity colour, an andon strip, a
  sparkline — then quality and maintenance. Six monospace dumps contradict a headline claim of the
  brief.
- **U4.** Story mode: a scripted sequence running the five demo scenes with captions and timed
  injections. It is simultaneously the video's spine, the rehearsal harness, and the failure plan.
- **U5.** VOI as a ranked, clickable retrofit roadmap: "instrument B3 next, ~$50 clamp, expected
  +2.4 min lead". Converts the brief's hardest constraint into a fundable plan on screen.

## M1–M2 · Market

- **M1.** Add to the landscape table, each with the same "why it does not fit" column: Augury
  Process Health (ex-Seebo) — the closest competitor, AI root-cause on multi-causal quality —
  Sight Machine, Braincube, Cognite, PTC ThingWorx, Azure Digital Twins, AWS IoT TwinMaker,
  Rockwell FactoryTalk/Plex, Siemens Opcenter.
- **M2.** A positioning map on the axes buyers purchase along (sensor coverage required ×
  reactive/predictive/prescriptive), a cost wedge against Vorne-class and Plant-Simulation-class
  alternatives, the buyer and the budget line (OT opex, not capex), a why-now beat, and prepared
  build-vs-buy and defensibility answers.

---

## Demo scene order (the spine of deck and video)

1. Healthy line — provenance marks, nothing raised.
2. Tool wear at B3 — alert, ETA, then the line blocks when it said it would.
3. **Hero shot** — the same fault with B3 dark. No data for B3 at all; the warning still fires
   from its neighbours. Under twenty seconds.
4. Silent weld drift — targeted hold before end-of-line inspection sees the first defect.
5. Rework — a quality problem becoming a flow problem; the inspection station becomes the bottleneck.
6. AI — the persona reports, the what-if ranking, the gate refusing a proposal, and the grounding
   check catching an ungrounded number.
7. Another plant, 30 stations, unchanged code.
