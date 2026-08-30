"""Parameter sweep: where the forecaster's thresholds come from.

The four numbers the forecaster runs on (`window`, `min_tstat`, `min_over_z`,
`raise_after`) were chosen from a sweep whose script was never committed, so
the table justifying them could not be re-run. This is that script.

    python -m loom.sweep --seeds 5 --out docs/forecaster_tuning.md

The grid is deliberately *axial*, not a full factorial: from the chosen
defaults, one parameter is varied at a time. Three anchor rows come first --
the naive trend test (short window, low t, no standard-error test, no
persistence rule) and that same corner with each of the two fixes put back --
because they are the point of the whole table: the alert-fatigue failure mode
the brief warns about, measured rather than asserted, and then what each fix
does about it. Sixteen combinations, a couple of minutes at 5 seeds.

Both criteria come from `harness.evaluate`, which is the same gate every
proposed change goes through: false alarms per 8 h on healthy shifts, and lead
time on the injected B3 ramp (instrumented and dark).
"""
from __future__ import annotations

import argparse
import textwrap
from dataclasses import dataclass, replace

from . import harness

# The alert budget the proposal publishes. A combination above it is not a
# candidate however good its lead time looks.
FA_BUDGET = 0.2

NAIVE = {"window": 10, "min_tstat": 3.0, "min_over_z": 0.0, "raise_after": 1}
# The naive corner with one fix put back, so each fix can be read on its own
# rather than only in combination.
NAIVE_PLUS = {"naive+se": {"min_over_z": 2.0}, "naive+persistence": {"raise_after": 3}}
AXES = {
    "window": (10, 15, 20, 25),
    "min_tstat": (3.0, 4.0, 5.0, 6.0),
    "min_over_z": (0.0, 1.0, 2.0, 3.0),
    "raise_after": (1, 2, 3, 4),
}


@dataclass
class Row:
    params: dict
    axis: str                 # which parameter this row varies ("naive" / "defaults" for the anchors)
    fa_per_8h: float
    mean_lead_min: float | None
    leads_min: list[float]
    misses: int

    @property
    def lead_range(self) -> str:
        """The spread across seeds, not just the mean -- a combination whose
        worst seed warns two minutes before the block is not the same offer as
        one that never drops below six."""
        if not self.leads_min:
            return "\u2014"
        return f"{min(self.leads_min):.1f}\u2013{max(self.leads_min):.1f}"

    @property
    def is_default(self) -> bool:
        return all(self.params[k] == v for k, v in harness.DEFAULT_PARAMS.items())


def _para(text: str, indent: str = "") -> str:
    """Wrap generated prose so the emitted markdown is readable as source."""
    return textwrap.fill(text, 96, subsequent_indent=indent)


def grid() -> list[tuple[str, dict]]:
    """The combinations the sweep explores, in table order.

    Deduplicated: the axial rows that coincide with the defaults collapse into
    the single `defaults` row, so no combination is measured twice.
    """
    out: list[tuple[str, dict]] = [("naive", dict(NAIVE))]
    out += [(k, {**NAIVE, **over}) for k, over in NAIVE_PLUS.items()]
    out.append(("defaults", dict(harness.DEFAULT_PARAMS)))
    seen = {tuple(sorted(p.items())) for _, p in out}
    for axis, values in AXES.items():
        for v in values:
            params = {**harness.DEFAULT_PARAMS, axis: v}
            key = tuple(sorted(params.items()))
            if key not in seen:
                seen.add(key)
                out.append((axis, params))
    return out


def scenarios(seeds: int):
    return tuple(replace(sc, seeds=tuple(range(seeds))) for sc in harness.DEFAULT_SCENARIOS)


def measure(seeds: int) -> list[Row]:
    scs = scenarios(seeds)
    rows = []
    for axis, params in grid():
        r = harness.evaluate(params, scs)
        rows.append(Row(r.params, axis, r.fa_per_8h, r.mean_lead_min, list(r.leads_min), r.misses))
    return rows


