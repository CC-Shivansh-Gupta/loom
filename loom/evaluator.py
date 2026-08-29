"""Layer 4: scores the twin against ground truth.

The only module allowed to see both the plant and the twin.
"""
from __future__ import annotations

from .plant import Plant
from .twin import Twin


def state_agreement(plant: Plant, twin: Twin) -> dict:
    truth = plant.truth()
    belief = twin.snapshot()
    mismatches = []
    for sid, ts in truth["stations"].items():
        bs = belief["stations"][sid]
        if ts != bs:
            mismatches.append((sid, "station", ts, bs))
        if len(truth["buffers"][sid]) != belief["buffer_counts"][sid]:
            mismatches.append((sid, "buffer", len(truth["buffers"][sid]),
                               belief["buffer_counts"][sid]))
    return {"checked": 2 * len(truth["stations"]), "mismatches": mismatches}
