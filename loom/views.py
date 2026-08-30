"""Role views. All read the same twin; they differ in horizon, aggregation
and which provenance marks they surface. Text renderers for now; a UI
layer later maps each to a screen.

    operator(twin, station)   now / my station / seconds
    supervisor(twin)          this shift / the line / minutes
    manager(twin, scorecard)  the week / trust ledger / daily
"""
from __future__ import annotations

from .twin import INFERRED, MARK, MEASURED, SIMULATED, Twin

LEGEND = "● measured  ◐ inferred  ○ simulated"


def _hm(t: float) -> str:
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}"


def operator(twin: Twin, station: str) -> str:
    twin.refresh()
    b = twin.stations[station]
    cfg = twin.cfg.station(station)
    lines = [f"OPERATOR  {station} ({cfg.type.name}, {cfg.zone})   t={_hm(twin.t)}   {LEGEND}"]
    lines.append(f"  state      {MARK[b.state.source]} {b.state.value}"
                 f"   vehicle {MARK[b.vehicle.source]} {b.vehicle.value}")
    cyc = b.cycle_s
    if cyc.value is None:
        lines.append("  cycle      (no data yet)")
    else:
        gap = cyc.value - twin.cfg.takt_s
        flag = "OVER TAKT" if gap > 0 else "ok"
        lines.append(f"  cycle      {MARK[cyc.source]} {cyc.value:.1f}s vs takt {twin.cfg.takt_s:.0f}s  ({gap:+.1f}s, {flag})")
    buf = twin.buffers[station]
    lines.append(f"  in-buffer  {MARK[buf.source]} {buf.value}/{cfg.buffer_before}")
    health = "" if b.health == "ok" else f"   !! sensor {b.health}"
    lines.append(f"  sensors    {cfg.sensors.name}{health}")
    a = twin.active.get(station)
    lines.append(f"  alert      {'none' if a is None else f'○ upstream blocks in ~{a.eta_s / 60:.0f} min (conf {a.confidence:.0%})'}")
    return "\n".join(lines)


def supervisor(twin: Twin) -> str:
    twin.refresh()
    cfg = twin.cfg
    lines = [f"SUPERVISOR  line {cfg.name}   t={_hm(twin.t)}   out {twin.exited}   "
             f"unplaced {twin.in_transit()}   {LEGEND}"]
    bn = twin.bottleneck_now()
    if bn is not None:
        lines.append(f"  momentary bottleneck (longest active period): {MARK[bn[2]]} {bn[0]} for {bn[1] / 60:.0f} min")
    lines.append(f"  {'stn':<5}{'zone':<8}{'state':<10}{'cycle':>8}{'buf':>7}  sensor      alert")
    bufs = twin.buffers
    for s in cfg.stations:
        b = twin.stations[s.id]
        cyc = b.cycle_s
        cyc_s = "   -   " if cyc.value is None else f"{MARK[cyc.source]}{cyc.value:5.1f}s"
        buf = bufs[s.id]
        a = twin.active.get(s.id)
        alert = "" if a is None else f"○ blocks in ~{a.eta_s / 60:.0f}m ({a.confidence:.0%})"
        health = s.sensors.name if b.health == "ok" else f"{s.sensors.name}!{b.health}"
        lines.append(f"  {s.id:<5}{s.zone:<8}{MARK[b.state.source]}{b.state.value:<9}"
                     f"{cyc_s:>8} {MARK[buf.source]}{buf.value:>2}/{s.buffer_before:<2} {health:<12}{alert}")
    return "\n".join(lines)


