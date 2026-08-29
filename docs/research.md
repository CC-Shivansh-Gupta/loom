# Research notes: landscape, factors, differentiators

Compiled 2026-08-29. Sources at the end.

## 1. Existing solutions

| Category | Examples | Strength | Gap Loom targets |
|---|---|---|---|
| Offline discrete-event simulation | Siemens Tecnomatix Plant Simulation, Simul8, AnyLogic, FlexSim, Simio | What-if scenarios, throughput/buffer analysis, automatic bottleneck ID on a *hand-built* model | Not connected to live data; needs a simulation engineer to build and keep the model current; "requires model validation, complex to build and maintain" |
| Real-time OEE / line analytics | Vorne XL, MachineMetrics, Fabrico | Takt vs actual pace, downtime Pareto, micro-stop pattern mining | Need a sensor at every monitored point — "you need to wire sensors to the bottleneck; moving them is a hassle if the constraint shifts"; reactive, no prediction; siloed (MachineMetrics misses non-machine bottlenecks) |
| Manual-process capture | Tulip | Operator app interactions replace sensors on manual stations; step-level cycle times | Requires operator engagement; no line-flow model |
| Academic bottleneck detection | utilisation, active-period (Roser 2001), turning-point, queue-length, arrow, bottleneck-walk, data-driven shifting-bottleneck from MES logs | Rigorous *detection* of the momentary bottleneck from complete state logs | The 2023 systematic review lists prediction and operation with incomplete data as open problems |
| Soft / virtual sensors | process industry, buildings, nuclear | Infer an unmeasured quantity from neighbouring measurements inside a model | Almost never applied to discrete assembly *flow state* |

**Positioning:** Loom sits between the offline DES tools (predictive, but offline and expert-built) and the OEE tools (live, but sensor-hungry and reactive): a live, config-built DES that runs forward from *believed* state, on lines where a third of stations may be dark.

## 2. Factors a configurable line model must cover

From ISA-95 (equipment hierarchy: enterprise → site → area → line → work cell/unit), AAS (asset = set of submodels), and the mixed-model assembly literature:

**Topology**
- Serial flow with buffers (capacity, and number of parallel FIFO lanes — paint entry buffers are multi-lane and allow resequencing)
- Parallel stations for one operation (n workers/robots sharing a queue)
- Rework loops from inspection back to a repair bay and re-entry
- Zones / segments with different takt (body shop is often decoupled from final assembly by a large buffer)

**Station**
- Type (robot weld, manual fit, paint booth, inspection, test) → defaults for sensors, monitored process parameters, failure modes
- Nominal cycle time, variation (CV), per-variant cycle multipliers
- Instrumentation profile: full PLC telemetry / cycle timestamps only / manual checklist (sampled, delayed) / dark
- Monitored process parameters with spec limits (torque, weld current, booth temperature) — the quality side
- Degradation modes: step failure, ramp (tool wear), intermittent micro-stops

**Product**
- Model variants with mix shares and station-specific cycle multipliers
- Build record per vehicle: stations visited, timestamps, measured/inferred parameters

**Operations**
- Shifts and breaks (later), scheduled maintenance windows (the only time sensors can be retrofitted)
- Source policy (release at takt vs push)

**Data quality**
- Noise, latency, dropouts per sensor profile — the model must degrade gracefully, and the twin must say how sure it is

## 3. Role views

From dashboard-by-role guidance:

| Role | Horizon | Refresh | Needs | Action driven |
|---|---|---|---|---|
| Operator / technician | now, my station | seconds | my station state, my cycle vs takt, my next alert, is my data measured or guessed | fix it now, call maintenance |
| Line supervisor | this shift | minutes | line andon, forecast bottlenecks with ETA, which buffers are filling, output vs target | rebalance, pull a floater, hold a batch |
| Quality engineer | day | hourly | drift trends, at-risk vehicle sets, containment scope, first-pass yield | targeted hold, extra inspection |
| Maintenance | week | daily | degradation trends per asset, time-to-threshold, MTBF | schedule intervention in a window |
| Plant manager | week/month | daily | throughput trend, loss Pareto, twin trust ledger (lead time, false alarms), dark-station coverage | invest in sensors where it pays, staffing |
| Executive | quarter | monthly | multi-line/plant benchmark, ROI of the twin, rollout plan | rollout decision |

