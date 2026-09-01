# Loom — solution design against the DigitalTwin.ai brief

This document answers the Round 2 brief point by point: every "real-world complexity" and every
"solutioning area" in the track, plus the complexities the brief does not list but a judge who
has run a plant will ask about. For each: the mechanism, how the prototype demonstrates it, and
what evidence the demo produces. Landscape and sources are in `research.md`; the architecture in
`framework.md`.

**Thesis.** Most assembly lines are partially instrumented and will stay that way. A twin that only
works when every station reports is a twin for the plant nobody has. Loom is built for the plant
everybody has: it says what it knows, what it infers, and what it forecasts, it keeps score of its
own predictions, and it recommends rather than acts.

---

## 1. Real-world complexities named in the brief

### C1 · Uneven sensor coverage — legacy and modern equipment side by side

**Mechanism.** Instrumentation is a per-station property in config (`plc_full`, `cycle_only`,
`checklist`, `dark`, or plant-defined). The sensor layer is the only path from plant to twin, so the
twin can never accidentally use data the real line would not have. For dark stations the twin runs a
*flow soft-sensor*: a vehicle that left the last instrumented station upstream at `t1` and appeared at
the first instrumented station downstream at `t2` must have occupied the dark stretch in between, which
bounds each dark station's cycle time and state. The bound widens with the number of consecutive dark
stations, and the belief is tagged ◐ inferred with an explicit interval.

**Low-cost sensing menu** (each maps to a sensor profile, so the twin can quantify what a retrofit buys):

| retrofit | cost | what it yields | profile |
|---|---|---|---|
| split-core current clamp on the main motor | ~$50 | busy / idle / loaded, cycle timestamps, load trend | `cycle_only` + load param |
| photoelectric / inductive sensor at the exit fixture | ~$30 | vehicle exit timestamps | `cycle_only` |
| barcode / RFID scan at zone boundaries | existing MES | vehicle identity + timestamps at 3–4 points | zone-boundary `cycle_only` |
| operator app tap (Tulip-style) on manual stations | app licence | finish timestamps + checklist | `checklist` |
| tri-axial accelerometer on a bearing housing | ~$100 | degradation trend for tool-wear ramps | param stream |

**Prototype evidence.** `ramp_b3_dark.yaml`: same tool-wear ramp as `ramp_b3.yaml` but B3 is dark and
B2 reports only cycle timestamps. The scorecard shows the lead time Loom still achieves from
neighbours, vs the fully instrumented case, vs no twin. The manager view lists coverage and ranks which
dark station to instrument next (see S3).

### C2 · Multi-causal, intermittent root causes

**Mechanism.** Every vehicle carries a build record: for each station the timestamps, the measured or
inferred process parameters, the variant, the shift, and the ambient context. When defects surface at
an inspection station, Loom does *contribution analysis* over that record: for each upstream condition
(station × parameter band × variant × shift × ambient band) it computes the lift of defective over
good vehicles with a significance test, and reports ranked hypotheses with evidence counts and
confidence — never a single "the cause is X". Interacting causes (e.g. low weld current **and** high
humidity) are found because pairs of conditions are scored too. Expert structure priors (which
parameters can physically affect which defect) restrict the search space; this is the practical form of
the causal-Bayesian-network approach in the literature, kept interpretable.

**Prototype evidence.** `multi_cause.yaml`: paint adhesion fails only when B4 torque is low *and* P1
humidity is high; neither alone. No drift signal. As the inspection fails accumulate the hypothesis
table ranks `B4.torque low AND P1.humidity high` first — lift 53.8x, 4 of the 5 vehicles under it
defective against 2 of 167 otherwise, p=2.1e-06 — above `B4.torque low` alone (16.0x, p=2.5e-06),
with humidity alone never clearing the bars. The hold names the un-inspected vehicles that match the
pair. The trace is generated: `docs/traces.md`. Across 20 seeds the true pair is ranked first in 17.

Ranking the cause is not the same as acting on it, and the interesting behaviour is what happens in
between. At 3 and 4 inspection failures the single condition `B4.torque low` still leads, at a
posterior of 0.30 and 0.33 — below the break-even point where a hold destroys more good product than
bad. The twin does not hold on it. It issues a `SampleRequest`: inspect these named vehicles next,
each chosen because it matches exactly one of the two candidate condition sets, so its result moves
the evidence rather than being consistent with both. At 5 failures the pair overtakes at 0.67 and
the hold opens on the pair. The abstention is an action with a reason, and both the trace and the
quality view show it beside the holds — see §6b for what it costs.