def choose(rows: list[Row]) -> Row:
    """The selection rule, stated so it can be argued with: stay inside the
    false-alarm budget, miss nothing, then take the longest mean warning."""
    ok = [r for r in rows if r.fa_per_8h <= FA_BUDGET and r.misses == 0 and r.mean_lead_min is not None]
    pool = ok or [r for r in rows if r.mean_lead_min is not None] or rows
    return max(pool, key=lambda r: (-r.fa_per_8h, r.mean_lead_min or 0.0))


def report(seeds: int, rows: list[Row] | None = None) -> str:
    rows = rows if rows is not None else measure(seeds)
    best = choose(rows)
    naive = next(r for r in rows if r.axis == "naive")
    defaults = next(r for r in rows if r.axis == "defaults")
    plus_se = next(r for r in rows if r.axis == "naive+se")
    plus_persist = next(r for r in rows if r.axis == "naive+persistence")
    no_se = next(r for r in rows if r.axis == "min_over_z" and r.params["min_over_z"] == 0.0)
    no_persist = next(r for r in rows if r.axis == "raise_after" and r.params["raise_after"] == 1)

    def f(x, s="{:.1f}") -> str:
        return "—" if x is None else s.format(x)

    out = ["# Forecaster tuning — where the four thresholds come from",
           "",
           f"Generated by `python -m loom.sweep --seeds {seeds}`. No hand-edited numbers.",
           "",
           "Two criteria, both from `harness.evaluate` — the same gate every proposed change goes",
           "through:",
           "",
           "- **FA/8 h**: alerts raised on `configs/healthy.yaml` over 8 h of healthy line — must sit",
           f"  inside the published budget of {FA_BUDGET} per 8 h.",
           "- **lead**: minutes between the first alert on B3 and the upstream station actually",
           "  blocking, on `configs/ramp_b3.yaml` and `configs/ramp_b3_dark.yaml`.",
           "",
           "The grid is axial: from the defaults, one parameter moves at a time. The first three rows",
           "are anchors — the naive trend test (short window, t ≥ 3, no standard-error test, no",
           "persistence rule), then that same corner with each fix put back on its own.",
           "",
           "| varies | window | min_tstat | min_over_z | raise_after | FA/8 h | mean lead (min) | lead range (min) | missed |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        p = r.params
        label = {"naive": "naive trend test", "defaults": "**chosen defaults**",
                 "naive+se": "naive + standard-error test",
                 "naive+persistence": "naive + persistence rule"}.get(r.axis, r.axis)
        cells = [label, f"{p['window']}", f"{p['min_tstat']:.0f}", f"{p['min_over_z']:.0f}",
                 f"{p['raise_after']}", f"{r.fa_per_8h:.1f}", f(r.mean_lead_min), r.lead_range,
                 f"{r.misses}"]
        if r.is_default:
            cells = [c if c.startswith("**") else f"**{c}**" for c in cells]
        out.append("| " + " | ".join(cells) + " |")

    out += ["",
            "## What the table says",
            ""]
    out.append(_para(
        f"**The naive trend test is the alert-fatigue failure mode.** It raises "
        f"{naive.fa_per_8h:.1f} false alarms per 8 h on a line where nothing is wrong, against a "
        f"budget of {FA_BUDGET}. Thousands of significance tests per shift means t \u2265 3 fires by "
        f"chance many times a shift, and a supervisor stops reading the panel long before the one "
        f"real alert arrives \u2014 a forecaster tuned this way is worse than no forecaster."))
    out += ["", _para(
        "Two mechanisms separate that corner from the shipped defaults. Rows 2 and 3 put each one "
        "back on its own; the axial rows below take each one away from the defaults."), ""]
    # Neither the direction nor the axis of each fix's effect is safe to assert
    # in advance -- the standard-error test can *raise* the raw alert count from
    # the naive corner, because it also makes the forecaster willing to fire
    # earlier. Report what the run produced, on both axes.
    if plus_se.fa_per_8h < naive.fa_per_8h:
        se_alone = (f"Added to the naive corner alone it takes false alarms from "
                    f"{naive.fa_per_8h:.1f} to {plus_se.fa_per_8h:.1f} per 8 h.")
    else:
        se_alone = (f"On its own it does not quieten the naive corner at all "
                    f"({naive.fa_per_8h:.1f} \u2192 {plus_se.fa_per_8h:.1f} per 8 h): with no "
                    f"persistence rule behind it, noise clears any single-cycle test. Its work "
                    f"shows once the line is quiet enough for one alert to matter.")
    se_cost = []
    if no_se.fa_per_8h > defaults.fa_per_8h:
        se_cost.append(f"false alarms rise to {no_se.fa_per_8h:.1f} per 8 h against "
                       f"{defaults.fa_per_8h:.1f}")
    if (no_se.mean_lead_min or 0) < (defaults.mean_lead_min or 0):
        se_cost.append(f"mean lead falls to {f(no_se.mean_lead_min)} min against "
                       f"{f(defaults.mean_lead_min)}")
    if no_se.misses > defaults.misses:
        se_cost.append(f"{no_se.misses} injected faults go unwarned")
    out.append(_para(
        f"1. **The standard-error test.** \"Already over takt\" has to mean the fitted cycle sits "
        f"`min_over_z` standard errors above takt, not a raw comparison. {se_alone} Removed from "
        f"the defaults (`min_over_z = 0`), "
        + ("; ".join(se_cost) if se_cost else "nothing measurably changes on this grid")
        + ".", indent="   "))
    out.append(_para(
        f"2. **The persistence rule.** The condition must hold on "
        f"{harness.DEFAULT_PARAMS['raise_after']} consecutive cycles before an alert is raised. "
        f"Added to the naive corner alone it takes false alarms from {naive.fa_per_8h:.1f} to "
        f"{plus_persist.fa_per_8h:.1f} per 8 h. Removed from the defaults (`raise_after = 1`) they "
        f"are {no_persist.fa_per_8h:.1f} per 8 h for {f(no_persist.mean_lead_min)} min of mean lead "
        f"against {f(defaults.mean_lead_min)} \u2014 which is the trade the rule exists to make.",
        indent="   "))
    out += ["",
            "Everything else is second-order: a longer `window` buys a steadier fit and a little lead,",
            "a higher `min_tstat` trades lead for quiet, and neither changes the shape of the result.",
            "",
            "## Chosen",
            "",
            "Selection rule: stay inside the false-alarm budget, miss no fault, then take the longest",
            "mean warning.",
            "",
            f"- Sweep picks: window {best.params['window']}, min_tstat {best.params['min_tstat']:.0f}, "
            f"min_over_z {best.params['min_over_z']:.0f}, raise_after {best.params['raise_after']} "
            f"({best.fa_per_8h:.1f} FA/8 h, {f(best.mean_lead_min)} min mean lead).",
            f"- Shipped defaults (`harness.DEFAULT_PARAMS`): window {harness.DEFAULT_PARAMS['window']}, "
            f"min_tstat {harness.DEFAULT_PARAMS['min_tstat']:.0f}, "
            f"min_over_z {harness.DEFAULT_PARAMS['min_over_z']:.0f}, "
            f"raise_after {harness.DEFAULT_PARAMS['raise_after']} "
            f"({defaults.fa_per_8h:.1f} FA/8 h, {f(defaults.mean_lead_min)} min mean lead)."]
    if best.is_default:
        out.append("")
        out.append("The sweep re-selects the shipped defaults.")
    else:
        out += ["", _para(
            "**These disagree.** The sweep's pick is recorded as it came out rather than edited to "
            "match the shipped values; the defaults change only through `improve.py`'s gate, so a "
            "disagreement here is a finding to act on, not a number to overwrite.")]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    text = report(a.seeds)
    print(text)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
