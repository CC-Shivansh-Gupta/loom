# Demo video — script and shot list

Three minutes, recorded from the control room's **▶ Story** button so it can be re-cut in ten
minutes after any code change. Story mode drives the same public API a person would click, so this
is a recording of the real product, not a mock-up.

Run it offline on the template provider with the recorded replay page open in a second tab: assume
the venue network fails, because it will.

| # | on screen | said over it | ~s |
|---|---|---|---|
| 1 | Title, then the healthy line running | "Most assembly lines are partly blind — some stations stream full telemetry, others run on a clipboard. Every twin on the market assumes they are not." | 15 |
| 2 | Healthy line, nothing raised; hover a station to show ● ◐ ○ | "This is a healthy line, and Loom is saying nothing. That is the hard part — a published false-alarm floor of 0.3 per eight hours. Every value carries whether it was measured, inferred, or simulated." | 20 |
| 3 | Load `ramp_b3`, B3 begins to wear; Floor tab | "Now B3 starts wearing. Fifty-six seconds drifting to seventy-eight." | 12 |
| 4 | The alert fires; station panel showing the fit and the ETA | "Loom warns before the block — it fits the measured cycles and simulates the buffer forward. The ETA is marked simulated, because it is." | 18 |
| 5 | Mgr tab, the prediction scored against what happened | "And it scores itself. Warned here, blocked there — that goes in the ledger whether it flatters us or not." | 15 |
| 6 | **Load `ramp_b3_dark`. B3 goes dark in the scene.** | "Same fault. Now B3 reports nothing at all. A threshold alarm has nothing to threshold — it never fires." | 15 |
| 7 | **The alert fires anyway** — hold on the belief lane | "Loom still warns. Six minutes of lead, reconstructed from its neighbours alone. This is the whole thesis: the plant everybody actually has is partly blind." | 20 |
| 8 | Load `weld_drift_b2`; Quality tab; the hold opens | "A different failure — weld current sagging out of spec, no cycle-time symptom, the line looks perfectly healthy. CUSUM catches it at source and holds the exact vehicles at risk, minutes before end-of-line inspection sees the first one." | 25 |
| 9 | AI tab: a briefing, then the what-if ranking | "The AI writes the briefings from that evidence and nothing else, and proposes what to try — but the simulator ranks it, not the model." | 18 |
| 10 | **Red-team panel: the grounding check flags a fabricated number red** | "And if it ever reaches for a number the evidence does not contain, the system catches it. Four out of four, no false accusations." | 15 |
| 11 | Improve loop: the gate refusing a proposal | "Even its own tuning goes through a gate it cannot bypass. Two changes that looked better on lead time were refused for breaking the false-alarm budget." | 15 |
| 12 | Exec tab, then `plant_b` loading unchanged | "The business case is computed from measured performance with every input printed. And a different thirty-station plant runs on the same code — a new line is a file." | 20 |

**Hero shot is 6–7.** If only twenty seconds survive the edit, keep those: the truth lane shows B3
wearing, the belief lane has no data for B3 at all, and the warning fires anyway.

**Do not** narrate over the limits slide — say those in the room, where they are worth more.
