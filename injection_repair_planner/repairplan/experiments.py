"""Evaluation grid: sizes x disruption types x seeds, every method replayed through the same simulator."""
from __future__ import annotations

import csv
import json
import os
import time
from typing import Dict, List, Optional

from . import methods as M
from .cases import synth_base
from .events import EVENT_TYPES
from .explain import explain_orders
from .repair import RepairParams, minimal_change_repair
from .scenario import make_scenario, prepare_repair_state

SIZES = (10, 25, 50, 100)
# wall-clock budgets (seconds) per plant size; baseline plan / full re-solve / exact repair (<=25 machines only)
PLAN_T = {10: 5.0, 25: 8.0, 50: 15.0, 100: 30.0}
RESOLVE_T = {10: 3.0, 25: 5.0, 50: 8.0, 100: 12.0}
EXACT_T = {10: 3.0, 25: 5.0}
LNS_T = {10: 2.0, 25: 2.5, 50: 3.0, 100: 4.0}
FIELDS = ["size", "seed", "event", "method", "late_orders", "late_qty", "changes", "runtime_s", "objective",
          "wtard_h", "late_before", "n_orders", "n_tasks_open", "status"]


def run_job(args) -> dict:
    size, seed = args
    P_base = RepairParams(time_limit=LNS_T[size], seed=seed)
    rows: List[dict] = []
    samples: List[dict] = []
    t0 = time.time()
    plant, old, notes, res = synth_base(size, seed, PLAN_T[size], 1)
    plan_status = res.status if res else "none"
    for et in EVENT_TYPES:
        scn, imp = make_scenario(plant, old, et, seed)
        st = prepare_repair_state(scn, imp, 4.0)
        P = P_base
        results = []
        steps = [("do_nothing", lambda: M.do_nothing_result(st, P)),
                 ("manual_rule", lambda: M.manual_rule(st, P)),
                 ("dispatch_edd", lambda: M.dispatch_result(st, P, "EDD")),
                 ("dispatch_atc", lambda: M.dispatch_result(st, P, "ATC")),
                 ("full_resolve", lambda: M.full_resolve(st, P, RESOLVE_T[size])),
                 ("minimal_change_repair", lambda: minimal_change_repair(st, P))]
        if size in EXACT_T:
            steps.append(("exact_repair", lambda: M.exact_repair(st, P, EXACT_T[size])))
        for name, fn in steps:
            try:
                results.append(fn())
            except Exception as ex:  # one failing method must not kill the whole grid; recorded in the CSV
                dn = M.do_nothing_result(st, P)
                dn.method, dn.meta = name, {"status": f"ERROR_FALLBACK_DO_NOTHING: {type(ex).__name__}: {str(ex)[:80]}"}
                results.append(dn)
        for r in results:
            m = r.metrics
            rows.append(dict(size=size, seed=seed, event=et, method=r.method, late_orders=m.late_orders,
                             late_qty=round(m.late_qty), changes=m.changes, runtime_s=round(r.runtime, 3),
                             objective=round(r.objective, 2), wtard_h=round(m.wtard, 2),
                             late_before=imp.metrics.late_orders, n_orders=len(scn.orders),
                             n_tasks_open=len(st.initial_solution().tasks),
                             status=str(r.meta.get("status", ""))))
        if seed < 2 and size <= 25:
            for e in explain_orders(st, P, max_orders=3):
                samples.append(dict(size=size, seed=seed, event=et, order=e.order, text=e.text))
    return {"rows": rows, "samples": samples, "plan_status": plan_status, "size": size, "seed": seed,
            "seconds": time.time() - t0, "due_notes": len(notes)}


def append_rows(path: str, rows: List[dict]) -> None:
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)
