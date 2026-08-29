# Forecaster tuning (step 2)

Sweep of `Forecaster` parameters against two criteria, 5 seeds each:

- **FA/8h**: alerts raised on `line_stochastic.yaml` (healthy, 5% CV) over 8 h — must be ~0.
- **lead**: minutes between first alert on B3 and B2 actually blocking, on `line_ramp_b3.yaml`
  (56→80 s ramp over 20 min starting at 30 min).

| window | tstat | over_z | raise_after | FA/8h | lead (min) | eta err (min) |
|---|---|---|---|---|---|---|
| 10 | 3 | 2 | 1 | 36.8 | 10–13 | ±3 |
| 10 | 4 | 3 | 3 | 0.0 | 3–7 | ±3 |
| 15 | 4 | 2 | 3 | 0.2 | 6–12 | ±1 |
| 15 | 5 | 2 | 3 | 0.0 | 6–7.5 | +1..+4 |
| **20** | **4** | **2** | **3** | **0.0** | **6–11** | **±2** |
| 20 | 5 | 3 | 3 | 0.0 | 4–8.5 | ±4 |

Naive trend test (first row) is the alert-fatigue failure mode from the brief: ~5,800
significance tests per shift means t≥3 fires by chance dozens of times. Two fixes did the work:

1. **Standard-error test** for "already over takt" (fitted cycle must sit `over_z` SEs above takt),
   instead of a raw comparison.
2. **Persistence rule** (`RAISE_AFTER=3`): condition must hold on three consecutive cycles.
   Cut false alarms ~10x on its own at any threshold.

Chosen defaults: window 20, tstat 4, over_z 2, raise_after 3.
Re-run: `python3 scratch/sweep.py` (kept out of the repo; recreate from this table if needed).
