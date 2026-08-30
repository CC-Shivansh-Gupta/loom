# Numbers we cite rather than measure

`python -m loom.numbers docs/proposal.md` applies the AI layer's grounding rule to our own prose:
every number in a document must appear in a document a run produced. Some legitimately cannot —
market figures come from published sources, and economic inputs are assumptions the reader is
invited to change. Those are listed here with what justifies them. Nothing else gets a pass.

If a number is in this table it is a **claim about the world**, not a claim about Loom. Every claim
about Loom must be traceable to `benchmark.md`, `baselines.md`, `ablation.md`, `coverage.md` or
`forecaster_tuning.md`.

| number | what it is | source |
|---|---|---|
| 2.3 | $2.3 M — cost of one idle automotive hour | Siemens, *True Cost of Downtime 2024* |
| 1.4 | $1.4 T/yr — unplanned downtime across the Fortune Global 500 | Siemens, *True Cost of Downtime 2024* |
| 8000 | $8,000/min — downtime cost for one line, the `economics:` default | ~1/5 of the Siemens plant-wide figure; stated assumption, editable in the plant file |
| 250 | $/vehicle cost of holding a vehicle | stated assumption (`economics:`) |
| 5000 | $/defect field cost of an escaped defect | stated assumption (`economics:`); the 10x/100x/1000x cost ladder is in `research.md` |
| 60000 | $60,000/yr licence, the cost side of the ROI model | our own pricing assumption |
| 500 | ~$500/station retrofit | low-cost sensing menu, `solution_design.md` C1 |
| 6.3 | mean lead in minutes used in the illustrative ROI arithmetic | derived from `benchmark.md`; recompute when the benchmark changes |
| 3.6 | $3.6 M — output of the bottleneck-avoidance formula | arithmetic on the row's own stated inputs |
| 16949 | IATF 16949 — the automotive quality management standard | standard designation, not a measurement |
| 1000 | the 10x / 100x / 1000x cost ladder for a defect caught downstream, at final assembly, in the field | `research.md`; standard automotive quality figure |
| 95 | ISA-95 — the enterprise/control integration standard | standard designation |