### C3 · Modifying live production systems is risky; retrofits only in maintenance windows

**Mechanism.** Loom is read-only by construction. It subscribes (OPC UA, MQTT unified namespace,
historian, MES events, operator app) and never writes to a PLC. Deployment ladder: **shadow** (twin
runs, nobody sees alerts, evaluator scores them) → **advisory** (alerts with evidence, humans act) →
**reversible automatic** (flag a batch for extra inspection; nothing that touches the physical line).
A retrofit is a config diff — a sensor profile flips from `dark` to `cycle_only` — and Loom's
value-of-information ranking (S3) tells the plant which station to instrument in the *next* window.

**Prototype evidence.** The sensor layer *is* the integration boundary: it is the only module a real
deployment replaces. `docs/framework.md` shows the config diff for a retrofit.

### C4 · A defect introduced early surfaces late; many units share it

**Mechanism.** Two triggers, one response. (a) A parameter drift is detected at its source by
EWMA/CUSUM on the station's own stream → onset time estimated (with uncertainty) → every vehicle that
passed the station since onset is in the *at-risk set*. (b) A defect is caught at inspection with no
upstream signal → trace backward through the build record to find the conditions those vehicles share
that good vehicles do not (C2) → trace forward to every other vehicle built under the same conditions.
Either way the result is a **targeted hold set** with a stated precision, instead of a blanket hold or a
line stop. Onset uncertainty is handled by widening the window and tagging the marginal vehicles ◐.

**Prototype evidence.** `weld_drift_b2.yaml`: B2's weld current sags out of spec from minute 30 with
no cycle-time symptom; weak welds surface only at the F5 end-of-line inspection. Measured on a 2-hour
shift (seed 0, `docs/traces.md`): CUSUM warning on `B2.weld_current` at 38.9 min with the onset
estimated at 32.9 min (true 30), the hold opened the same minute on the first out-of-spec reading —
**16.7 minutes before the first inspection catch at 55.6 min** — 76 vehicles held, 80 % precision,
98 % recall, zero escaped; the no-genealogy blanket hold would be 90 vehicles and would not have
started until inspection caught the first one. Over 20 seeds: 12.8 min ahead, 81 % precision, 99 %
recall. Research figures for the pitch: a defect caught
downstream costs ~10×, at final assembly ~100×, in the field ~1000×; one plant cut a containment
exercise from two days of log review to four minutes with genealogy.

### C5 · Different stakeholders need different views of the same twin

Same twin, six lenses. Built: operator, supervisor, plant manager. Planned: quality engineer,
maintenance, leadership.

| role | horizon | what they see | provenance surfaced |
|---|---|---|---|
| operator / technician | now, my station | my state, my cycle vs takt, next alert, "is this measured or a guess" | every value |
| line supervisor | this shift | andon by station, forecast bottlenecks with ETA, buffers filling, output vs target | alerts marked ○ |
| quality engineer | today | drift trends, at-risk sets, hypothesis table, first-pass yield | intervals on inferred params |
| maintenance | this week | degradation trends per asset, time-to-threshold, suggested window | trend confidence |
| plant manager | week / month | throughput and loss Pareto, **trust ledger** (lead time, false alarms, containment precision), coverage map, next-sensor ranking | aggregates |
| leadership | quarter | multi-line/plant benchmark, ROI to date, rollout plan | none — outcomes only |

### C6 · Extending beyond one line or plant

**Mechanism.** The line is data. ISA-95-shaped YAML, station-type and sensor-profile libraries shared
across plants, `extends:` for site overrides. Twin algorithms read topology from config and never
hard-code station counts. Alert thresholds are self-calibrated per station from its own noise (tests are
in standard-error units), so a noisier legacy station does not need hand tuning. Rollout playbook per
site: 2 weeks shadow → advisory → reversible automatic, with the trust ledger as the gate.

**Prototype evidence.** `plant_b.yaml` — a 30-station, 4-zone line with different buffers, mix and
sensor maturity — runs on the same code with zero changes.

### C7 · Predictive claims must be validated over time; false alarms erode trust fast

