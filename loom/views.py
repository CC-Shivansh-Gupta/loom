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
