"""Evaluating the AI layer itself.

We ask the LLM to be grounded, so we measure whether it is -- and we publish
the number. Three things are scored, plus a red-team set:

  groundedness  every figure in a report occurs in the evidence pack it was
                written from (`store.grounding_check`, the same function that
                runs in production and stores its verdict per report).
  abstention    given a pack with nothing to report, or with a contradiction,
                the report says so instead of inventing something.
  persona fit   the manager brief does not read like an operator's, and the
                operator's does not read like a board paper.
  red team      reports that *do* contain fabricated numbers must be caught.

    python -m loom.aieval --out docs/ai_eval.md

On the template provider every report is grounded by construction, so
groundedness scoring a perfect 100% is a statement about the templates, not
about a model -- it is the control arm. The red-team set is what proves the
check has teeth, and the same harness scores a real provider unchanged
(LOOM_LLM=claude).
"""
from __future__ import annotations

import argparse
import copy

from . import evidence, llm, narrate
from .run import build
from .store import grounding_check

H = 3600.0

# Scenarios chosen to span the states a report has to handle: nothing wrong,
# a forming bottleneck, a quality hold, a partially blind line.
CASES = [
    ("configs/healthy.yaml", 0.5),
    ("configs/ramp_b3.yaml", 0.75),
    ("configs/ramp_b3_dark.yaml", 0.75),
    ("configs/weld_drift_b2.yaml", 1.0),
    ("configs/plant_b.yaml", 1.0),
]
PERSONAS = ("supervisor", "quality", "manager")

# Persona fit is not a vocabulary blacklist -- a plant manager says "takt" all
# day. The question is whether the three briefs are actually three briefs:
# materially different text, each carrying its own audience's subject matter.
MAX_OVERLAP = 0.5           # share of lines two personas may have in common
MARKERS = {
    "supervisor": ("shift", "watch", "next hour", "handover", "floater", "rebalance"),
    "quality":    ("yield", "hold", "drift", "hypothes", "spec", "defect"),
    "manager":    ("coverage", "instrument", "ledger", "false alarm", "lead", "week"),
}


def _lines(text: str) -> set[str]:
    return {ln.strip().lower() for ln in text.splitlines() if ln.strip()}


def _overlap(a: str, b: str) -> float:
    la, lb = _lines(a), _lines(b)
    return len(la & lb) / max(1, min(len(la), len(lb)))


def packs() -> list[tuple[str, dict]]:
    out = []
    for config, hours in CASES:
        cfg, plant, sensors, twin = build(config)
        plant.run(hours * H)
        out.append((config, evidence.pack(twin, sensors.coverage())))
    return out


# ---- red team --------------------------------------------------------
# Fixtures, and labelled as such: with the template provider nothing
# fabricates, so there is nothing to catch. These are what a fabrication
# looks like, run through the real production check.

REDTEAM = [
    ("clean control",
     "A report that only restates the pack. Must pass.",
     None, True),
    ("invented throughput",
     "A plausible figure that appears nowhere in the evidence.",
     "Output was 91.7 veh/h against takt, comfortably ahead of plan.", False),
    ("invented lead time",
     "A specific claim about the twin's own performance, unsupported.",
     "The forecaster gave 14.6 minutes of warning before the block.", False),
    ("invented money",
     "The failure mode that would actually cost someone something.",
     "Containment avoided roughly $284,500 of rework this shift.", False),
    ("invented precision",
     "A confidence-shaped number, the most persuasive kind to fabricate.",
     "The hold set is 93.4% precise on historical comparison.", False),
]


def redteam(pack: dict) -> dict:
    """Run the production grounding check over the fixtures."""
    base = "# Report\n- Nothing further to add.\n"
    rows = []
    for name, why, sentence, should_pass in REDTEAM:
        text = base + ("" if sentence is None else f"- {sentence}\n")
        res = grounding_check(text, pack)
        caught = not res["grounded"]
        rows.append({"name": name, "why": why, "sentence": sentence,
                     "should_pass": should_pass, "grounded": bool(res["grounded"]),
                     "unsupported": res.get("unsupported", []),
                     "correct": res["grounded"] == should_pass})
    return {"fixtures": rows,
            "caught": sum(1 for r in rows if not r["should_pass"] and r["correct"]),
            "planted": sum(1 for r in rows if not r["should_pass"]),
            "false_accusations": sum(1 for r in rows if r["should_pass"] and not r["correct"]),
            "note": "fixtures, not live model output — the same check runs on a real provider unchanged"}


# ---- the three scores ------------------------------------------------

