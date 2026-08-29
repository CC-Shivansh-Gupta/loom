# Loom — assembly-line digital twin prototype

Four layers, one event schema, a hard wall between truth and belief.

| layer | module | sees |
|---|---|---|
| 1 Plant (ground truth) | `loom/plant.py` | everything |
| 2 Sensors | `loom/sensors.py` | plant events → forwards a subset |
| 3 Loom (twin) | `loom/twin.py` | only what sensors forward; tags every value ● measured / ◐ inferred / ○ simulated |
| 4 Evaluator | `loom/evaluator.py` | plant **and** twin; scores the twin |

Line topology is data: `configs/*.yaml`.

```
python -m loom.run configs/line_basic.yaml --hours 2
python -m loom.run configs/line_slow_b3.yaml --hours 2 --trace
python -m pytest -q
```
