# Minimal-Change Disruption Repair Planner (injection molding)

When a weekly plan is hit by a disruption (machine down, blocked cavities, mold unavailable, rush order,
quality hold), find the **cheapest fix that changes as little of the plan as possible**, and explain it in
plain language.

## Install
```
pip install ortools pandas numpy matplotlib openpyxl pytest
```
Python 3.10+. Tested on 3.13 (Linux) and 3.14 (Windows).

## Layout
| Path | What it is |
|---|---|
| `repairplan/model.py` | plant model: machines, molds, orders, changeover model, sorter |
| `repairplan/synth.py` | synthetic plant generator (10-100+ machines), calibrated to the WK41 plant |
| `repairplan/realdata.py`, `scripts/extract_real_data.py` | the real WK41 plant (`data/real_wk41.json`) |
| `repairplan/sim.py` | exact forward event simulator (capacity profiles, changeovers, mold exclusivity, hold stock) |
| `repairplan/cpsat.py`, `plans.py` | CP-SAT model and the weekly baseline plan (CP-SAT warm-started by ATC dispatch) |
| `repairplan/events.py`, `scenario.py` | seeded disruption generator, impact detection, frozen/open split |
| `repairplan/repair.py` | **minimal-change repair** (local search + LNS, moves: relocate, swap, split) |
| `repairplan/methods.py`, `dispatch.py` | baselines: do-nothing, manual rule, EDD/ATC, full CP-SAT re-solve, exact CP-SAT repair |
| `repairplan/explain.py` | plain-language "why late / cheapest fix" and a step list of the repair |
| `repairplan/viz.py` | before / after Gantt charts |
| `repairplan/experiments.py` + `scripts/` | evaluation grid and summaries |

## Use it from code (e.g. a Streamlit app)
```python
from repairplan.cases import real_case, synth_base, synth_case
from repairplan.scenario import prepare_repair_state
from repairplan.repair import minimal_change_repair, RepairParams
from repairplan.explain import explain_orders, describe_repair
from repairplan.viz import before_after

plant, old, scn, imp, notes = real_case(down_hours=36)        # real Tuesday 07:00 replay
st = prepare_repair_state(scn, imp, frozen_h=4.0)
res = minimal_change_repair(st, RepairParams(lam=8.0))        # lam = cost of one plan change
for e in explain_orders(st): print(e.text)
print(describe_repair(st, res))
before_after(scn, imp, st, res, "gantt.png")
```
Synthetic: `base = synth_base(25, seed, plan_time)` then `synth_case(25, seed, "rush_order", plan_time, 1, base)`.
Event types: `machine_down, cavity_block, mold_unavailable, rush_order, quality_hold`.

## Scripts
```
python scripts/run_demo.py                      # real replay + one synthetic example per event -> results/demo/
python scripts/make_dataset.py                  # synthetic plants as CSV + real-vs-synthetic table -> results/dataset/
python scripts/run_experiments.py --seeds 30    # full grid (about 1 h on 2 cores) -> results/results_raw.csv
python scripts/summarize.py results             # tables + scaling chart
python -m pytest tests -q                       # 12 tests, ~15 s
```
`run_experiments.py` appends to `results_raw.csv`; delete the file before a fresh run (the summary removes duplicates anyway).

## Repair objective
`w1 * late_orders + w2 * late_pieces/1e6 + w3 * priority-weighted tardiness + lam * plan_changes`
(defaults 100, 100, 0.5, 8). A plan change = a task on a different machine, started more than 0.5 h away from
its old start, or split. The tail of a run already in progress resuming on the same machine is not a change.
