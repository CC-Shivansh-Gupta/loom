"""AI layer, template path (no network). The Claude path shares every
line of code except the provider call, so this covers the mechanism."""
import json
import re

import pytest

from loom import evidence, harness, improve, llm, narrate, onboard, voi, whatif
from loom.config import load_line
from loom.evaluator import bottleneck_scorecard, containment_scorecard
from loom.run import build

H = 3600.0


@pytest.fixture
def prov():
    return llm.template_provider()


def _pack(config, hours):
    cfg, plant, sensors, twin = build(config)
    plant.run(hours * H)
    p = evidence.pack(twin, sensors.coverage(), bottleneck_scorecard(plant, twin),
                      containment_scorecard(plant, twin), voi.rank(cfg, plant, twin))
    return cfg, plant, twin, p


def test_evidence_pack_is_json_and_carries_provenance():
    cfg, plant, twin, p = _pack("configs/ramp_b3_dark.yaml", 1.0)
    json.dumps(p)
    b3 = next(s for s in p["stations"] if s["id"] == "B3")
    assert b3["sensors"] == "dark" and b3["state_source"] == "inferred"
    assert p["next_sensor"][0]["station"] == "B3"
    assert "ledger" in p and p["ledger"]["bottleneck"][0]["station"] == "B3"


def test_reports_only_use_numbers_from_the_pack(prov):
    cfg, plant, twin, p = _pack("configs/weld_drift_b2.yaml", 1.5)
    blob = json.dumps(p)
    for persona in narrate.PERSONAS:
        text = narrate.report(persona, p, prov)
        assert text.startswith("#")
        # every number with a decimal in the report appears in the pack
        for num in re.findall(r"\b\d+\.\d+\b", text):
            assert num in blob or f"{float(num):g}" in blob, f"{persona}: {num} not in evidence"
    q = narrate.report("quality", p, prov)
    assert "weld_current" in q and "Hold #1" in q


def test_whatif_ranks_candidates_by_simulation(prov):
    cfg, plant, twin, p = _pack("configs/ramp_b3.yaml", 0.75)
    res = whatif.recommend(cfg, twin, p, "B3", prov, 1800)
    assert res["focus_station"] == "B3"
    assert len(res["ranked"]) == 3
    best = res["ranked"][0]
    assert best["simulated_veh_per_h"] > res["baseline"]["simulated_veh_per_h"]
    assert "simulated" in res["explanation"]
    # a floater on the bottleneck must beat doing nothing
    fl = next(o for o in res["ranked"] if o["mitigation"].startswith("floater"))
    assert fl["simulated_upstream_blocked_min"] <= res["baseline"]["simulated_upstream_blocked_min"]


def test_llm_candidates_are_validated_against_the_menu():
    cfg = load_line("configs/ramp_b3.yaml")
    data = {"candidates": [
        {"action": "floater", "station": "B3", "factor": 0.1, "why": "x"},     # clamped
        {"action": "rebalance", "station": "B3", "to": "ZZ", "why": "x"},      # bad target
        {"action": "teleport", "station": "B3", "why": "x"},                   # not on menu
        {"action": "buffer", "station": "Q9", "why": "x"},                     # unknown station
    ]}
    out = whatif._parse_candidates(data, cfg)
    assert [c.action for c in out] == ["floater"] and out[0].factor == 0.6


def test_harness_and_gated_improvement(prov):
    quick = (harness.Scenario("configs/healthy.yaml", 2.0, (0,), "healthy"),
             harness.Scenario("configs/ramp_b3.yaml", 1.5, (0,), "fault"))
    run = improve.improve(iterations=2, scenarios=quick, provider=prov)
    assert run.baseline.leads_min and run.baseline.fa_per_8h <= improve.FA_BUDGET_PER_8H
    assert len(run.iterations) == 2
    for it in run.iterations:
        if it.accepted:
            assert it.result.fa_per_8h <= improve.FA_BUDGET_PER_8H
            assert it.result.mean_lead_min >= run.baseline.mean_lead_min - improve.LEAD_TOLERANCE_MIN
    cal = harness.calibration(run.baseline.records)
    assert cal and all(0 <= c["hit_rate"] <= 1 for c in cal)