**Mechanism.** The evaluator ships with the product. Every alert becomes a record with an outcome:
hit (with lead time and ETA error), miss, or false alarm. Thresholds are set against a stated
false-alarm budget (currently < 1 per five 8-hour shifts on a healthy noisy line) and the sweep is
documented (`forecaster_tuning.md`). Persistence rules and standard-error tests are the guards; a
naive trend test produced 49 false alarms per shift and is kept in the doc as the cautionary baseline.
Operators can confirm or dismiss an alert; dismissals feed the per-station threshold. Confidence is
shown on every alert and is calibrated against realised outcomes (calibration curve in the ledger).

**Prototype evidence.** `test_false_alarm_rate_on_healthy_noisy_line`, the scorecard in every run,
the plant-manager trust ledger.

---

## 2. Solutioning areas named in the brief

### S1 · Modelling approach — explicit vs inferred

| represent explicitly | infer | simulate | deliberately not modelled |
|---|---|---|---|
| station state, buffers, cycle times, takt, variants; process parameters where measured (torque, weld current, booth temperature, humidity); inspection outcomes; build records | dark-station state and cycle (interval bounds from neighbours); physically-coupled parameters at unmeasured stations (booth humidity at P2 from P1); drift onset time | bottleneck ETA, next-hour throughput, what-if (add a floater, change buffer) | 3D geometry, robot kinematics, energy — none of the decisions in scope need them |

### S2 · Predictive techniques and how they are validated before trusted

| target | technique | why this one | validation |
|---|---|---|---|
| bottleneck forming | per-station linear trend on measured cycles + forward micro-simulation of buffer fill | transparent, cheap, produces an ETA not just a flag | lead time and ETA error vs ground truth; FA rate on healthy shifts |
| current bottleneck (cross-check) | active-period method (Roser) — longest uninterrupted active period | best-validated detection method in the literature; catches shifting bottlenecks | agreement with forecaster on injected faults |
| parameter drift | EWMA and CUSUM per station with self-baselining; time-to-tolerance projection | standard SPC, tuned for small persistent shifts, explainable to a quality engineer | detection delay and FA on injected ramps |
| defect risk per vehicle | interpretable score from parameter bands passed through (logistic, few terms) | must be explainable at the point of a hold decision | precision/recall and calibration vs inspection outcomes |
| root-cause hypotheses | contribution analysis with expert priors (C2) | multi-causal, interpretable, works with sparse defects | recovers injected cause pairs |

No black-box model at the core. An LLM, if used, narrates ranked evidence for a persona; it never
produces a number.

### S3 · Handling data gaps

Interval inference for dark stations (C1); confidence that decays with the length of the dark stretch;
the low-cost sensing menu; and a **value-of-information ranking**: for each dark station, Loom
estimates how much its forecasts and containment sets would tighten if that station reported cycle
timestamps (by re-running the twin on recent history with the profile flipped) and orders retrofits
by gain per dollar. This turns "uneven coverage" into a roadmap the plant manager can fund.

### S4 · User experience

Section C5. Every view reads one twin; the differences are horizon, aggregation and which provenance
marks are shown. Alerts carry evidence (the fitted trend, the buffer trajectory, the sample count) and a
confidence; recommendations carry the at-risk set and its precision. Overrides are captured with a
reason and shown back in the ledger.

### S5 · Integration approach

```
PLCs ──OPC UA (subscribe)──┐
MES / historian ───────────┤                     ┌── operator view
operator app / barcode ────┼─▶ edge collector ─▶ Loom ─┼── supervisor view
low-cost retrofit sensors ─┘   (normalise to      │   └── manager view
                               Event schema)      ▼
                                             evaluator / ledger
```
Read-only, on the OT side of the firewall, one event schema. In the prototype the sensor layer plays
the role of the edge collector, which is why it is the one module a real deployment swaps.

### S6 · Scalability and ROI

Config replication (C6). ROI is a live view (`views.leadership`, the **Exec** tab), computed from
this line's *measured* lead time, hold sizes and escapes and from stated assumptions in the plant
file's `economics:` block — every input printed so a sceptic can change one and redo the arithmetic.
Anchors from the research: an idle automotive line costs $2.3 M/h (Siemens 2024), of which one
line's share is the default `downtime_cost_per_min: 8000`. Formulas:

