"""Grounding check, turned on our own prose.

`evidence.grounding_check` asserts that every number an LLM writes appears in
the evidence pack it was given. This module applies exactly the same rule to
the documents *we* write: every number quoted in the proposal must appear in a
document a run produced, or be declared in `docs/exempt_numbers.md` with a
source.

    python -m loom.numbers docs/proposal.md          # exits non-zero on a miss
    python -m loom.numbers --list                    # what the runs support

**What this does not catch.** It checks *presence*, not *correspondence*: a figure that appears
somewhere in a generated document passes, even if the prose has attached it to the wrong row. That
is exactly how a stale calibration table survived a re-run — the new percentages were all present,
just paired with the wrong confidence bins. Presence is a cheap, mechanical guard against the
common failure (a number nothing produced); correspondence still needs a person to read the table
next to the claim.

Rationale: the benchmark numbers were hand-copied into three documents once and
were stale within two commits. A claim nobody can trace is worth nothing to a
panel, and we are in no position to demand grounding from a language model and
not from ourselves.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Documents a run writes. Anything quoted in prose must appear in one of them.
GENERATED = ["docs/benchmark.md", "docs/baselines.md", "docs/ablation.md",
             "docs/coverage.md", "docs/traces.md", "docs/forecaster_tuning.md"]
EXEMPT_DOC = "docs/exempt_numbers.md"

NUM = re.compile(r"\d[\d,]*\.?\d*")
# Structure, not claims: section numbers, small counts, years, percentages of
# the form "5 %" used as a config setting. Mirrors the exemptions in
# evidence.grounding_check.
SMALL = 3               # integers below this many digits are structure
YEARS = range(1990, 2101)


def _norm(tok: str) -> str:
    tok = tok.replace(",", "").rstrip(".")
    if tok.endswith(".0"):
        tok = tok[:-2]
    try:
        f = float(tok)
    except ValueError:
        return tok
    return f"{f:g}"


def harvest(paths: list[str]) -> set[str]:
    out: set[str] = set()
    for p in paths:
        full = os.path.join(ROOT, p)
        if not os.path.exists(full):
            continue
        with open(full) as f:
            text = f.read()
        for tok in NUM.findall(text):
            n = _norm(tok)
            out.add(n)
            # A number quoted to fewer decimals than the run reported is still
            # grounded: 7.04 in the table supports "7.0" and "7" in prose.
            try:
                f_ = float(n)
            except ValueError:
                continue
            out.add(f"{f_:.0f}")
            out.add(f"{f_:.1f}".rstrip("0").rstrip("."))
            out.add(f"{round(f_):g}")
    return out


def exempt() -> dict[str, str]:
    """`docs/exempt_numbers.md`: a markdown table of numbers that are cited
    rather than measured -- market figures, prices, config constants -- each
    with the source that justifies it."""
    out: dict[str, str] = {}
    full = os.path.join(ROOT, EXEMPT_DOC)
    if not os.path.exists(full):
        return out
    with open(full) as f:
        for line in f:
            if not line.startswith("|") or line.startswith("|---"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2 or cells[0].lower() in ("number", "value"):
                continue
            for tok in NUM.findall(cells[0]):
                out[_norm(tok)] = cells[-1]
    return out


def is_structural(n: str) -> bool:
    try:
        f = float(n)
    except ValueError:
        return True
    if f != int(f) or f < 0:
        return False
    i = int(f)
    return len(str(i)) <= SMALL or i in YEARS


def check(path: str, supported: set[str] | None = None) -> list[tuple[str, str]]:
    """Returns the unsupported numbers as (number, the line it appears on)."""
    supported = harvest(GENERATED) if supported is None else supported
    ex = exempt()
    bad: list[tuple[str, str]] = []
    with open(os.path.join(ROOT, path) if not os.path.isabs(path) else path) as f:
        lines = f.readlines()
    in_code = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code or line.lstrip().startswith(">"):
            continue
        # Strip inline code and links -- commands and URLs are not claims.
        clean = re.sub(r"`[^`]*`", " ", line)
        clean = re.sub(r"\]\([^)]*\)", "] ", clean)
        for tok in NUM.findall(clean):
            n = _norm(tok)
            if n in supported or n in ex or is_structural(n):
                continue
            bad.append((n, line.strip()))
    return bad


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("docs", nargs="*", default=["docs/proposal.md"])
    ap.add_argument("--list", action="store_true", help="print what the runs support")
    a = ap.parse_args()
    supported = harvest(GENERATED)
    if a.list:
        print(f"{len(supported)} values supported by {len(GENERATED)} generated documents")
        print(" ".join(sorted(supported, key=lambda x: (len(x), x))))
        return
    fail = 0
    for d in a.docs:
        bad = check(d, supported)
        print(f"{d}: {len(bad)} unsupported")
        seen = set()
        for n, line in bad:
            if n in seen:
                continue
            seen.add(n)
            print(f"   {n:>10}   {line[:96]}")
        fail += len(bad)
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
