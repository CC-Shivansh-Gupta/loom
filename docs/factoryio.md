# Factory I/O integration

Purpose: one slide and a 30-second clip that say *"Loom ran read-only against third-party,
PLC-driven equipment and tracked it — including a station with no sensors."* Everything else in
the pitch runs on the simulated plant; this is the integration proof.

## How it fits

```
Factory I/O (Windows)                      Loom (any machine on the LAN)
┌──────────────────────────┐  Modbus/TCP   ┌─────────────────────────────┐
│ scene: 6 conveyor        │◀── reads ─────│ loom.factoryio.Feed          │
│ stations, photo-eyes,    │  discrete     │  edges → events → Twin       │
│ emitter, remover         │  inputs       │  (never writes)              │
│                          │               └─────────────┬───────────────┘
│ Modbus TCP/IP Server     │◀── writes ────┐             │ frames
│ driver, port 502         │  coils        │             ▼
└──────────────────────────┘               │   loom.server --factoryio
                       loom.plc_stub ──────┘   (control room, live)
                       (plays the PLC; separate process)
```

Loom only ever reads discrete inputs. The `plc_stub` is the plant's controller — it drives the
conveyors and can wear a station on cue — and it is deliberately a separate process Loom does not
talk to. Swap it for a real PLC and nothing on Loom's side changes.

## Build the scene (Factory I/O, once)

1. New scene. Place, left to right: an **Emitter**, then six **Conveyor** segments (S1…S6), then a
   **Remover**.
2. On each segment put a **Diffuse Sensor** at its start (`entry`) and one at its end (`exit`).
   For the integration story leave **S3 with no sensors** and give **S5 only the exit sensor** —
   that is what the twin will have to infer.
3. **File → Drivers → Modbus TCP/IP Server.** Configure: I/O points ≥ 16 inputs / 16 coils. Map the
   tags by dragging them onto addresses exactly as in `configs/factoryio_map.yaml`:

   | tag | Modbus | address |
   |---|---|---|
   | S1 entry / exit sensors | discrete input | 0 / 1 |
   | S2 entry / exit | discrete input | 2 / 3 |
   | S4 entry / exit | discrete input | 6 / 7 |
   | S5 exit | discrete input | 9 |
   | S6 entry / exit | discrete input | 10 / 11 |
   | Emitter (emit) | coil | 0 |
   | S1…S6 conveyor run | coil | 1…6 |

   (S3's sensors, if you placed any, simply stay unmapped.)
4. Note the Windows machine's IP; allow port 502 through its firewall. Press **Play** in Factory I/O.

## Run it

On the Windows box (or anywhere that can reach it), in two terminals:

```
# 1. the "PLC": drives the line at takt, wears S2 after 10 min
python -m loom.plc_stub configs/factoryio_map.yaml --wear S2:600:30

# 2. Loom, read-only, with the control room
python -m loom.server --factoryio configs/factoryio_map.yaml
#    open http://localhost:8000
```

Set `host:` in `configs/factoryio_map.yaml` to the Windows IP if Loom runs elsewhere. The control
room's front lane shows the raw sensor picture (a station is "busy" while its entry eye is covered);
the back lane is the twin, with S3 dashed-purple and its vehicles as outlines.

## Try it without Factory I/O

`loom.fakefactory` is a Modbus/TCP server whose inputs are driven by Loom's own plant simulation
— sensor semantics match a real scene (entry eye high while a part sits at the station, exit eye
high until it actually leaves, every level held long enough to be polled). It is what the tests use.

```
python -m loom.fakefactory configs/factoryio_map.yaml --speed 30            # serves Modbus on 5020 (502 needs root)
python -m loom.server --factoryio configs/factoryio_map.yaml --time-scale 30 --modbus-port 5020
```

## What the test proves (`tests/test_factoryio.py`)

Through a real Modbus/TCP socket at 50 Hz, from photo-eye edges alone: every vehicle tracked end to
end; the dark station S3 reconstructed (state inferred, cycle samples from its neighbours' edges);
the exit-only S5 handled; S2's wear forecast before the line blocks. Zero writes.

## Limits, stated

- Parts are anonymous in Factory I/O, so identity is FIFO order from the first sensor — fine for a
  serial line, not for one with merges. Real plants carry a VIN on an RFID/barcode; the adapter
  would take that from an input register instead.
- Poll rate bounds timestamp resolution (20 ms at 50 Hz). Real PLC timestamps would be better; OPC
  UA with server-side timestamps is the production route (Factory I/O's OPC UA driver is a client,
  which is why Modbus is used here).
- No process parameters come out of a Factory I/O scene, so the quality side of the twin is idle in
  this mode.