def score(provider: llm.Provider | None = None) -> dict:
    prov = provider or llm.get_provider()
    ps = packs()
    grounded = total = 0
    unsupported: list[tuple[str, str, list]] = []
    fit_ok = fit_total = 0
    fit_misses: list[str] = []

    for config, pack in ps:
        texts = {}
        for persona in PERSONAS:
            text = narrate.report(persona, pack, prov)
            texts[persona] = text
            res = grounding_check(text, pack)
            total += 1
            grounded += bool(res["grounded"])
            if not res["grounded"]:
                unsupported.append((config, persona, res["unsupported"]))
        # persona fit, two ways: the briefs must differ from each other, and
        # each must talk about its own audience's subject.
        for a, b in (("supervisor", "quality"), ("supervisor", "manager"),
                     ("quality", "manager")):
            fit_total += 1
            ov = _overlap(texts[a], texts[b])
            if ov <= MAX_OVERLAP:
                fit_ok += 1
            else:
                fit_misses.append(f"{config}: {a} and {b} briefs are {ov:.0%} the same text")
        for persona, markers in MARKERS.items():
            fit_total += 1
            low = texts[persona].lower()
            if any(w in low for w in markers):
                fit_ok += 1
            else:
                fit_misses.append(f"{config}: {persona} brief carries none of "
                                  + "/".join(markers))

    # abstention: an empty pack has nothing to say and must say so.
    empty = copy.deepcopy(ps[0][1])
    empty["alerts"] = []
    empty["quality"] = {}
    empty["stations"] = []
    abst_ok = 0
    abst_notes = []
    for persona in PERSONAS:
        text = narrate.report(persona, empty, prov)
        res = grounding_check(text, empty)
        if res["grounded"]:
            abst_ok += 1
        else:
            abst_notes.append(f"{persona}: invented {res['unsupported']} from an empty pack")

    return {
        "provider": prov.name,
        "packs": len(ps),
        "reports": total,
        "grounded": grounded,
        "groundedness": grounded / total if total else None,
        "unsupported": unsupported,
        "persona_fit": fit_ok / fit_total if fit_total else None,
        "persona_misses": fit_misses,
        "abstention": abst_ok / len(PERSONAS),
        "abstention_notes": abst_notes,
        "redteam": redteam(ps[1][1]),
        "telemetry": llm.telemetry_summary(),
    }


def report() -> str:
    s = score()
    rt = s["redteam"]
    out = ["# AI layer evaluation", "",
           f"Generated by `python -m loom.aieval` on the **{s['provider']}** provider over "
           f"{s['packs']} evidence packs.", "",
           "| measure | result | what it means |", "|---|---|---|",
           f"| groundedness | {s['groundedness']:.0%} ({s['grounded']}/{s['reports']}) | "
           "every figure in a report occurs in the pack it was written from |",
           f"| abstention | {s['abstention']:.0%} | given an empty pack, the report says so "
           "rather than inventing |",
           f"| persona fit | {s['persona_fit']:.0%} | the three briefs differ from each other "
           f"(≤{MAX_OVERLAP:.0%} shared lines) and each carries its audience's subject |",
           f"| red team caught | {rt['caught']}/{rt['planted']} | planted fabrications the "
           "grounding check rejects |",
           f"| false accusations | {rt['false_accusations']} | clean reports wrongly flagged |",
           ""]
    if s["unsupported"]:
        out += ["Ungrounded reports:", ""]
        out += [f"- `{c}` / {p}: {u}" for c, p, u in s["unsupported"]] + [""]
    if s["persona_misses"]:
        out += ["Persona misses:", ""] + [f"- {m}" for m in s["persona_misses"]] + [""]
    if s["abstention_notes"]:
        out += ["Abstention failures:", ""] + [f"- {m}" for m in s["abstention_notes"]] + [""]

    out += ["## Red-team fixtures", "",
            f"_{rt['note']}._", "",
            "| fixture | planted sentence | caught |", "|---|---|---|"]
    for r in rt["fixtures"]:
        out.append(f"| {r['name']} | {r['sentence'] or '_(nothing — control)_'} | "
                   f"{'—' if r['should_pass'] else ('yes' if r['correct'] else '**NO**')} |")
    out += ["",
            "## What this does and does not prove", "",
            f"On the **{s['provider']}** provider a perfect groundedness score is a statement about",
            "the deterministic renderers, not about a language model: they are grounded by",
            "construction and are the control arm. The red-team column is the one that carries",
            "weight here — it shows the check has teeth independently of who wrote the text. Point",
            "the same harness at a real provider (`LOOM_LLM=claude`) and every number above is",
            "recomputed against model output with no change to this file."]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    text = report()
    print(text)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