All views read the same twin; they differ in aggregation, horizon, and which provenance tags they surface.

## 4. Differentiators (each demonstrable in the prototype)

1. **Partial instrumentation is the default case, not an edge case.** Dark stations in config; the twin infers their state and labels it ◐.
2. **Prediction with a published error rate.** Every alert has an ETA and confidence; the evaluator scores lead time and false alarms against ground truth. The trust ledger is a first-class view.
3. **Flow and quality in one graph.** Build records connect a parameter drift to the set of vehicles built under it → targeted containment instead of a line stop.
4. **Onboarding by configuration.** ISA-95-shaped YAML with station-type and sensor-profile libraries; a scenario is an `extends:` diff.
5. **Human-in-the-loop by construction.** Recommendations carry evidence; only reversible actions are automatic.
6. **Sensor-investment guidance.** Because the twin knows where inference is weakest, it can rank which dark station to instrument next — turns "uneven sensor coverage" into a roadmap.

## Sources

- [Siemens Plant Simulation](https://www.siemens.com/en-us/products/tecnomatix/plant-simulation-software/)
- [Simio manufacturing digital twin](https://www.simio.com/manufacturing-digital-twin-simulation/)
- [Best manufacturing bottleneck analysis software 2026 (Fabrico review of Vorne, Simul8, MachineMetrics, Tulip)](https://www.fabrico.io/blog/best-manufacturing-bottleneck-analysis-software-2026-review/)
- [Best digital twin software for manufacturing 2026](https://www.fabrico.io/blog/best-digital-twin-software-manufacturing/)
- [Throughput bottleneck detection in manufacturing: systematic review (2023)](https://www.tandfonline.com/doi/full/10.1080/21693277.2023.2283031)
- [Shifting bottleneck detection, Roser et al. (WSC 2002)](https://www.allaboutlean.com/wp-content/uploads/2015/02/2002_WSC-Shifting-Bottleneck-Detection-Preprint.pdf)
- [Active period method — AllAboutLean](https://www.allaboutlean.com/active-period-method/)
- [Data-driven shifting bottleneck detection](https://www.tandfonline.com/doi/full/10.1080/23311916.2016.1239516)
- [Bottleneck prediction using active period + buffer inventories](https://www.academia.edu/60353104/Bottleneck_Prediction_Using_the_Active_Period_Method_in_Combination_with_Buffer_Inventories)
- [ISA-95 hierarchical structures](https://documentation.iconics.com/v11/Content/Assets/ISA95-and-ISA88-hierarchical-structures.htm)
- [ISA-95 for MES architectures](https://www.symestic.com/en-us/blog/mes/isa95)
- [Digital twin and the Asset Administration Shell (SoSyM 2024)](https://link.springer.com/article/10.1007/s10270-024-01255-0)
- [IIC/Plattform Industrie 4.0 joint whitepaper: Digital Twin and AAS](https://www.iiconsortium.org/pdf/Digital-Twin-and-Asset-Administration-Shell-Concepts-and-Application-Joint-Whitepaper.pdf)
- [Online resequencing of buffers for automotive assembly lines](https://www.sciencedirect.com/science/article/abs/pii/S0360835222008452)
- [Optimizing car resequencing on mixed-model assembly lines](https://arxiv.org/pdf/2507.17422)
- [What are soft sensors — ATS](https://www.advancedtech.com/blog/soft-sensors/)
- [Virtual sensing digital twin framework (arXiv 2410.13762)](https://arxiv.org/pdf/2410.13762)
- [Manufacturing dashboard templates by role 2026](https://ifactoryapp.com/analytics-reporting/top-manufacturing-dashboard-templates-role-2026)
- [Manufacturing dashboard UX design guide](https://fuselabcreative.com/manufacturing-dashboard-ux-design/)
- [Guidewheel: OEE dashboards that drive floor action](https://www.guidewheel.com/blog/oee-dashboard)
