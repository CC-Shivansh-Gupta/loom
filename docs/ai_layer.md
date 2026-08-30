# The AI layer

Rule: **statistics and simulation produce every number; the LLM turns numbers into decisions,
proposes hypotheses for the simulator to test, and drives the improvement loop through a gate it
cannot bypass.**

```
                       twin + ledger
                            │
                     evidence.pack()  ──── one JSON, every number the AI may use
                            │
        ┌───────────────────┼─────────────────────┬──────────────────────┐
        ▼                   ▼                     ▼                      ▼
   narrate.py          whatif.py             improve.py             onboard.py
   persona reports     LLM proposes from     LLM/rule proposes a    NL description →
   (supervisor,        a fixed menu →        parameter change →     JSON in schema →
   quality, manager)   simulator measures →  harness backtests →    YAML → loader
                       LLM explains ranked   gate accepts/rejects   validates
```

## Provider boundary (`llm.py`)

| provider | when | what |
|---|---|---|
| `TemplateProvider` | default when no SDK/credentials, or `LOOM_LLM=template` | deterministic renderers reading the same JSON the LLM would get |
| `ClaudeProvider` | `pip install anthropic` + credentials, or `LOOM_LLM=claude` | `claude-opus-5`, adaptive thinking, `effort: medium`, JSON-schema output where structure matters |

Every call is logged (`llm.TELEMETRY`: tokens, latency, estimated cost) and surfaced on the manager
report as "AI layer cost". The template path costs $0 and exists so no demo depends on a key.

## Grounding

The system prompt for reports carries hard rules: every number must appear in the JSON; say
measured / inferred / simulated when the JSON marks it so; recommend only human actions; say what is
missing. The template renderers follow the same rules by construction, and a test checks that every
decimal in a template report appears in the evidence pack.

## What-if engine

`whatif.simulate` builds a fresh plant from the twin's *believed* state — fitted cycles (◐), buffer
counts, busy stations — applies one mitigation from the menu, runs 30 min forward and measures
throughput and upstream blocked time. Menu: floater (cycle × factor), rebalance (move seconds of
work to a neighbour), buffer (+n slots). LLM candidates are validated against the menu and the
station list (unknown stations and off-menu actions are dropped; factors clamped to 0.6–0.95).

Measured on `ramp_b3.yaml` at 45 min: baseline 50 veh/h with 10.2 min upstream blocking over the next
30 min; floater at B3 → 56 veh/h, 0 blocking; rebalance 5 s B3→B4 → 54 veh/h; +2 buffer → 50 veh/h.

## Gated improvement loop

`harness.evaluate(params)` reruns recorded scenarios (healthy shifts for false alarms, faults for lead
time) and returns false alarms per 8 h and lead times. `improve.improve()`:

1. propose — LLM (JSON: one or two parameter changes + rationale) or the rule proposer
2. backtest — `harness.evaluate` on the same scenarios
3. gate — accept only if false alarms ≤ 0.2 per 8 h, misses did not rise, and mean lead did not fall
   by more than 0.5 min; otherwise reject with the reason

The run log (`Run.as_dict()`) is the audit trail: every proposal, its numbers, accepted or not, why.
One run with the rule proposer (10 s, template path):

| proposal | FA / 8 h | mean lead | verdict |
|---|---|---|---|
| baseline (window 20, tstat 4, over_z 2, raise_after 3) | 0.00 | 6.9 min | — |
| raise_after 3 → 2 | 1.33 | 8.0 min | rejected: false alarms exceed budget 0.2 |
| min_tstat 4 → 3.5 | 1.33 | 7.2 min | rejected: false alarms exceed budget 0.2 |
| window 20 → 16 | 0.00 | 7.3 min | **accepted**: lead +0.4 min, false alarms unchanged |

The first two rows are the point: a proposal that "looks" better on lead time is refused because it
breaks the false-alarm budget — the gate, not the proposer, decides.
The harness also produces the **calibration table** (stated confidence vs realised hit rate) from
the ledger records.

## In the control room

All four features are reachable from the **AI** tab of the live control room, not only from the
CLI — a panel looks for the AI where the product is, and three of the four used to have no HTTP
route at all. Routes: `/api/report/{persona}`, `/api/whatif`, `/api/improve`, `/api/onboard`,
`/api/redteam`. Each one runs on the twin's *believed* state and each writes an audit row.

## Evaluating the AI layer (`docs/ai_eval.md`)

We ask the model to be grounded, so we measure whether it is and publish the number.
`python -m loom.aieval` scores four things over five evidence packs:

| measure | template provider | what it means |
|---|---|---|
| groundedness | 100 % (15/15) | every figure in a report occurs in the pack it was written from |
| abstention | 100 % | given an empty pack the report says so rather than inventing |
| persona fit | 100 % | the three briefs share ≤ 50 % of their lines and each carries its audience's subject |
| red team caught | 4/4 | planted fabrications the grounding check rejects |
| false accusations | 0 | clean reports wrongly flagged |

On the template path a perfect groundedness score is a statement about the deterministic
renderers, not about a model — they are grounded by construction and are the **control arm**. The
red-team column is what carries weight: four reports containing invented throughput, lead time,
money and precision figures, run through the same `store.grounding_check` that gates every stored
report. All four are caught, no clean report is flagged. Pointing the harness at a real provider
(`LOOM_LLM=claude`) recomputes every number above with no change to the file.

The red-team set is also a live panel (**AI → 5 · Grounding**), so the check catching a fabricated
number is something a judge watches happen rather than reads about.

Building this found a real defect: the renderers raised `KeyError` on a sparse evidence pack —
they failed at exactly the moment there was nothing to report. Fixed, and the abstention test keeps
it fixed.

## Onboarding

`onboard.draft("18 stations, takt 72 s, 4 manual, 2 dark, paint buffer 10")` → YAML + a list of
stated assumptions; the loader validates it, and one corrective retry feeds the loader's error back
to the LLM. The template path extracts the numbers it recognises and lays out a default line.

## What the LLM is not allowed to do

Predict, score a vehicle, decide a hold, change a live threshold, or write a number that is not in
the evidence pack.
