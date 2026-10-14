# Minimal-Change Disruption Repair for a Mixed-Fleet Injection-Molding Plant — short report

## 1. Problem
A weekly plan assigns orders to machines and molds (a job needs both). Disruptions break it: a machine
goes down, cavities of a mold are blocked, a mold becomes unavailable, a rush order arrives, or produced
lots are put on quality hold (limited sorter, 92% yield, shift calendar). Re-planning from scratch fixes lateness
but reshuffles many jobs the floor has already prepared. We want the **cheapest repair with the fewest plan
changes**, plus a plain-language explanation.

## 2. Method
1. **Plant model.** Machines (IMP, SIPA, cap lines), molds with cavities and rate (rate = cycle rate × available cavities),
   changeovers (mold + colour purge asymmetric + resin purge), sorter. Dual resource: a job holds machine and mold.
2. **Exact simulator.** Forward discrete-event simulation with capacity profiles (down/mold-off = 0,
   cavity block = (n−k)/n), pause/resume, changeovers, mold exclusivity, and hold stock (produced − held + sorter credits).
   Every method's plan is replayed through this one simulator, so no solver approximation reaches the metrics.
3. **Impact detection.** Simulate the old plan under the event → late orders, late pieces, affected tasks.
4. **Frozen window.** Work in the next 4 h is kept; a run in progress is split into a frozen head and an open tail.
   A tail keeps its mold (the mold travels with the job).
5. **Repair.** Objective `100·late_orders + 100·late_pieces/1e6 + 0.5·weighted tardiness + 8·plan_changes`.
   Incremental evaluator (re-simulates only machines linked through shared molds), steepest-descent over
   relocate / swap / split-lot moves, then large-neighbourhood search (remove 2–4 related jobs, greedily reinsert;
   only strictly better solutions accepted, stop after 400 non-improving iterations).
6. **Explanation.** For each late order, candidate fixes (move machine, twin mold, run earlier, split shipment,
   sorter overtime) are re-simulated; the cheapest that makes the order on time is written up by template.
7. **Baselines.** Do nothing; manual rule (finish nearest lot, then rush order; move stranded work to the
   earliest-finishing option); EDD and ATC dispatching of all open work; full CP-SAT re-solve (3–12 s, warm-started
   from the old plan); exact CP-SAT repair with deviation penalty (≤ 25 machines only).

## 3. Data
* **Real:** the WK41 workbook (17 machines, 27 molds, 18 orders) → replay of Tuesday 07:00, IMP07 breakdown.
* **Synthetic:** 10/25/50/100 machines, calibrated to WK41 (family mix, mold rates 13.6k–123k pcs/h, run length median ≈ 32–35 h
  vs 31 h real). See `results/dataset/calibration_real_vs_synthetic.csv`.
* **Assumptions (not in the workbook):** cavity counts, mold–machine compatibility, changeover hours, priorities, due dates for undated
  orders, sorter parameters. Std output is read as pieces per hour. Where the human plan itself overruns a due date, the due date
  is moved to plan completion + 12 h so the old plan is on time before the disruption.
* **Synthetic load** is about 113 h of work per machine over the 192 h horizon, versus about 57 h in the real remaining-work snapshot.
  The synthetic plants are therefore busier than WK41.
* The baseline weekly plan for larger plants is mostly the ATC dispatch plan, not CP-SAT: CP-SAT with a 8–30 s limit rarely beat it.

## 4. Evaluation
Sizes 10/25/50/100 machines × 5 disruption types × 30 seeds (600 cases per method; 300 for the exact CP-SAT repair).
Each disruption is redrawn (deterministically) until the old plan gets at least one newly late order.
Means over cases; changes = plan changes as defined in the README.

| Machines | Late orders: do nothing | Late: full re-solve | Late: **repair** | Changes: full re-solve | Changes: **repair** | Repair runtime (median) |
|---|---|---|---|---|---|---|
| 10  | 1.79 | 1.59  | **0.93** | 4.8  | 1.6 | 1.2 s |
| 25  | 1.63 | 5.39  | **0.83** | 15.7 | 1.5 | 1.0 s |
| 50  | 1.37 | 8.01  | **0.70** | 32.5 | 1.3 | 1.1 s |
| 100 | 1.41 | 18.17 | **0.71** | 67.5 | 1.3 | 1.2 s |

Full table: `results/metrics_table.csv`; chart: `results/scaling.png`. The repair is no worse (late orders, then changes) than the full re-solve
in 99% of cases and makes fewer changes in 89.5%; no worse than do-nothing in 88% (it makes more changes in about 89%, the price of fixing lateness);
no worse than the exact CP-SAT repair in 84% (≤ 25 machines).

Real replay (IMP07 down 36 h): do nothing → 2 late orders (17.0 h and 5.7 h late); repair → 0 late orders with **1 change** (move the
remaining 620,075 pcs of order 2100001751 from IMP07 to IMP01). The exact CP-SAT repair finds the same.

## 5. Limitations (read before quoting numbers)
* The full re-solve runs under a fixed 3–12 s budget; with more time or a stronger solver it would improve. The result is "under a
  fixed small budget", not "CP-SAT cannot do this".
* The manual rule is a simple stand-in, nearly as good as doing nothing; it says nothing about real planners.
* The "exact" CP-SAT repair is approximate (outages are non-preemptive obstacles, cavity blocks use an average factor); the heuristic
  sometimes beats it. Both are scored by the exact simulator.
* Time-limited CP-SAT runs are not bit-for-bit reproducible, so individual cases (especially size 10 plans) can differ between runs.
* Some disruptions cannot be fully absorbed (for example a quality hold limited by sorter capacity); the explanation says so.
* Cavity counts, compatibility, changeover times and priorities are assumptions; real values would change absolute numbers.
* Only single disruptions are evaluated, not cascades.
