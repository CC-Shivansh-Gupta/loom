"""AI layer CLI.

    python -m loom.ai report  configs/ramp_b3.yaml --persona supervisor [--hours 1.0]
    python -m loom.ai whatif  configs/ramp_b3.yaml [--hours 0.75] [--station B3]
    python -m loom.ai improve [--iterations 3]
    python -m loom.ai onboard "18 stations, takt 72 s, 4 manual, 2 dark, paint buffer 10"

Provider: LOOM_LLM=template|claude (auto: Claude when the SDK and credentials
are present, otherwise the deterministic templates).
"""
from __future__ import annotations

import argparse
import json

from . import evidence, improve, llm, narrate, onboard, voi, whatif
from .evaluator import bottleneck_scorecard, containment_scorecard
from .run import build


def _pack(cfg_path: str, hours: float, with_ledger: bool = True) -> tuple:
    cfg, plant, sensors, twin = build(cfg_path)
    plant.run(hours * 3600)
    p = evidence.pack(
        twin, sensors.coverage(),
        bottleneck_scorecard(plant, twin) if with_ledger else None,
        containment_scorecard(plant, twin) if with_ledger else None,
        voi.rank(cfg, plant, twin) if with_ledger else None)
    return cfg, plant, sensors, twin, p


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("config")
    r.add_argument("--persona", default="supervisor", choices=sorted(narrate.PERSONAS))
    r.add_argument("--hours", type=float, default=1.0)
    r.add_argument("--json", action="store_true", help="print the evidence pack instead")
    w = sub.add_parser("whatif")
    w.add_argument("config")
    w.add_argument("--hours", type=float, default=0.75)
    w.add_argument("--station")
    w.add_argument("--horizon", type=float, default=30.0, help="minutes")
    i = sub.add_parser("improve")
    i.add_argument("--iterations", type=int, default=3)
    o = sub.add_parser("onboard")
    o.add_argument("description")
    args = ap.parse_args()

    prov = llm.get_provider()
    if args.cmd == "report":
        cfg, plant, sensors, twin, p = _pack(args.config, args.hours)
        if args.json:
            print(json.dumps(p, indent=1))
            return
        p["ai_telemetry"] = llm.telemetry_summary()
        print(narrate.report(args.persona, p, prov))
    elif args.cmd == "whatif":
        cfg, plant, sensors, twin, p = _pack(args.config, args.hours, with_ledger=False)
        res = whatif.recommend(cfg, twin, p, args.station, prov, args.horizon * 60)
        print(res["explanation"])
    elif args.cmd == "improve":
        run = improve.improve(args.iterations, provider=prov)
        print(json.dumps(run.as_dict(), indent=1))
    elif args.cmd == "onboard":
        text, assumptions = onboard.draft(args.description, prov)
        print(text)
        print("# assumptions:")
        for a in assumptions:
            print(f"#  - {a}")
    print(f"\n[llm: {prov.name}] {llm.telemetry_summary()}")


if __name__ == "__main__":
    main()
