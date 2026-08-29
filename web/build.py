"""Bundle the control-room page: gzip + base64 each scenario JSON into the template.

    python web/build.py [--out web/dist/index.html]
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
from pathlib import Path

HERE = Path(__file__).parent
ORDER = ["ramp_b3", "ramp_b3_dark", "sensor_fault_b2", "weld_drift_b2", "shifting", "plant_b"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "dist" / "index.html"))
    args = ap.parse_args()
    pack = {}
    for name in ORDER:
        p = HERE / "data" / f"{name}.json"
        if p.exists():
            raw = p.read_bytes()
            pack[name] = base64.b64encode(gzip.compress(raw, 9)).decode()
    html = (HERE / "index.template.html").read_text().replace("__DATA__", json.dumps(pack))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {len(pack)} scenarios)")


if __name__ == "__main__":
    main()
