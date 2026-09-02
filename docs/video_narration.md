# Demo video — narration

Voice-over for `video/loom_demo.mp4`. The video is silent; every line below is
timed to the cut, and the on-screen caption for each beat is printed under it so
you can see what the viewer is reading while you speak.

Read at a normal presenting pace (~155 words per minute). Each window has a
second or two of slack at the end — land the line early rather than run into the
next cut. **Beat 7 is the hero.** If anything gets cut for time, keep it.

Total run time **3:03.65**.

| beat | in | out | you say |
|---|---|---|---|
| **title card** | 0:00.00 | 0:11.27 | Most assembly lines are partly blind. Some stations stream telemetry; others still run on a clipboard. Every digital twin on the market assumes they don't. |
| **1** | 0:11.27 | 0:27.37 | This is a live twin of a twelve-station line. Front lane is the plant. Behind it is what Loom believes. And Loom is saying nothing — which is the hard part. Our false-alarm floor is nought point three per eight hours. |
| **2** | 0:27.37 | 0:38.19 | Every value carries where it came from — measured, inferred, or simulated. No other twin tells an operator whether they're reading a measurement or a guess. |
| **3** | 0:38.19 | 0:46.72 | Now B3 starts wearing: fifty-six seconds drifting to seventy-eight. Nothing here is precomputed — the plant and the twin are both running right now. |
| **4** | 0:46.72 | 1:01.40 | Loom warns before the block. It fits the measured cycles, then simulates the buffer forward. The ETA is marked simulated — because it is. |
| **5** | 1:01.40 | 1:13.61 | And it scores itself against what actually happened. Warned here, blocked there. That lead time goes in the ledger whether it flatters us or not. |
| **6** | 1:13.61 | 1:24.14 | Same fault. But now B3 is dark — it reports nothing at all. A threshold alarm has nothing to threshold. It never fires. |
| **7** | 1:24.14 | 1:41.49 | Loom still warns. Six minutes of lead, reconstructed from B2 and B4 alone — the stations either side of the blind one. This is the whole thesis. The plant everybody actually has is partly blind, and that is the plant we built for. |
| **8** | 1:41.49 | 1:51.26 | Statistics and simulation produce every number. The model turns numbers into decisions — it writes the briefings from that evidence pack, and nothing else. |
| **9** | 1:51.26 | 2:02.61 | And if it ever reaches for a number the evidence doesn't contain, the grounding check catches it. That is running live, on this shift — not a stored result. |
| **10** | 2:02.61 | 2:12.96 | Even its own tuning goes through a gate it cannot bypass. Changes that looked better on lead time — refused, for breaking the false-alarm budget. |
| **11** | 2:12.96 | 2:21.40 | A different failure. B2's weld current sags out of spec. No cycle-time symptom at all — the line looks perfectly healthy. |
| **12** | 2:21.40 | 2:35.19 | CUSUM catches it at source and opens a targeted hold — twelve point eight minutes before end-of-line inspection sees the first weak weld. Seventy-seven vehicles held, instead of a blanket ninety. |
| **13** | 2:35.19 | 2:42.97 | The business case is computed from measured performance, with every assumption printed on the page. |
| **14** | 2:42.97 | 2:53.38 | And a different plant — thirty stations, four zones, mixed sensor maturity — runs on the same code, unchanged. A new line is a file. |
| **end card** | 2:53.38 | 3:03.65 | One line. One shadow period. Four weeks. That's the ask. |

## What is on screen

The captions below are burned into the video — the viewer reads them while you
speak, so do not read them aloud.

| beat | on-screen caption |
|---|---|
| 1 | A healthy line: 12 stations, real cycle noise, two model variants. The front lane is the plant; the lane behind it is what Loom believes. Loom is raising nothing — and that is the hard part. |
| 2 | Every value carries its provenance: ● measured, ◐ inferred, ○ simulated. Nobody else shows an operator whether they are reading a measurement or a guess. |
| 3 | Now B3 starts wearing — 56 s drifting to 78 s over 20 minutes. Nothing here is precomputed: the plant and the twin are both running, right now. |
| 4 | Loom warns before the block. The forecaster fits the measured cycles, then simulates the buffer forward — and the ETA is marked ○ simulated, because it is. |
| 5 | And it scores itself against what actually happened. That lead time goes in the ledger whether it flatters us or not. |
| 6 | THE SAME FAULT, WITH B3 DARK. B3 now reports nothing at all. A threshold alarm has nothing to threshold and never fires. |
| 7 | Loom still warns — 6.0 minutes of lead, reconstructed from B2 and B4 alone. This is the whole thesis: the plant everybody actually has is partly blind. |
| 8 | Statistics and simulation produce every number. The model turns numbers into decisions, and writes the briefings from that evidence pack and nothing else. |
| 9 | And if it ever reaches for a number the evidence does not contain, the grounding check catches it. Live, on this shift: 4/4 caught, 0 clean reports wrongly flagged. |
| 10 | Even its own tuning goes through a gate it cannot bypass — changes that looked better on lead time, refused for breaking the false-alarm budget. |
| 11 | A different failure: B2's weld current sags out of spec. No cycle-time symptom at all — the line looks perfectly healthy. |
| 12 | CUSUM catches it at source and opens a targeted hold — 12.8 minutes before end-of-line inspection sees the first weak weld. 77 vehicles held instead of a blanket 90. |
| 13 | The business case is computed from measured performance, with every assumption printed. |
| 14 | And a different plant — 30 stations, 4 zones, mixed sensor maturity — runs on the same code, unchanged. A new line is a file. |

## Notes

- **Every number spoken is in `docs/benchmark.md` or the run documents beside it.**
  `python -m loom.numbers` enforces that for the written proposal; the same figures are used here.
- **Do not narrate over the limits.** What we do not claim is worth more said in the room.
- If asked *"is this simulated?"* — yes, the plant is a simulator, and that is the point:
  it gives ground truth the twin never sees, which is what makes the scoring honest.
  The twin only ever receives the sensor-filtered event stream.
- **Know this before beat 9.** The red-team panel runs the grounding check against *this
  shift's* evidence pack, so its score is live, not fixed. It holds at 4/4 on the dark-station
  run, which is what the video shows. On a long `weld_drift_b2` shift it falls to 1/4: the
  check tests whether a figure occurs in the pack, and after two hours a pack of that size
  happens to contain 91.7, 14.6 and 93.4. If a judge re-runs it late in a shift and sees a
  lower number, that is the honest answer — presence-checking is weaker the more numbers the
  pack holds. The fixed **4/4 over 5 controlled packs** in `docs/ai_eval.md` is the claim to
  quote; the panel is the live demonstration that the check has teeth.