- **Bottleneck avoidance.** Lead time × fraction of events where a floater or re-balance prevents the
  block × line rate × contribution margin per vehicle. Illustrative: 8 min lead, 50 % prevented,
  60 veh/h, 3 events/week.
- **Targeted containment.** (blanket hold size − targeted set size) × cost per held vehicle; plus
  time-to-containment (days → minutes).
- **Escape reduction.** Drift caught at source instead of at end-of-line: 10× / 100× / 1000× cost
  ladder applied to the escaped-defect rate.
- **Sensor spend avoided.** Instrument the top-VOI stations only, in existing windows.

---

## 3. Complexities the brief does not list — and how Loom handles them

| complexity | handling |
|---|---|
| **Shifting bottlenecks** — the constraint moves as mix and wear change | per-station forecasting plus the active-period cross-check; demo with two staggered ramps |
| **Mixed-model sequence effects** — a run of SUVs overloads F1 even with no fault | variant multipliers in the plant; forecaster reads the variant mix waiting in upstream buffers |
| **Sensor faults vs process faults** — stuck sensor, clock skew, dropouts | sensor-health checks: a reading that contradicts its neighbours' timestamps is quarantined and its provenance downgraded to ◐ |
| **Alarm flooding** — one bottleneck starves eight downstream stations and blocks the ones before it | alerts grouped by causal chain; a consequence is a line under the root alert, not eight alerts. Grouping keys on the *physical* constraint — a downstream station whose believed cycle sits `min_over_z` SEs over takt — not on whether that station currently holds an alert, because the root's alert clears before the queue it built drains |
| **Rework loops and parallel stations** | built: `capacity: n` workers share a queue (forecaster compares cycle/n to takt; what-if "floater" adds a worker); inspection `rework_p`/`rework_s` sends fails to a bay and re-enters them out of order as a new thread. `parallel_rework.yaml` shows a quality problem becoming a flow problem: F5 processes fails twice, turns into the bottleneck, and the forecaster names it — because supply is measured per station, not assumed to be takt |
| **Shifts, breaks, operator variation** | built: `calendar:` with breaks (no releases, cycles paused; the twin subtracts break time from cycles and never calls a station silent during one) and shifts with per-station crew multipliers (trend windows restart at a shift change so a slower crew is not a ramp). `shifts.yaml`: two breaks, a slower night crew, and a real B3 ramp at 5 h — 0 false alarms, ramp caught |
| **Onset-time uncertainty in containment** | window widened by the detection delay's uncertainty; marginal vehicles tagged ◐ and listed separately |
| **Cost of a false hold** | every hold recommendation carries its expected precision; supervisor sees "12 vehicles, ~9 truly at risk" |
| **Data governance and OT security** | read-only, on-prem edge, OT/IT segmentation; audit trail of every recommendation and override (supports IATF 16949 traceability) |
| **Change management on the floor** | shadow-mode scoreboard visible to operators before alerts go live; overrides respected and fed back; explanations kept brief — the literature warns over-explaining raises misuse as fast as it lowers disuse |
| **Model drift of the twin itself** | trust ledger trends over weeks; thresholds re-baselined per station on a schedule |

---

## 3b. Where AI belongs (and where it does not)

This is an AI competition, so the use of AI has to be visible — and defensible to a judge who has seen
LLMs hallucinate a number. The rule: **statistics and simulation produce every number; an LLM turns
numbers into decisions people can act on, proposes hypotheses for the simulator to test, and drives
the improvement loop through a gate it cannot bypass.**

