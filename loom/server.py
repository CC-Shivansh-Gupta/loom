"""Live web server for the control room.

    python -m loom.server [--config healthy.yaml] [--port 8000]
    open http://localhost:8000

The simulation advances in a background task at `speed` x real time;
frames stream over a WebSocket; controls, injections and the config
editor go over REST. Nothing is precomputed.
"""
from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from .live import LiveSim

WEB = Path(__file__).resolve().parent.parent / "web"
app = FastAPI(title="Loom control room")
sim = LiveSim("healthy.yaml")


class Control(BaseModel):
    playing: bool | None = None
    speed: float | None = None
    reset: bool = False


class Load(BaseModel):
    name: str | None = None
    yaml: str | None = None


class Inject(BaseModel):
    kind: str
    station: str
    cycle_s: float | None = None
    ramp_s: float | None = None
    profile: str | None = None
    duration_s: float | None = None
    param: str | None = None
    to: float | None = None


@app.on_event("startup")
async def _start() -> None:
    async def ticker() -> None:
        last = time.perf_counter()
        while True:
            await asyncio.sleep(0.05)
            now = time.perf_counter()
            dt, last = now - last, now
            await asyncio.get_event_loop().run_in_executor(None, sim.step, min(dt, 0.5))
    asyncio.create_task(ticker())


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse((WEB / "app.html").read_text())


@app.get("/api/meta")
async def meta() -> dict:
    return sim.meta()


@app.post("/api/load")
async def load(body: Load):
    try:
        if body.yaml is not None:
            sim.load_yaml(body.yaml)
        elif body.name:
            sim.load_named(body.name)
        sim.playing = False
    except Exception as e:                      # config errors come back as text
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return {"ok": True, "meta": sim.meta()}


@app.post("/api/control")
async def control(body: Control) -> dict:
    if body.reset:
        sim.reset()
        sim.playing = False
    if body.playing is not None:
        sim.playing = body.playing
    if body.speed is not None:
        sim.speed = max(1.0, min(600.0, body.speed))
    return {"playing": sim.playing, "speed": sim.speed, "t": sim.plant.t}


@app.post("/api/inject")
async def inject(body: Inject):
    kw = {k: v for k, v in body.model_dump().items() if v is not None and k not in ("kind", "station")}
    try:
        return sim.inject(body.kind, body.station, **kw)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.get("/api/station/{sid}")
async def station(sid: str) -> dict:
    return sim.station_detail(sid)


@app.get("/api/view/{role}")
async def view(role: str) -> PlainTextResponse:
    return PlainTextResponse(sim.view(role))


@app.get("/api/scorecard")
async def scorecard() -> dict:
    return sim.scorecard()


@app.websocket("/ws")
async def ws(sock: WebSocket) -> None:
    await sock.accept()
    try:
        while True:
            frame = await asyncio.get_event_loop().run_in_executor(None, sim.frame)
            await sock.send_json(frame)
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="healthy.yaml")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    sim.load_named(args.config)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