def quality(twin: Twin) -> str:
    twin.refresh()
    q = twin.quality
    cfg = twin.cfg
    lines = [f"QUALITY ENGINEER  line {cfg.name}   t={_hm(twin.t)}   {LEGEND}"]
    for s in cfg.stations:
        if s.type.inspection:
            ok, n = q.first_pass_yield(s.id)
            if n:
                lines.append(f"  first-pass yield @{s.id}: {ok}/{n} = {ok / n:.1%}")
    lines.append("  parameter monitors (EWMA in sd units, CUSUM lo/hi; reported stations only):")
    for (sid, pname), m in q.monitors.items():
        if sid not in q.reports or m.n == 0:
            continue
        flag = "  <-- DRIFT" if m.active else ""
        lines.append(f"    {sid}.{pname:<13} mean ● {m.mean_now():8.3f} {m.spec.unit:<3} "
                     f"(spec {m.spec.lsl}-{m.spec.usl})  ewma {m.ewma:+5.2f}  cusum {m.c_lo:4.1f}/{m.c_hi:4.1f}{flag}")
    unreported = [f"{s.id}.{p.name}" for s in cfg.stations for p in s.params if s.id not in q.reports]
    if unreported:
        lines.append(f"    not reported (◐ unknown): {', '.join(unreported)}")
    if q.drift_log:
        lines.append("  drift alerts:")
        for a in q.drift_log:
            lines.append(f"    {a}")
    if q.hypotheses:
        lines.append("  root-cause hypotheses (ranked; evidence, not verdicts):")
        for h in q.hypotheses[:5]:
            lines.append(f"    {h}")
    if q.holds:
        lines.append("  containment:")
        for h in q.holds:
            lines.append(f"    {h}")
            lines.append(f"      sure: {h.sure[:12]}{' ...' if len(h.sure) > 12 else ''}")
            if h.uncertain:
                lines.append(f"      ◐ uncertain: {h.uncertain[:12]}{' ...' if len(h.uncertain) > 12 else ''}")
            if h.exited:
                lines.append(f"      already exited (yard check): {h.exited}")
    else:
        lines.append("  containment: none active")
    return "\n".join(lines)


def maintenance(twin: Twin) -> str:
    """Degradation trends per asset: which station is wearing, how fast, and
    when it crosses takt or a spec limit -- the input to scheduling a
    maintenance window."""
    twin.refresh()
    cfg = twin.cfg
    lines = [f"MAINTENANCE  line {cfg.name}   t={_hm(twin.t)}   {LEGEND}"]
    lines.append(f"  {'stn':<5}{'type':<12}{'cycle':>8}{'trend':>12}{'to takt':>10}  status")
    rows = []
    for s in cfg.stations:
        fit = twin.forecaster.fit(s.id, twin.t)
        if fit is None:
            continue
        slope_min = fit.slope * 60
        c_eff = fit.c_now / s.capacity
        over = c_eff > cfg.takt_s
        if over:
            eta = "over now"
        elif fit.slope > 0 and fit.tstat >= 2.0:
            eta = f"~{(cfg.takt_s * s.capacity - fit.c_now) / fit.slope / 60:.0f} min"
        else:
            eta = "-"
        status = ("SCHEDULE" if over or (fit.slope > 0 and fit.tstat >= twin.forecaster.min_tstat)
                  else ("watch" if fit.slope > 0 and fit.tstat >= 2.0 else "ok"))
        src = MARK[INFERRED]
        rows.append((0 if status == "SCHEDULE" else 1 if status == "watch" else 2, s.id,
                     f"  {s.id:<5}{s.type.name:<12}{src}{fit.c_now:6.1f}s{slope_min:+9.2f}s/min{eta:>10}  {status}"))
    for _, _, r in sorted(rows):
        lines.append(r)
    q = twin.quality
    drifting = [(k, m) for k, m in q.monitors.items() if m.active is not None]
    if drifting:
        lines.append("  parameter drift (process side):")
        for (sid, pname), m in drifting:
            a = m.active
            eta = ("out of spec now" if a.t_to_limit_s is not None and a.t_to_limit_s <= 0 else
                   "no crossing projected" if a.t_to_limit_s is None else f"limit in ~{a.t_to_limit_s / 60:.0f} min")
            lines.append(f"    {sid}.{pname}: {a.direction}, mean ● {m.mean_now():.3f} {m.spec.unit}, since ~{_hm(a.onset_t)}, {eta}")
    sched = [r for r in rows if r[0] == 0]
    if sched:
        lines.append(f"  next window: {', '.join(sid for _, sid, _ in sched)} -- intervene before the projected crossing")
    else:
        lines.append("  next window: nothing due")
    return "\n".join(lines)


_BENCH_CACHE: dict | None = None


def benchmark_reference() -> dict:
    """Aggregates from the last `python -m loom.bench` run, if one exists."""
    global _BENCH_CACHE
    if _BENCH_CACHE is None:
        import json
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "docs", "benchmark.json")
        try:
            with open(path) as f:
                _BENCH_CACHE = json.load(f)
        except (OSError, ValueError):
            _BENCH_CACHE = {}
    return _BENCH_CACHE