| use | valid? | mechanism | why it survives scrutiny |
|---|---|---|---|
| **Persona reports** — shift handover for the supervisor, weekly summary for the plant manager, containment memo for quality | yes | `narrate.py`: LLM is given the twin's structured evidence (alerts, scorecard, ledger, VOI ranking, at-risk sets) as JSON and writes for one persona; deterministic template fallback for offline demo | grounded generation; the LLM never computes; every figure in the text is traceable to a field |
| **Improvement suggestions** | yes, with a constraint | `whatif.py`: a what-if engine clones the twin's *believed* state and simulates candidate mitigations (floater at B3 → cycle × 0.8, rebalance work B3→B4, hold batch, larger buffer) and ranks them by predicted lead-time and throughput gain. LLM proposes candidates from a fixed menu given the situation, then explains the ranked, quantified options with caveats | LLM as hypothesis generator, simulator as judge — recommendations come with a predicted effect and are validated by the evaluator like any prediction |
| **Root-cause narration** (step 5) | yes | contribution analysis produces ranked hypotheses with lift and support; LLM writes the quality engineer's brief and suggests which hypothesis to check first and how | the ranking is statistical; the LLM adds the "what to do about it" |
| **Self-improving loop** | partly | two forms. (a) Statistical: evaluator outcomes and operator dismissals recalibrate per-station thresholds and confidence — no LLM, fully verifiable. (b) Agentic: an agent reviews the trust ledger, proposes parameter or rule changes, and they are accepted **only** if they pass the evaluation harness on recorded shifts (false-alarm budget kept, lead time not worse). One iteration is shown in the demo | propose → backtest → gate. Nothing an LLM says changes live behaviour without passing the same test humans set |
| **Onboarding assistant** | yes | "18 stations, three manual, paint buffer holds 10, takt 72 s…" → draft plant YAML, validated by the loader, reviewed by the engineer | turns the "extend to other plants" argument into a five-minute demo |
| LLM as the predictor or scorer | **no** | — | not validatable, not explainable at a hold decision, and the brief warns against exactly this |

Telemetry is kept for every LLM call (tokens, latency, cost) so the economics are on the manager view.

---

## 4. Why this can place

1. **It works on the plant people actually have.** Partial instrumentation is the default case;
   the competition's own brief says so. Competitors either need a sensor everywhere (OEE tools) or an
   expert-built offline model (DES tools).
2. **It keeps score.** The evaluator and trust ledger are in the product, with a published false-alarm
   budget and the tuning table that got there. Judges asked for validation against outcomes; this is it.
3. **Flow and quality in one graph.** The build-record thread turns a bottleneck twin into a
   containment tool — that is where the money is.
4. **Provenance everywhere.** ●/◐/○ on every number. Nobody else surfaces epistemic status to the operator.
5. **Onboarding is a YAML file.** Demonstrated live by running a second, different plant unchanged.
6. **Human-in-the-loop by construction**, with only reversible actions automated.

---

## 5. Demo script

Ten scripted scenes with captions run from the control room's **▶ Story** button, driving the same
public API a person would click — so it rehearses the real demo rather than being a separate code
path, and it is the failure plan if the live demo misbehaves. The spine:

1. **Healthy line** — `healthy.yaml`, 12 stations, noise and mix. Nothing raised, which is the hard
   part. Point out ● / ◐ / ○ on every value.
2. **Tool wear at B3** — `ramp_b3.yaml`. The cycle creeps up, the forecaster fits it and simulates
   the buffer forward: "B2 blocks in ~7 min". Then it does. The manager view scores the lead time
   whether it flatters us or not.
3. **Lights out at B3** — `ramp_b3_dark.yaml`, the hero shot. Same fault, B3 reporting nothing at
   all. A threshold alarm has nothing to threshold and never fires; Loom still warns, reconstructed
   from B2 and B4 alone, later and with lower confidence — and says by how much.
4. **Silent drift** — `weld_drift_b2.yaml`. Weld current sags out of spec with no cycle-time
   symptom. CUSUM catches it at source and opens a targeted hold minutes before end-of-line
   inspection sees the first weak weld, holding fewer vehicles than a blanket hold would.
5. **The AI layer** — the briefings written from the evidence pack, the what-if ranked by the
   simulator, the gate refusing a proposal, and the grounding check catching a fabricated number.
6. **Another plant** — `plant_b.yaml`, 30 stations. Same code, same views, zero changes.

Figures are deliberately not quoted here: every number in the demo comes from the run happening on
screen, and the measured distributions are in §6b and `docs/benchmark.md`.

## 6. Roadmap

