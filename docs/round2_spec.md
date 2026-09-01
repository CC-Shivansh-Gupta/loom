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
| E5 | Restore the forecaster tuning sweep | P1 | `loom/sweep.py` | **done** → `docs/forecaster_tuning.md`, and it disagreed with the shipped defaults; resolved below |
| E6 | Multi-cause: discriminating sample instead of a bad hold | P1 | `loom/quality.py`, `loom/trace.py`, `loom/evidence.py`, `web/app.html` | **done** — abstain, `SampleRequest`, precision curve; precision 16 % → 67 %, and the curve is reported |
| E6a | Back-fill: a scenario that exercises it, or drop the claim | P1 | `configs/`, `docs/proposal.md` | **open** — claim removed from the proposal for now |
| E7 | False-alarm rate on multi-fault scenarios (1.3–2.4 / 8 h vs a 0.2 budget) | **P0** | `loom/twin.py`, `loom/evaluator.py` | **done** — 0.1–0.2 / 8 h, healthy floor unchanged |
| A1 | Claude wired; live run of all four AI features | P0 | env + `loom/llm.py` | — |
| A2 | AI tab: reports, what-if, improve, onboard in the control room | P0 | `loom/server.py`, `web/app.html` | **done** |
| A3 | Grounding catch — the model caught reaching for a number | P0 | `loom/aieval.py`, AI tab | **done** — red-team fixtures, 4/4 caught |
| A4 | LLM eval suite (groundedness, abstention, persona fit) | P1 | `loom/aieval.py` | **done** → `docs/ai_eval.md` |
| A5 | Model tiering and measured cost per insight | P1 | `loom/llm.py` | — |
| A6 | Operator notes treated as data, not instructions | P2 | `loom/evidence.py` | **done** — quoted, delimiter-stripped, `trust: untrusted_operator_text`; `tests/test_injection.py` |
| U1 | Exec view never renders $0, and is a designed view | P0 | `loom/views.py`, `loom/bench.py`, `web/app.html` | **done** — falls back to `benchmark.json` and labels the basis; adds a 1/10th sensitivity line |
| U2 | Camera fit; default panel shows live alerts | P0 | `web/app.html` | **done** — and it exposed a real UX confusion, see U2a |
| U3 | Supervisor, quality and manager views as designed UI, not `<pre>` | P1 | `web/app.html`, `loom/live.py` | **done** — render from `/api/pack`, the same evidence pack the AI layer gets |
| U4 | Story mode — scripted demo scenes with captions | P1 | `web/app.html` | **done** — 10 scenes, drives the same public API a person would click |
| U5 | VOI as a clickable retrofit roadmap | P1 | `web/app.html`, `loom/live.py` | **done** — ranked, priced, in the manager view |
| M1 | Complete the competitive landscape | P0 | `docs/research.md`, `proposal.md` | **done** — Augury/Seebo, Sight Machine, Braincube, Cognite, ThingWorx, Azure DT, TwinMaker, Opcenter, FactoryTalk |
| M2 | Positioning map, cost wedge, buyer and budget, why-now | P1 | `docs/proposal.md`, `research.md` | **done** — plus build-vs-buy and defensibility |

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

**What shipped.** All of it, plus one thing the design did not anticipate.

The twin now records a `precision_curve` entry at every contribution run — the leading hypothesis,
its posterior, how many vehicles sit under it, the best rival, and whether it held or sampled — and
abstains when the leader is below `HOLD_MIN_POSTERIOR` (0.5, the break-even point at which a hold
destroys more good product than bad) or within `HOLD_SEPARATION` of its rival. The abstention emits
a `SampleRequest` naming up to `SAMPLE_K` un-inspected vehicles, each matching exactly one of the two
candidate condition sets so its result moves the evidence rather than being consistent with both.

On `multi_cause.yaml`, seed 0, the curve is the whole argument in four lines: at 3 fails the single
condition `B4.torque low` leads at 0.30 and it samples; at 4 fails, 0.33, it samples; at 5 fails the
pair overtakes at 0.67 and it holds — on the pair. Over 20 seeds containment precision goes **16 %
→ 67 %** and hold size from 11 % of a blanket hold to 3 %.

**What it costs, said plainly.** Recall falls 37 % → 13 % and escapes go 6 → 8 across 20 runs. That
is not a detection failure — the pair is still ranked first in 17 of 20 — it is the twin declining to
contain on evidence that would scrap five good vehicles for every two bad ones. The trade is now
`HOLD_MIN_POSTERIOR`, written as a break-even argument rather than a tuned constant, so a plant that
would rather over-contain has exactly one number to move and can see what moving it buys.
`docs/solution_design.md` §6b limit (4) states this rather than hiding it.