def roi(twin: Twin, scorecard: dict | None = None, containment: list | None = None,
        coverage: dict[str, str] | None = None, voi_rank: list[dict] | None = None) -> dict:
    """The investment case as data, so the text view and the control room's
    Exec tab cannot drift apart. Every input is returned alongside every
    output: a business case a sceptic cannot re-derive is worthless."""
    twin.refresh()
    cfg = twin.cfg
    e = cfg.economics
    leads = [s.lead_s / 60 for s in (scorecard or {}).get("scores", []) if s.lead_s]
    lead = sum(leads) / len(leads) if leads else None
    ref = benchmark_reference()
    basis = "measured on this line"
    if lead is None and ref.get("mean_lead_min"):
        lead = ref["mean_lead_min"]
        basis = (f"no fault observed on this line yet — benchmark mean over "
                 f"{ref.get('seeds', '?')} seeds x {len(ref.get('scenarios', {}))} scenarios")
    hold_saved = escaped = None
    cont = (containment or [None])[0]
    if cont is not None and cont.hold_size and cont.blanket_size:
        hold_saved = cont.blanket_size - cont.hold_size
        escaped = cont.escaped
    if hold_saved is None and ref.get("mean_hold_saved") is not None:
        hold_saved = ref["mean_hold_saved"]
    hs = hold_saved or 0
    if escaped is None:
        esc_avoided = ref.get("mean_escapes_prevented") or 0
    else:
        esc_avoided = max(0, (cont.n_defective - cont.detected_at_inspection) - escaped)

    v_bottleneck = (lead or 0.0) * e.prevented_share * e.bottleneck_events_per_week \
        * e.weeks_per_year * e.downtime_cost_per_min
    v_holds = hs * e.hold_cost_per_vehicle * e.quality_events_per_month * 12
    v_escape = esc_avoided * e.escape_cost_per_defect * e.quality_events_per_month * 12
    total = v_bottleneck + v_holds + v_escape
    n_retrofit = sum(1 for v in (coverage or {}).values() if v != "plc_full")
    capex = n_retrofit * e.sensor_cost_per_station
    net = total - e.licence_per_line_per_year
    payback = None if net <= 0 else 12 * (capex + e.licence_per_line_per_year) / total
    tenth = v_bottleneck / 10 + v_holds + v_escape
    pb10 = None if tenth <= e.licence_per_line_per_year else \
        12 * (capex + e.licence_per_line_per_year) / tenth
    n = len(coverage or {})
    dark = sum(1 for v in (coverage or {}).values() if v == "dark")
    partial = n - dark - sum(1 for v in (coverage or {}).values() if v == "plc_full")
    return {
        "basis": basis, "lead_min": lead, "lead_from_this_line": bool(leads),
        "false_alarms": len((scorecard or {}).get("false_alarms", [])),
        "alerts_raised": (scorecard or {}).get("alerts_raised", 0),
        "coverage": {"full": n - dark - partial, "partial": partial, "dark": dark, "total": n},
        "lines": [
            {"name": "bottlenecks avoided", "value": v_bottleneck,
             "formula": (f"{lead or 0:.1f} min lead x {e.prevented_share:.0%} acted on x "
                         f"{e.bottleneck_events_per_week:g}/wk x {e.weeks_per_year:g} wk x "
                         f"${e.downtime_cost_per_min:,.0f}/min")},
            {"name": "targeted holds", "value": v_holds,
             "formula": (f"{hs} fewer vehicles held/event x ${e.hold_cost_per_vehicle:,.0f} x "
                         f"{e.quality_events_per_month:g}/mo x 12")},
            {"name": "escapes prevented", "value": v_escape,
             "formula": (f"{esc_avoided} defects/event x ${e.escape_cost_per_defect:,.0f} x "
                         f"{e.quality_events_per_month:g}/mo x 12")},
        ],
        "total": total, "licence": e.licence_per_line_per_year,
        "retrofit_stations": n_retrofit, "capex": capex, "net": net,
        "payback_months": payback,
        "sensitivity": {"downtime_cost_per_min": e.downtime_cost_per_min,
                        "at_one_tenth_total": tenth, "at_one_tenth_payback_months": pb10},
        "next_retrofit": (voi_rank or [None])[0],
    }


