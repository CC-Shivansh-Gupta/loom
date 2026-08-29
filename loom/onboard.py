"""Onboarding assistant: plain-language plant description -> validated YAML.

The LLM drafts a config in the schema; the loader validates it; the
engineer reviews it. The template path extracts the numbers it can and
lays out a sensible default line.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from . import llm
from .config import BUILTIN_SENSOR_PROFILES, BUILTIN_STATION_TYPES, load_line

SCHEMA = {
    "type": "object",
    "properties": {
        "plant": {"type": "object", "properties": {"name": {"type": "string"}, "site": {"type": "string"},
                                                   "area": {"type": "string"}}, "additionalProperties": False},
        "line": {
            "type": "object",
            "properties": {
                "id": {"type": "string"}, "takt_s": {"type": "number"}, "cv": {"type": "number"},
                "default_buffer": {"type": "integer"},
                "zones": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "stations": {"type": "array", "items": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}, "type": {"type": "string"},
                                           "cycle_s": {"type": "number"}, "buffer_before": {"type": "integer"},
                                           "sensors": {"type": "string"}},
                            "required": ["id", "type", "cycle_s"], "additionalProperties": False}}},
                    "required": ["name", "stations"], "additionalProperties": False}},
            },
            "required": ["id", "takt_s", "zones"], "additionalProperties": False,
        },
        "variants": {"type": "object", "additionalProperties": {
            "type": "object", "properties": {"share": {"type": "number"},
                                             "cycle_mult": {"type": "object", "additionalProperties": {"type": "number"}}},
            "required": ["share"], "additionalProperties": False}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["plant", "line", "assumptions"],
    "additionalProperties": False,
}

SYSTEM = f"""You draft a Loom plant configuration from a plain-language description of an assembly line.
Station types available: {', '.join(BUILTIN_STATION_TYPES)}.
Sensor profiles available: {', '.join(BUILTIN_SENSOR_PROFILES)} (plc_full = full telemetry,
cycle_only = start/finish timestamps only, checklist = manual finish log, dark = nothing).
Rules: station ids unique and short (zone letter + number); cycle_s a few seconds under takt unless
told otherwise; put a larger buffer_before on the first station of the paint zone; when the
description leaves something out, choose a reasonable default and list it under `assumptions`."""


def draft(description: str, provider: llm.Provider | None = None, retries: int = 1) -> tuple[str, list[str]]:
    """Return (yaml_text, assumptions). Raises ValueError if it cannot validate."""
    prov = provider or llm.get_provider()
    user = description
    last_err = None
    for _ in range(retries + 1):
        if isinstance(prov, llm.TemplateProvider):
            data = _template_draft(description)
        else:
            data = prov.complete_json("onboard:draft", SYSTEM, user, SCHEMA)
        assumptions = data.pop("assumptions", [])
        text = yaml.safe_dump(data, sort_keys=False)
        try:
            _validate(text)
            return text, assumptions
        except Exception as e:                      # loader error -> one corrective retry
            last_err = e
            user = f"{description}\n\nYour previous draft failed validation: {e}. Fix it."
    raise ValueError(f"could not produce a valid config: {last_err}")


def _validate(text: str) -> None:
    p = Path("/tmp") / "loom_onboard_check.yaml"
    p.write_text(text)
    cfg = load_line(p)
    if len(cfg.stations) < 2:
        raise ValueError("a line needs at least two stations")


def _template_draft(description: str) -> dict:
    d = description.lower()

    def num(pattern: str, default: float) -> float:
        m = re.search(pattern, d)
        return float(m.group(1)) if m else default

    n = int(num(r"(\d+)\s*stations?", 12))
    takt = num(r"takt\D*(\d+)", 60)
    manual = int(num(r"(\d+)\s*(?:are\s+)?manual", 0))
    dark = int(num(r"(\d+)\s*(?:are\s+)?(?:dark|unmonitored|without sensors)", 0))
    paint_buf = int(num(r"paint\D*buffer\D*(\d+)|buffer\D*(\d+)\D*paint", 6))
    assumptions = [f"{n} stations split 1/3 body, 1/4 paint, rest final assembly",
                   f"cycle times 3-6 s under a {takt:.0f} s takt", "default buffers of 2"]
    zones = {"body": [], "paint": [], "final": []}
    for k in range(n):
        zone = "body" if k < n // 3 else ("paint" if k < n // 3 + n // 4 else "final")
        idx = len(zones[zone]) + 1
        st = {"id": f"{zone[0].upper()}{idx}", "type": "generic", "cycle_s": takt - 3 - (k % 4)}
        if zone == "body":
            st["type"] = "robot_weld"
        elif zone == "paint":
            st["type"] = "paint_booth"
            if idx == 1:
                st["buffer_before"] = paint_buf
        else:
            st["type"] = "manual_fit"
            st["sensors"] = "plc_full"
        zones[zone].append(st)
    finals = zones["final"]
    for st in finals[:manual]:
        st.pop("sensors", None)                      # manual -> checklist by type default
    if manual:
        assumptions.append(f"{manual} manual stations report by checklist only")
    for z in zones.values():
        if z and z[-1]["type"] != "inspection":
            z[-1]["type"] = "inspection"          # every zone ends in a quality gate
            z[-1].pop("sensors", None)
    if dark:
        candidates = [st for st in zones["body"] + zones["paint"] + finals
                      if st["type"] not in ("inspection",) and st.get("sensors") != "checklist"]
        for st in candidates[-dark:]:
            st["sensors"] = "dark"
        assumptions.append(f"the last {dark} non-inspection stations are dark")
    return {
        "plant": {"name": "New Plant", "site": "tbd", "area": "Assembly"},
        "line": {"id": "L1", "takt_s": takt, "cv": 0.05, "default_buffer": 2,
                 "zones": [{"name": z, "stations": s} for z, s in zones.items() if s]},
        "assumptions": assumptions,
    }