**Reported.** `docs/traces.md` carries the curve and the sample request; the quality view renders a
"Held off, sampling instead" block beside the holds, with the decision table under it, so the
abstention is visible in the product and not only in a document. `tests/test_quality.py` asserts the
sample-then-hold ordering and the rising posterior.

**One thing the ablation nearly hid.** The above landed in commit `a80b3c8` and `docs/benchmark.md`
was not regenerated, so for a day the repo carried a 16 %-precision row describing code that no
longer existed. It surfaced only because a later regeneration showed a recall drop that looked like a
regression. Regenerating the evidence documents is part of a change, not a follow-up to it.

**A refinement on top of it.** Before the twin can recommend a better action it has to stop taking
a worse one, and it was still taking one. A hold opened by an out-of-spec reading was withdrawn as soon
as the single-condition posterior fell under `HOLD_MIN_POSTERIOR` — a *point* estimate. Inspection
sits minutes downstream of the cause station, so the vehicles that have been judged are
systematically older than the ones being held; early on that estimate is both low and worthless.
Two defects in five reads 0.40 and pulls a good hold twenty minutes before it should.

`QualityTwin._posterior_ucb` replaces it with the Wilson upper bound, so containment is withdrawn
on what the evidence *rules out* rather than on what it happens to say. The same 2-of-5 bounds at
0.77 and the hold stands; 2 of 40 bounds at 0.16 and it does not. Measured on `multi_cause.yaml`,
30 seeds, against the point estimate:

| | precision | recall | escapes |
|---|---|---|---|
| point estimate | 62.2 % | 12.2 % | 11 |
| Wilson bound | **66.1 %** | **13.9 %** | 11 |

Small, in the right direction, and exactly inert on `weld_drift_b2.yaml` (79.8 % / 99.3 % either
way), which is the control: a single-cause scenario has no rival hypothesis for an early sample to
wrongly favour.

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

## U2a · Forecast alerts and the current constraint are different questions

Building the live panel surfaced something the old placeholder hid. On `ramp_b3` at 08:00 the twin
holds **no open alert**, while B3 sits at 79.5 s against a 60 s takt and is plainly the constraint.
That is correct, and the reason is worth putting in the pitch: the forecaster predicts *when a
buffer will fill*. Once the block has formed and the line settles, measured supply into B3 falls to
B3's own rate, the buffer stops growing, and there is no future event left to predict. The alert
clears because the thing it was predicting has already happened.

The constraint has not gone away — it has become the *current* one, which is exactly what the
active-period detector reports and what the header has always shown. The panel now presents them as
two sections, **Current constraint** and **Forecast alerts**, because a panel that shows only the
second says "nothing forming" while a station is visibly holding up the line.

