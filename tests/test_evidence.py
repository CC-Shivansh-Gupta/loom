"""The evidence documents and the prose that quotes them stay in step.

`loom.numbers` applies the AI layer's grounding rule to our own writing: a
figure in the proposal must appear in a document a run produced. These tests
are what stop the benchmark and the pitch drifting apart again.
"""
import loom.numbers as N
from loom import baseline, coverage
from loom.config import load_line


def test_proposal_numbers_are_grounded():
    supported = N.harvest(N.GENERATED)
    assert supported, "no generated evidence documents found"
    for doc in ("docs/proposal.md", "docs/competition_readiness.md",
                "docs/solution_design.md", "docs/deck.html",
                "docs/proposal_deck.html"):
        bad = N.check(doc, supported)
        assert not bad, (
            f"{doc} quotes figures no run produced: "
            + ", ".join(sorted({n for n, _ in bad}))
            + " -- regenerate the evidence docs or declare them in docs/exempt_numbers.md")


def test_exempt_numbers_all_carry_a_source():
    ex = N.exempt()
    assert ex, "docs/exempt_numbers.md is empty"
    for n, source in ex.items():
        assert source and source not in ("-", ""), f"{n} is exempt with no source"


def test_threshold_baseline_cannot_see_a_dark_station():
    """The comparator has to be a fair fight: a threshold alarm reads
    measured cycles only, so a dark station is invisible to it. If this ever
    passes, the baseline is being fed data the real alternative would not
    have and every comparison in docs/baselines.md is worthless."""
    scores, _ = baseline.compare("configs/ramp_b3_dark.yaml", 1.5, 0)
    thr = [s for s in scores if s.method == "threshold"]
    assert thr, "no threshold scores produced"
    assert all(s.t_warn is None for s in thr)
    loom_scores = [s for s in scores if s.method == "loom"]
    assert any(s.t_warn is not None for s in loom_scores), "the twin should still warn"


def test_darken_hits_the_faulting_station_first():
    """Darkening bystanders is the easy case. The coverage curve is only
    honest if the failing station goes dark before anything else."""
    cfg = load_line("configs/ramp_b3.yaml")
    faulted = {p.station for p in cfg.perturbations}
    _, chosen = coverage.darken(cfg, 0.1)
    assert chosen & faulted, f"{chosen} misses the faulting station {faulted}"


def test_numbers_correspond_to_the_rows_that_produced_them(tmp_path):
    """The failure presence cannot see: every figure is real, and one of them
    has been attached to the wrong row.

    This is not hypothetical. A benchmark re-run moved the 0.5–0.7 confidence
    bin from 59 % to 54 %, the prose kept saying 59 %, and the presence check
    passed because 59 was still somewhere in the document. What identifies the
    claim is the company its numbers keep: the row that produced it holds
    0.5–0.7 and 54 together, and no row holds 0.5–0.7 and 59.
    """
    gen = tmp_path / "gen.md"
    gen.write_text(
        "| stated confidence | n | realised hit rate |\n"
        "|---|---|---|\n"
        "| 0.5-0.7 | 41 | 54% |\n"
        "| 0.7-0.9 | 30 | 97% |\n"
        "| 0.9-1.0 | 59 | 100% |\n")           # 59 is present, as a count
    idx, common = N.index_generated([str(gen)])

    stale = tmp_path / "stale.md"
    stale.write_text("Confidence means something: 0.5-0.7 stated gives 59 % realised.\n")
    assert N.check(str(stale), N.harvest([str(gen)])) == [], "presence should pass — 59 is there"
    bad = N.correspondence(str(stale), idx, common)
    assert [n for n, _, _ in bad] == ["59"], bad

    fresh = tmp_path / "fresh.md"
    fresh.write_text("Confidence means something: 0.5-0.7 stated gives 54 % realised.\n")
    assert N.correspondence(str(fresh), idx, common) == []


def test_evidence_documents_and_prose_correspond():
    idx, common = N.index_generated()
    assert idx, "no generated evidence documents found"
    for doc in ("docs/proposal.md", "docs/competition_readiness.md",
                "docs/solution_design.md", "docs/deck.html",
                "docs/proposal_deck.html"):
        bad = N.correspondence(doc, idx, common)
        assert not bad, (
            f"{doc} quotes figures a run produced, but not for the claim they are attached to:\n"
            + "\n".join(f"  {n} in {line[:80]!r} — lives in {where[:80]!r}" for n, line, where in bad))