def leadership(twin: Twin, scorecard: dict | None = None, containment: list | None = None,
               coverage: dict[str, str] | None = None, voi_rank: list[dict] | None = None) -> str:
    """Text rendering of `roi()`. One implementation, two surfaces."""
    r = roi(twin, scorecard, containment, coverage, voi_rank)
    cfg = twin.cfg
    e = cfg.economics
    L = [f"LEADERSHIP  {cfg.plant.get('name', cfg.name)} · line {cfg.name}   t={_hm(twin.t)}",
         f"  basis: {r['basis']}",
         "    warning lead        " + ("-" if r["lead_min"] is None else f"{r['lead_min']:.1f} min"),
         f"    false alarms        {r['false_alarms']} of {r['alerts_raised']} alerts",
         f"    instrumentation     {r['coverage']['full']}/{r['coverage']['total']} full, "
         f"{r['coverage']['partial']} partial, {r['coverage']['dark']} dark",
         "  annual value (assumptions in configs `economics:`):"]
    for ln in r["lines"]:
        L.append(f"    {ln['name']:<20} {ln['formula']} = ${ln['value']:,.0f}")
    L.append(f"    total                ${r['total']:,.0f} / year")
    L.append(f"  cost: licence ${r['licence']:,.0f}/yr + retrofit of {r['retrofit_stations']} "
             f"station(s) ${r['capex']:,.0f} one-off")
    if r["total"] <= 0:
        L.append("  payback              not yet — no events on this line and no benchmark to fall back on")
    else:
        pb = r["payback_months"]
        L.append(f"  payback              {'never at these inputs' if pb is None else f'{pb:.1f} months'}"
                 f"   (net ${r['net']:,.0f}/yr after licence)")
        sv = r["sensitivity"]
        p10 = sv["at_one_tenth_payback_months"]
        L.append(f"  sensitivity          at 1/10th the assumed ${sv['downtime_cost_per_min']:,.0f}/min: "
                 f"${sv['at_one_tenth_total']:,.0f}/yr, payback "
                 + ("never" if p10 is None else f"{p10:.1f} months"))
        L.append("                       the claim is not the number; it is that after two weeks of")
        L.append("                       shadow mode the plant has its own number in place of ours")
    nr = r["next_retrofit"]
    if nr:
        gain = "" if nr["d_lead_s"] is None else f", +{nr['d_lead_s'] / 60:.1f} min lead"
        L.append(f"  next retrofit        {nr['station']} ({nr['from']} -> {nr['to']}): "
                 f"~${e.sensor_cost_per_station:,.0f}{gain}")
    L.append("  rollout              shadow 2 wk -> advisory -> reversible automatic; gate = trust ledger")
    return "\n".join(L)


def manager(twin: Twin, scorecard: dict | None = None, coverage: dict[str, str] | None = None,
            voi_rank: list[dict] | None = None) -> str:
    twin.refresh()
    cfg = twin.cfg
    lines = [f"PLANT MANAGER  {cfg.plant.get('name', cfg.name)}   t={_hm(twin.t)}"]
    lines.append(f"  output {twin.exited} veh   ({twin.exited / max(twin.t / 3600, 1e-9):.1f} veh/h vs {3600 / cfg.takt_s:.0f} takt)")
    if coverage:
        n_dark = sum(1 for p in coverage.values() if p == "dark")
        n_full = sum(1 for p in coverage.values() if p == "plc_full")
        lines.append(f"  instrumentation: {n_full} full, {len(coverage) - n_full - n_dark} partial, {n_dark} dark of {len(coverage)} stations")
    unhealthy = [f"{sid} {b.health}" for sid, b in twin.stations.items() if b.health != "ok"]
    if unhealthy:
        lines.append(f"  sensor health: {', '.join(unhealthy)}")
    if scorecard:
        lines.append("  twin trust ledger:")
        for s in scorecard["scores"]:
            if s.lead_s is not None:
                outcome = f"{s.lead_s / 60:.1f} min lead, conf {s.alert_conf:.0%}"
                if s.alert_inferred_share:
                    outcome += f" ({s.alert_inferred_share:.0%} of evidence inferred)"
            elif s.t_alert is not None:
                outcome = f"warned at {_hm(s.t_alert)}, outcome pending"
            elif s.t_upstream_blocked is not None:
                outcome = "missed"
            else:
                outcome = "no event yet"
            lines.append(f"    {s.station}: {outcome}")
        lines.append(f"    false alarms: {len(scorecard['false_alarms'])} / {scorecard['alerts_raised']} raised")
    if voi_rank:
        lines.append("  next sensor to buy:")
        for r in voi_rank[:3]:
            lead = "" if r["d_lead_s"] is None else f", +{r['d_lead_s'] / 60:.1f} min lead"
            lines.append(f"    {r['station']} ({r['from']} → {r['to']}): "
                         f"+{r['d_samples_per_h']:.0f} exact samples/h{lead}, ~${r['cost']:.0f}")
    return "\n".join(lines)