This is a good answer to a question a judge will ask ("your alert cleared but the station is still
slow?") and it was invisible until the default panel had to state the line's condition in words.

## U3a · The evaluator does not belong on a render path (found by U3)

The persona views render from `/api/pack` — the same evidence pack the AI layer is handed, so a
briefing and the screen a supervisor is looking at cannot disagree. Building that endpoint exposed
two performance faults that would have been fatal in a live demo:

1. **`voi.rank` re-runs the twin once per dark station.** On the 30-station plant that is ~40 s. It
   was inside the pack.
2. **The scorecards walk every plant event and every vehicle built**, so their cost grows with the
   run. Fine once at the end of a benchmark, wrong on the path that paints a screen: the first
   person to open a persona view after an hour of fast-forward waited 40 s on a blank panel.

Fixed in three steps, each worth stating because the first two were wrong:

- Cached both, keyed on **line** time. This does nothing: at 300× a 60 s line-time cache expires
  every 0.2 s of real time. What is being protected is the responsiveness of a screen, which is a
  wall-clock property.
- Cached on wall time. Better, but the *first* request after a long run still paid the full cost.
- A daemon keeps the ledger and the ranking warm. Views read what is current and never block, at
  the cost of up to 15 s of staleness in a number whose horizon is a shift. `scorecard()` and the
  text views still recompute, because the strip is cheap and the tests read it as the truth of the
  run.

**And one presentation fault.** The retrofit roadmap rounded sub-minute warning gains to "0.0 min"
and then hid them — so a list that *is* ranked by warning gained looked arbitrarily ordered, on a
panel whose entire claim is "ranked by what it buys". It now shows the gain in seconds under a
minute, and abstains entirely until the twin has seen twenty minutes of line, because ranking
retrofits off ten minutes of history produces a table of zeroes in a confident order.

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

## E5a · The sweep disagreed with the shipped defaults (opened by E5)

`loom/sweep.py`, restored and run, picks `window = 10` where the twin ships `window = 20` — 9.3 min
of mean lead against 6.4, both at zero false alarms on the scenarios the sweep saw. The defaults
only move through `improve.py`'s gate, so the disagreement was recorded rather than applied, then
tested.

It is wrong, and the way it is wrong is the interesting part. Neither the sweep's three scenarios
nor the broadened six-scenario harness separates the two settings on false alarms; both report the
same rate, because a false alarm is a low-rate quantity and twelve healthy hours cannot tell 0.4
per 8 h from 0.8 per 8 h. Measured on 240 healthy hours per setting, they separate immediately:

| `window` | alarms in 240 healthy h | per 8 h |
|---|---|---|
| 20 (shipped) | 9 | **0.30** |
| 10 (sweep's pick) | 29 | **0.97** |

A shorter window fits fewer cycles, so its slope is noisier, so it crosses the significance test
more often on a line where nothing is wrong. The lead it wins on a real ramp and the alarms it
invents on a quiet line are one effect measured twice, and 2.9 min does not buy a tripled
false-alarm rate against a 0.2-per-8-h budget.

**The finding is about the gate, not the window: a gate that samples the cost of a change less
precisely than its benefit will always drift toward the change.** Lead time is measured per fault,
so a few runs pin it down; false alarms are counted per healthy hour, so the same few runs leave
them wide open, and any search optimising the pair walks into the imprecision. `DEFAULT_SCENARIOS`
now runs 48 healthy hours over six seeds and adds the moving constraint and the 30-station plant,
with the reasoning in a comment at the definition. Written up in `docs/forecaster_tuning.md`.

---

## What is left

| item | why it is still open |
|---|---|
| **A1 · Claude wired** | needs an `ANTHROPIC_API_KEY`. Everything is built behind the provider boundary, so it is one environment variable; `aieval` then rescores model output with no change to any file |
| **A5 · model tiering / measured cost** | blocked on A1 |
| **E6a · the back-fill claim** | needs a sparse-reading scenario, or the mechanism goes |
| **Deck** | **done** — `docs/deck.html`, 11 slides, every figure from a generated evidence document |
| **Video** | not started. Story mode is its spine: ten scripted scenes, re-recordable after any code change |

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

---

## Where to pick up

Everything below is repo state, not memory — a fresh session can start here.

**Run it.**

| | |
|---|---|
| tests | `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q` — the flag is not optional, a ROS `launch_testing` plugin on this machine breaks collection without it |
| control room | `python3 -m loom.server`, then `http://localhost:8000`. Check `pgrep -af loom.server` and kill stale ones first; a leftover process holding port 8000 serves old HTML and looks exactly like a render bug |
| regenerate evidence | `python3 -m loom.bench --seeds 20 --out docs/benchmark.md`, and `-m loom.{baseline,ablate,coverage,trace,sweep,aieval} --out docs/<file>.md`. **`--out` is required** — without it they only print |
| grounding check | `python3 -m loom.numbers` — fails if any figure in a generated document was not produced by a run. It checks *presence*, not correspondence, so a stale-but-still-present number survives it; `docs/exempt_numbers.md` carries the cited-not-measured figures |

**Open, in the order worth doing them.**

1. **A1 · wire Claude.** `pip install anthropic` and an `ANTHROPIC_API_KEY`. `ClaudeProvider` is already
   written and already selected by env; `TemplateProvider` stays as the offline control arm. Then
   `python3 -m loom.aieval --out docs/ai_eval.md` rescores real model output with no file changed.
   Everything else in the AI axis is done and demonstrable offline, so this is upside, not a blocker.
2. **A5 · model tiering and cost per insight.** Blocked on A1 and cheap once it lands.
3. **E6a · back-fill.** Needs a config that samples parameters sparsely — `weld_drift_b2` reports a
   reading per vehicle, which is why the ablation measures the mechanism as inert. Otherwise drop it.
4. **Video.** `docs/video_script.md` is the shot list; story mode in the control room is the spine,
   so it can be re-recorded after any code change. Hero shot is scenes 6–7, the dark station.

**Things learned the hard way, so they are not re-learned.**

- Do not commit while subagents are still editing. A `git add -A` mid-flight swept another agent's
  in-progress work into a commit whose message did not describe it.
- A claim is worth what its seed count is worth. The 20-seed re-run destroyed two claims that four
  seeds had supported, and that is what opened E7 — a real alert-flooding bug.
- The tuning gate measures benefit per fault and cost per healthy hour, so it under-samples cost by
  construction and drifts toward whatever change it is offered. See E5a.
