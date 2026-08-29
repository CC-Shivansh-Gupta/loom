# Data, audit and grounding

Before this step nothing was persisted: the twin, its alerts and the AI's evidence packs lived in
process memory and were rebuilt on demand. That is fine for a simulation and unacceptable for a
product whose pitch is "keeps score of itself". This is what exists now (`loom/store.py`, SQLite,
standard library only, written to `web/loom.db` by the live server).

## What is stored

| table | rows | why |
|---|---|---|
| `runs` | one per config load / reset: config name, the full YAML, wall time | a run is reproducible from its YAML plus its events |
| `events` | **every event the twin received**, after the sensor layer | the twin's beliefs are a pure function of this table — dark stations never appear in it, a silenced sensor stops appearing |
| `twin_events` | every alert raised / cleared / grouped, drift warning, hold | the twin's outputs, with ETA and confidence at the time |
| `snapshots` | the twin's belief every minute: state, provenance, buffers, active alerts | "what did Loom believe at 10:42" has one stored answer |
| `audit` | every action with actor and line time: config load, reset, each injection, alert **confirm / dismiss** with a note, recording start/stop, each AI report | the human-in-the-loop trail |
| `evidence` | every evidence pack handed to the AI, content-hashed (SHA-256) | the AI wrote from *this* data, provably |
| `reports` | every AI output: persona, provider, model, prompt hash, tokens, cost, latency, text, and the **grounding check** result | what the AI said, from what, at what cost, and whether every number in it traces back |

## How the AI is kept honest

1. The evidence pack is built from the twin (and the evaluator's ledger), hashed and stored
   **before** the LLM sees it.
2. The report row references that evidence id and stores the prompt hash.
3. `grounding_check(text, pack)`: every number in the report (decimals, and integers of three or
   more digits) must occur in the pack — as a value or a plain formatting of one. Times, ids and
   small counts are structure and exempt. A report with an unsupported number is stored with
   `grounded = 0` and the offending numbers listed; the Log tab shows it in red.
4. The template renderers pass this check by construction; a test asserts it. When the Claude
   provider is wired, the same check runs on its output — an ungrounded sentence is visible, not
   silent.

## Reproducibility

`Store.replay(run_id)` rebuilds a fresh twin from the stored events and compares its station states
against every stored snapshot. On a 90-minute dark-B3 run: ≥ 97 % agreement, identical exit count,
identical alert sequence. The remaining disagreement is timing quantisation (snapshots are taken at
minute marks between events).

## Operator feedback

Alerts can be **confirmed** or **dismissed** from the station panel with a note; the dismissal
clears the alert, is written to the audit with the actor, and is kept on the twin as feedback for
per-station recalibration (the statistical half of the improvement loop).

## What a real deployment would change

SQLite → the plant historian or a Postgres/Timescale instance; the schema stays. Add row-level
retention policies (IATF 16949 traceability requires that holds and their rationale be retrievable
for the product's life), user identity from the plant SSO for `actor`, and immutable export of the
`audit` and `reports` tables for quality audits.