| step | builds | proves |
|---|---|---|
| 1–3 (done) | DES plant, sensor profiles, forecaster with FA guards, config libraries, variants, three views | C5, C6, C7, S1, S2 (bottleneck), S4 |
| 4 (done) | sensor noise model (jitter, clock offset, dropouts, latency, silent-sensor faults); flow reconstruction for dark and finish-only stations with exact/bound provenance; sensor-health detection; alert grouping by causal chain; VOI ranking; `plant_b.yaml` | C1, C3, S3, alarm flooding, sensor faults |
| 5 (done) | process parameters with spec limits; EWMA/CUSUM drift with onset estimate; latent multi-cause defects visible only at inspection; contribution analysis (lift + Fisher exact, singles and pairs); targeted holds with sure/uncertain/exited split; containment scorecard vs blanket hold and vs end-of-line detection; quality view | C2, C4, S2 (drift, risk) |
| 6 (done) | AI layer (§3b, `docs/ai_layer.md`): evidence pack, template/Claude provider boundary with cost telemetry, persona reports, what-if mitigation engine (LLM proposes from a menu, simulator judges), evaluation harness + gated improvement loop + calibration table, onboarding assistant | S4, "appropriate use of AI" |
| 7 (done) | multi-seed benchmark (`docs/benchmark.md`) with calibration table; active-period momentary-bottleneck detector scored against plant truth (97 % agreement during faults, dark B3 included); sustained-block truth definition; `shifting.yaml`; maintenance view | C7, shifting bottlenecks |
| 8 | web UI on top of `views.py`; leadership view with ROI model | pitch |

---

## 6b. What the benchmark says (`docs/benchmark.md`, 20 seeds per scenario)

| claim | measured |
|---|---|
| Healthy line stays quiet | 0.10 bottleneck alerts and 0.45 drift warnings per 8 h line-wide; 0 holds over 160 h |
| Bottleneck warned ahead, fully instrumented | 20/20 caught, 7.4 min lead, ETA error 0.8 min |
| …with the bottleneck station dark | 20/20 caught, 5.2 min lead; inferred cycle error 0.3 s |
| …with a PLC link silent mid-fault | 20/20 caught, 7.4 min lead |
| Shifting bottleneck (two faults, one repair) | 37/40 caught, 0.2 false alarms / 8 h |
| A different 30-station plant with 9 checklist/dark stations | 20/20 caught, 12.0 min lead, 0.1 false alarms / 8 h |
| Momentary bottleneck from partial data vs plant truth | 96–99 % agreement during faults |
| Silent weld drift | hold 12.8 min before the first end-of-line catch, precision 81 %, recall 99 % |
| …with that parameter logged one body in five | hold 5.8 min before the first catch, precision 65 %, recall 58 % — the rest of the hold is placed by the estimated spec crossing, not by readings |
| Two-condition intermittent defect | true pair ranked first in 17/20 runs; containment precision 67 %, recall 13 %, holding 3 % of what a blanket hold would stop |
| Confidence means something | stated 0.9–1.0 → 100 % hit rate; 0.7–0.9 → 100 %; 0.5–0.7 → 85 % |

Known limits, stated rather than hidden: (0) `shifting` still **misses 3 of 40 faults**. (1) a defect with no upstream signal is only learnable from
inspection fails, so its hold necessarily trails the first catch; (2) two adjacent finish-only
(checklist) stations cannot be told apart — the twin abstains rather than guess; (3) one false alarm
per five 2-hour fault runs on the demo line sits at the budget, not below it.

(4) **`multi_cause` recall is 13 %, and that number is a choice, not a failure to detect.** The twin
ranks the true pair in 17 of 20 runs; what it will not do is contain on a posterior below the
break-even bar. Holding anyway would read as 37 % recall at 16 % precision — five vehicles scrapped
for every two that were ever going to fail — which is why the earlier build was tuned away from. Eight
defects escaped across 20 runs against six before, and that is the honest price: the twin trades
recall it cannot justify for scrap it will not create, and asks for the five inspections that would
settle it. Where a plant would rather over-contain, `HOLD_MIN_POSTERIOR` is the single number to
move, and it is written as a break-even argument rather than a tuned constant so that the trade is
visible to whoever moves it.

## 7. Risks and mitigations

| risk | mitigation |
|---|---|
| Judges see the simulated plant as circular ("you predict what you injected") | The wall between plant and twin is architectural and shown; the healthy-line false-alarm test is the honest half of the validation; the deployment ladder starts in shadow mode on real data |
| Inference at dark stations is wrong in the demo | Intervals, not point values; confidence decays with dark-stretch length; evaluator shows the miss honestly |
| Over-scoping | Each step is a runnable demo; the roadmap is ordered by pitch value |
| "Where is the AI?" | Statistics you can defend beat a model you cannot; the LLM role (narration for personas) is explicit and bounded |
