"""The tuning sweep stays runnable.

`docs/forecaster_tuning.md` is the justification for the four numbers the
forecaster runs on. A table nobody can regenerate is an assertion, so these
tests keep the generator working and keep the shipped defaults inside the grid
it explores -- otherwise the document could "justify" a combination the sweep
never measured.
"""
import pytest

from loom import harness, sweep


@pytest.fixture(scope="module")
def rows():
    """One sweep at one seed, shared: each combination is a full harness run."""
    return sweep.measure(1)


def test_the_grid_contains_the_shipped_defaults():
    combos = [p for _, p in sweep.grid()]
    assert harness.DEFAULT_PARAMS in combos
    for key, value in harness.DEFAULT_PARAMS.items():
        assert value in sweep.AXES[key], f"{key}={value} is not a level the sweep explores"


def test_every_combination_is_measured_once():
    combos = [tuple(sorted(p.items())) for _, p in sweep.grid()]
    assert len(combos) == len(set(combos)), "a combination is measured twice"


def test_search_rows_stay_inside_the_bounds_the_improvement_loop_uses():
    """The tuning rows have to be combinations `improve.py` could actually
    propose. `min_over_z = 0` is exempt on purpose: it turns the standard-error
    test *off*, which is a mechanism being removed — the same knob
    `loom.ablate` uses — not a parameter being tuned."""
    for axis, p in sweep.grid():
        if axis.startswith("naive") or p["min_over_z"] == 0.0:
            continue
        assert harness.clamp(p) == p, f"{p} falls outside harness.BOUNDS"
    assert harness.clamp(sweep.NAIVE) != sweep.NAIVE


def test_sweep_runs_at_one_seed_and_reports_the_naive_failure_mode(rows):
    assert len(rows) == len(sweep.grid())
    by_axis = {r.axis: r for r in rows}
    naive, defaults = by_axis["naive"], by_axis["defaults"]
    assert defaults.is_default and defaults.mean_lead_min is not None
    # The point of the table: the untutored trend test is an alert-fatigue
    # machine, and the shipped defaults are inside the published budget.
    assert naive.fa_per_8h > 10 * sweep.FA_BUDGET
    assert defaults.fa_per_8h <= sweep.FA_BUDGET

    chosen = sweep.choose(rows)
    assert chosen.fa_per_8h <= sweep.FA_BUDGET and chosen.misses == 0


def test_report_is_markdown_that_states_the_chosen_row(rows):
    text = sweep.report(1, rows)
    assert text.startswith("# Forecaster tuning")
    assert "| varies | window |" in text
    assert "standard-error test" in text and "persistence rule" in text
    assert "Sweep picks:" in text and "Shipped defaults" in text
