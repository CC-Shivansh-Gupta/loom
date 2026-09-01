"""Grounding check, turned on our own prose.

`evidence.grounding_check` asserts that every number an LLM writes appears in
the evidence pack it was given. This module applies exactly the same rule to
the documents *we* write: every number quoted in the proposal must appear in a
document a run produced, or be declared in `docs/exempt_numbers.md` with a
source.

    python -m loom.numbers docs/proposal.md          # exits non-zero on a miss
    python -m loom.numbers --list                    # what the runs support
    python -m loom.numbers --presence-only           # skip the correspondence pass

Two checks, because a number can be wrong in two different ways.

**Presence** (`check`): the figure exists somewhere in a document a run produced. Catches the
common failure — a number nothing measured.

**Correspondence** (`correspondence`): the figure exists *in a row that is about the thing the
claim is about*. Presence alone let a stale calibration table survive a re-run: every new
percentage was present in the document, just paired with the wrong confidence bin, so nothing
fired. The rule is that a number must appear in a generated row whose own label or column header
shares a discriminating word with the sentence quoting it — "discriminating" meaning a word that
does not appear on most generated lines, since a word everything shares anchors nothing.

Correspondence is a heuristic and says so: it can be satisfied by a coincidence, and prose that
describes a row in entirely different words than the row uses will fail it honestly. Both
outcomes are cheap to inspect, which is the point — it converts "someone must re-read every table
before every commit" into "read the three lines the tool flagged".

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
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Documents a run writes. Anything quoted in prose must appear in one of them.
# benchmark.json is here because it is a run product like the rest, and because
# the Exec view reads it: the money in the proposal and the money on the screen
# have to come from one file or a judge can catch them disagreeing.
GENERATED = ["docs/benchmark.md", "docs/benchmark.json", "docs/baselines.md",
             "docs/ablation.md", "docs/coverage.md", "docs/traces.md",
             "docs/forecaster_tuning.md", "docs/ai_eval.md"]
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


def _variants(tok: str) -> set[str]:
    """A number quoted to fewer decimals than the run reported is still
    grounded: 7.04 in the table supports "7.0" and "7" in prose."""
    n = _norm(tok)
    out = {n}
    try:
        f_ = float(n)
    except ValueError:
        return out
    out.add(f"{f_:.0f}")
    out.add(f"{f_:.1f}".rstrip("0").rstrip("."))
    out.add(f"{round(f_):g}")
    return out


def harvest(paths: list[str]) -> set[str]:
    out: set[str] = set()
    for p in paths:
        full = os.path.join(ROOT, p)
        if not os.path.exists(full):
            continue
        with open(full) as f:
            text = f.read()
        for tok in NUM.findall(text):
            out |= _variants(tok)
    return out


# -- correspondence -----------------------------------------------------------
# Presence asks "did a run produce this number?". Correspondence asks "did a run
# produce it *for this claim*?" -- the question a stale table passes and a reader
# has to ask by hand.

WORD = re.compile(r"[a-z][a-z0-9_]{1,}")
SPLIT = re.compile(r"[_./-]")


def _words(text: str) -> frozenset[str]:
    """Words, plus the parts of compound identifiers. `configs/ramp_b3.yaml` is
    the row label and "an instrumented ramp" is the prose; they are about the
    same thing and only meet if the identifier also yields `ramp`."""
    out: set[str] = set()
    for w in WORD.findall(text.lower()):
        out.add(w)
        out.update(x for x in SPLIT.split(w) if len(x) > 1)
    return frozenset(out)
# A word on more than this share of generated contexts anchors nothing. The bar
# is low on purpose. An anchor has to identify *which row*, and a column header
# like "lead" or "min" is on every scenario's row, so it pins the column and
# leaves the row free -- which is the exact mistake the check exists to catch.
# What survives is identity: scenario names, mechanism names, parameter names.
COMMON_SHARE = 0.10


@dataclass(frozen=True)
class Occurrence:
    """Where a number lives in a generated document, and what it is about."""
    doc: str
    context: str                # row label + column header, or the whole line
    line: str                   # the row as written, for the failure message

    @property
    def tokens(self) -> frozenset[str]:
        return _words(self.context)

    @property
    def numbers(self) -> frozenset[str]:
        """Every number on the row, in all the roundings prose may quote it to.
        A row is identified by the set of figures it reports together."""
        out: set[str] = set()
        for tok in NUM.findall(self.line):
            out |= _variants(tok)
        return frozenset(out)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_rule(line: str) -> bool:
    return bool(re.match(r"^\|[\s:|-]+\|?\s*$", line.strip()))


def index_generated(paths: list[str] | None = None) -> tuple[dict[str, list[Occurrence]], set[str]]:
    """Map every number in the generated documents to the rows it appears in,
    and work out which words are too common to anchor anything.

    A markdown table row gives a number two labels for free -- the row's first
    cell and its column's header -- and those are exactly what a claim has to
    match. Outside a table there is no such structure, so the line itself is the
    context and the check is correspondingly weaker.
    """
    paths = GENERATED if paths is None else paths
    idx: dict[str, list[Occurrence]] = {}
    line_tokens: list[frozenset[str]] = []
    for p in paths:
        full = os.path.join(ROOT, p)
        if not os.path.exists(full):
            continue
        with open(full) as f:
            lines = f.readlines()
        header: list[str] = []
        for i, raw in enumerate(lines):
            line = raw.rstrip("\n")
            if not line.strip():
                header = []
                continue
            if _is_rule(line):
                continue
            if line.lstrip().startswith("|"):
                cells = _cells(line)
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                if _is_rule(nxt):
                    header = cells
                    continue
                # Everything on the row that is not a figure is a label for
                # the figures that are: the scenario in the first cell, but also
                # the comparator or mechanism named in the second.
                label = " ".join(c for c in cells if not NUM.fullmatch(c.strip("*` ")))
                for j, cell in enumerate(cells):
                    col = header[j] if j < len(header) else ""
                    ctx = f"{label} {col}"
                    for tok in NUM.findall(cell):
                        occ = Occurrence(p, ctx, line.strip())
                        for v in _variants(tok):
                            idx.setdefault(v, []).append(occ)
                    line_tokens.append(_words(ctx))
                continue
            ctx = line
            for tok in NUM.findall(line):
                occ = Occurrence(p, ctx, line.strip())
                for v in _variants(tok):
                    idx.setdefault(v, []).append(occ)
            line_tokens.append(_words(ctx))

    n = len(line_tokens) or 1
    counts: dict[str, int] = {}
    for ts in line_tokens:
        for t in ts:
            counts[t] = counts.get(t, 0) + 1
    common = {t for t, c in counts.items() if c / n > COMMON_SHARE}
    return idx, common


def correspondence(path: str, idx=None, common=None) -> list[tuple[str, str, str]]:
    """Numbers that a run produced, but not for the claim quoting them.

    Returns (number, the prose line, where the number actually lives). Numbers
    no run produced at all are `check`'s business and are skipped here, so the
    two checks report each failure once.
    """
    if idx is None or common is None:
        idx, common = index_generated()
    ex = exempt()
    bad: list[tuple[str, str, str]] = []
    for line, clean, anchor in _prose_lines(path):
        claim = _words(anchor) - common
        nums = {_norm(t) for t in NUM.findall(clean)}
        for m in NUM.finditer(clean):
            n = _norm(m.group())
            if n in ex:
                continue
            if is_structural(n) and not _pct_at(clean, m.end()):
                continue
            occs = idx.get(n)
            if not occs:
                continue                        # ungrounded: check() reports it
            # Anchored by the company it keeps. "0.5-0.7 -> 59 %" is a claim
            # about three numbers at once, and the row that produced it holds
            # all three; when the re-run made it 54 %, no row held 0.7 and 59
            # together any more. This is the strong form of the check, because
            # a claim's own numbers identify its row without the prose having
            # to repeat the row's wording.
            if any(o.numbers & (nums - {n}) for o in occs):
                continue
            # ...or by naming what it is about, for a claim carrying one number.
            if any(o.tokens & claim for o in occs):
                continue
            where = "; ".join(dict.fromkeys(o.line[:60] for o in occs[:2]))
            bad.append((n, line.strip(), where))
    return bad


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


PCT = re.compile(r"\s*%")


def _pct_at(text: str, end: int) -> bool:
    """Is the number that ends here a percentage? A bare small integer is
    structure -- a section number, twelve stations, three zones -- but the same
    integer with a percent sign after it is a measurement, and the exemption
    that keeps section numbers quiet was hiding every rate the documents quote.
    That is not a corner case: hit rates, precision, recall and yield are the
    claims most likely to go stale, and they are almost all two digits."""
    return bool(PCT.match(text, end))


def is_structural(n: str) -> bool:
    try:
        f = float(n)
    except ValueError:
        return True
    if f != int(f) or f < 0:
        return False
    i = int(f)
    return len(str(i)) <= SMALL or i in YEARS


def _html_text(lines: list[str]) -> list[str]:
    """The prose of an HTML page, with the machinery removed.

    The deck is the document most likely to be read by a judge and the one
    furthest from the code, so it is the one most likely to drift -- but a naive
    read of it drowns in colours and font sizes. Style and script blocks go
    entirely, then tags, leaving the text a reader sees. One source line stays
    one line, which keeps a `<tr>` a row.
    """
    out, skip = [], False
    for line in lines:
        low = line.lower()
        if "<style" in low or "<script" in low:
            skip = True
        if skip:
            if "</style>" in low or "</script>" in low:
                skip = False
            out.append("")
            continue
        text = re.sub(r"<!--.*?-->", " ", line)
        text = re.sub(r"<[^>]*>", " ", text)
        out.append(text)
    return out


def _prose_lines(path: str):
    """The lines of a document that make claims, as (raw, numbers-text,
    anchor-text).

    Code blocks are commands, block quotes are somebody else's words, and link
    targets are URLs -- none of them are ours to ground, so numbers are read
    from the line with those removed. *Anchoring* reads a wider and less
    censored text, for two reasons a single stripped line gets wrong. Markdown
    hard-wraps, so "the pair overtakes at 0.67" and the sentence naming the pair
    are different lines of one claim; and the word that identifies what a claim
    is about is very often inside the inline code -- `B4.torque`, `shifting`,
    `configs/healthy.yaml` -- so stripping it removes the anchor and keeps the
    number. A table row anchors on itself plus its header, never on its
    neighbours: rows of one table are separate claims and must not vouch for
    each other.
    """
    with open(os.path.join(ROOT, path) if not os.path.isabs(path) else path) as f:
        lines = f.readlines()
    if path.endswith(".html"):
        lines = _html_text(lines)
    blocks: list[list[str]] = []
    cur: list[str] = []
    in_code = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_code = not in_code
            cur.append("")
            continue
        if in_code or line.lstrip().startswith(">"):
            cur.append("")
            continue
        if not line.strip():
            blocks.append(cur)
            cur = []
        cur.append(line)
    blocks.append(cur)

    for block in blocks:
        real = [x for x in block if x.strip()]
        para = " ".join(real)
        head = next((x for x in real if x.lstrip().startswith("|")), "")
        for line in block:
            if not line.strip():
                continue
            clean = re.sub(r"`[^`]*`", " ", line)
            clean = re.sub(r"\]\([^)]*\)", "] ", clean)
            anchor = line + " " + (head if line.lstrip().startswith("|") else para)
            anchor = re.sub(r"\]\([^)]*\)", "] ", anchor).replace("`", " ")
            yield line, clean, anchor


def check(path: str, supported: set[str] | None = None) -> list[tuple[str, str]]:
    """Returns the unsupported numbers as (number, the line it appears on)."""
    supported = harvest(GENERATED) if supported is None else supported
    ex = exempt()
    bad: list[tuple[str, str]] = []
    for line, clean, _ in _prose_lines(path):
        for m in NUM.finditer(clean):
            n = _norm(m.group())
            if n in supported or n in ex:
                continue
            if is_structural(n) and not _pct_at(clean, m.end()):
                continue
            bad.append((n, line.strip()))
    return bad


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("docs", nargs="*", default=["docs/proposal.md"])
    ap.add_argument("--list", action="store_true", help="print what the runs support")
    ap.add_argument("--presence-only", action="store_true",
                    help="skip the correspondence pass")
    a = ap.parse_args()
    supported = harvest(GENERATED)
    if a.list:
        print(f"{len(supported)} values supported by {len(GENERATED)} generated documents")
        print(" ".join(sorted(supported, key=lambda x: (len(x), x))))
        return
    idx, common = (None, None) if a.presence_only else index_generated()
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
        if a.presence_only:
            continue
        off = correspondence(d, idx, common)
        print(f"{d}: {len(off)} produced, but not for this claim")
        for n, line, where in off:
            print(f"   {n:>10}   {line[:88]}")
            print(f"   {'':>10}   lives in: {where[:88]}")
        fail += len(off)
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