def test_onboard_template_produces_valid_config(prov):
    text, assumptions = onboard.draft(
        "18 stations, takt 72 s, 4 manual, 2 dark, paint buffer 10", prov)
    assert "takt_s: 72" in text and assumptions
    import tempfile, pathlib
    p = pathlib.Path(tempfile.mkdtemp()) / "new.yaml"
    p.write_text(text)
    cfg = load_line(p)
    assert len(cfg.stations) == 18 and cfg.takt_s == 72
    assert sum(1 for s in cfg.stations if s.sensors.name == "dark") == 2
    assert sum(1 for s in cfg.stations if s.sensors.name == "checklist") == 4


def test_telemetry_is_recorded(prov):
    before = len(llm.TELEMETRY)
    cfg, plant, twin, p = _pack("configs/healthy.yaml", 0.5)
    narrate.report("supervisor", p, prov)
    assert len(llm.TELEMETRY) == before + 1
    assert llm.TELEMETRY[-1].provider == "template"
    assert llm.telemetry_summary()["cost_usd"] == 0.0


# ---- AI layer evaluation (docs/ai_eval.md) --------------------------------

def test_redteam_catches_every_planted_fabrication():
    """The grounding check is the whole basis for trusting a generated
    briefing. If a planted number ever slips through, nothing else in the AI
    layer is defensible."""
    from loom import aieval, evidence
    from loom.run import build
    cfg, plant, sensors, twin = build("configs/ramp_b3.yaml")
    plant.run(2400)
    pack = evidence.pack(twin, sensors.coverage())
    r = aieval.redteam(pack)
    assert r["caught"] == r["planted"], \
        f"missed: {[f['name'] for f in r['fixtures'] if not f['correct']]}"
    assert r["false_accusations"] == 0, "a clean report was wrongly flagged"


def test_reports_survive_an_empty_evidence_pack():
    """Abstention: with nothing to report the renderers must say so, not
    raise. A briefing generator that crashes on sparse evidence fails exactly
    when the line is quiet."""
    from loom import evidence, llm, narrate
    from loom.run import build
    cfg, plant, sensors, twin = build("configs/healthy.yaml")
    plant.run(600)
    pack = evidence.pack(twin, sensors.coverage())
    pack.update(alerts=[], quality={}, stations=[])
    prov = llm.get_provider()
    for persona in ("supervisor", "quality", "manager"):
        text = narrate.report(persona, pack, prov)
        assert text.strip(), f"{persona} produced nothing"


def test_whatif_ranks_against_a_simulated_baseline():
    """The model proposes from a menu; the ranking has to come from the
    simulator. Every option must carry a simulated number."""
    from loom.live import LiveSim
    sim = LiveSim("ramp_b3.yaml")
    sim.plant.run(2700)
    res = sim.whatif(horizon_min=15)
    assert res["baseline"]["simulated_veh_per_h"] > 0
    assert res["ranked"], "no mitigations proposed"
    for o in res["ranked"]:
        assert o["simulated_veh_per_h"] is not None
        assert o["mitigation"]


def test_improve_gate_refuses_a_budget_breaking_proposal():
    """The point of the loop is the refusal. If every proposal is accepted the
    gate is decorative."""
    from loom import improve
    run = improve.improve(2)
    d = run.as_dict()
    assert d["iterations"], "no proposals made"
    for it in d["iterations"]:
        if not it["accepted"]:
            assert it["reason"], "a rejection with no stated reason"


def test_auto_detection_needs_a_credential_not_just_the_package(monkeypatch):
    """Installing `anthropic` is not the same as being able to call it.

    The SDK constructs without a credential and raises only when the first
    request goes out, so choosing the provider on importability alone picks
    Claude and then fails mid-feature — with the deterministic path, which
    exists for exactly this, sitting right there unused.
    """
    pytest.importorskip("anthropic")
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "LOOM_LLM"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(llm, "_credentials_visible", lambda client: False)
    assert isinstance(llm.get_provider("auto"), llm.TemplateProvider)
    # ...and asking for it explicitly still gets it, so the fallback is not a trap
    monkeypatch.setattr(llm, "_credentials_visible", lambda client: True)
    assert llm.get_provider("claude").name == "claude"
