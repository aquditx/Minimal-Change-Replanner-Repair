"""Baseline repair methods, all evaluated through the same exact simulator (state.run).

  do_nothing      old plan untouched (new work appended where it would finish soonest)
  manual_rule     planner's rule of thumb: finish the nearest lot, then the rush / make-up order; move
                  work off a dead machine / unavailable mold to the option that finishes first
  dispatch_edd    EDD / ATC list scheduling of ALL open work (ignores the old plan)
  full_resolve    CP-SAT re-solve of all open work from scratch (lam = 0: ignores the old plan)
  exact_repair    CP-SAT with unaffected tasks fixed and a deviation penalty (optimal-ish reference for small cases)
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Optional, Tuple

from .cpsat import CpTask, solve_cpsat
from .dispatch import dispatch_core
from .events import MACHINE_DOWN, MOLD_UNAVAILABLE
from .repair import RepairParams, RepairResult, objective_of
from .scenario import RepairState
from .sim import Solution, Task, sequences_from_starts, simulate


def _finish(state: RepairState, sol: Solution, name: str, t0: float, P: RepairParams, meta=None) -> RepairResult:
    acts, met = state.run(sol)
    return RepairResult(name, sol, acts, met, time.time() - t0, objective_of(met, P), meta or {})


def _product(state: RepairState, t: Task) -> str:
    return state.scn.orders[t.order].product


def _options(state: RepairState, t: Task):
    """Options of a task; the tail of a run in progress keeps its mold (mold travels with the job)."""
    opts = state.scn.plant.options(_product(state, t))
    return [(m, k) for (m, k) in opts if k == t.mold] if t.kind == "tail" else opts


def _finish_time(state: RepairState, sol: Solution, acts, m: str, k: str, t: Task) -> Tuple[float, float]:
    """(end, start) if task t were appended to the end of machine m's queue using mold k."""
    plant, ctx = state.scn.plant, state.scn.ctx
    seq = sol.seq.get(m, [])
    if seq:
        last = seq[-1]
        mfree, prev = acts[last].end, sol.tasks[last].mold
    else:
        mfree, prev = state.st0.machine_free.get(m, 0.0), state.st0.machine_mold.get(m)
    mfree = max(mfree, state.st0.mold_free.get(k, 0.0))
    ps = max(ctx.co_end(m, mfree, plant.co(prev, k, m)), t.lb)
    e, _ = ctx.qty_end(m, k, ps, t.qty)
    return e, ps


# ----------------------------------------------------------------------------- do nothing
def do_nothing_result(state: RepairState, P: RepairParams) -> RepairResult:
    t0 = time.time()
    return _finish(state, state.initial_solution(), "do_nothing", t0, P)


# ----------------------------------------------------------------------------- manual rule
def manual_rule(state: RepairState, P: RepairParams) -> RepairResult:
    t0 = time.time()
    plant, e = state.scn.plant, state.scn.event
    sol = state.initial_solution()
    new_ids = [t.id for t in state.new_tasks.values()]
    movable: List[str] = list(new_ids)
    # work stranded on a dead machine / unavailable mold is relocated (planner phones around)
    for tid, t in list(sol.tasks.items()):
        if t.kind == "new" or tid in new_ids:
            continue
        if (e.type == MACHINE_DOWN and t.machine == e.machine) or (e.type == MOLD_UNAVAILABLE and t.mold == e.mold):
            movable.append(tid)
    for tid in movable:  # remove, then re-insert one by one
        t = sol.tasks[tid]
        sol.seq[t.machine].remove(tid)
    for tid in movable:
        t = sol.tasks[tid]
        acts = simulate(state.scn.ctx, list(state.scn.plant.machines), sol.seq, sol.tasks, state.st0)
        best = None
        for (m, k) in _options(state, t):
            if e.type == MACHINE_DOWN and m == e.machine:
                continue
            if e.type == MOLD_UNAVAILABLE and k == e.mold:
                continue
            end, _s = _finish_time(state, sol, acts, m, k, t)
            if best is None or end < best[0]:
                best = (end, m, k)
        if best is None:  # nothing else possible: put back where it was
            m, k = t.machine, t.mold
        else:
            _, m, k = best
        # nearest-lot rule: after the first open task of that machine (finish the lot in progress), else first
        seq = sol.seq.setdefault(m, [])
        if tid in new_ids:
            pos = 1 if (seq and sol.tasks[seq[0]].kind == "tail") else 0
            seq.insert(pos, tid)
        else:
            seq.append(tid)
        sol.tasks[tid] = t.clone(machine=m, mold=k)
    return _finish(state, sol, "manual_rule", t0, P)


# ----------------------------------------------------------------------------- EDD / ATC dispatch
def dispatch(state: RepairState, P: RepairParams, rule: str = "ATC", K: float = 2.0) -> Solution:
    base = state.initial_solution()
    return dispatch_core(state.scn.plant, state.scn.ctx, state.scn.orders, base.tasks, state.st0, state.t_r, rule, K,
                         options_fn=lambda t: _options(state, t))


def dispatch_result(state: RepairState, P: RepairParams, rule: str = "ATC") -> RepairResult:
    t0 = time.time()
    return _finish(state, dispatch(state, P, rule), f"dispatch_{rule.lower()}", t0, P)


# ----------------------------------------------------------------------------- CP-SAT based
def _cp_tasks(state: RepairState, sol: Solution, fixed_ids=(), fixed_acts=None) -> List[CpTask]:
    orders, plant = state.scn.orders, state.scn.plant
    cps = []
    for tid, t in sol.tasks.items():
        o = orders[t.order]
        ct = CpTask(id=tid, order=t.order, qty=t.qty, due=o.due, weight=o.weight, lb=t.lb,
                    options=_options(state, t), ref_machine=t.ref_machine, ref_start=t.ref_start,
                    only_machine_ref=t.kind in ("tail", "head"))
        if tid in fixed_ids and fixed_acts is not None:
            ct.fixed = (t.machine, t.mold, fixed_acts[tid].ps)
        cps.append(ct)
    return cps


def _from_cp(state: RepairState, sol0: Solution, assign) -> Solution:
    tasks, starts = {}, {}
    for tid, (m, k, s) in assign.items():
        tasks[tid] = sol0.tasks[tid].clone(machine=m, mold=k)
        starts[tid] = s
    return Solution(tasks, sequences_from_starts(tasks, starts))


def full_resolve(state: RepairState, P: RepairParams, time_limit: float, workers: int = 1) -> RepairResult:
    t0 = time.time()
    sol0 = state.initial_solution()
    acts0, _ = state.run(sol0)  # warm start from the old plan (best case for the baseline)
    hints = {tid: (t.machine, t.mold, acts0[tid].ps) for tid, t in sol0.tasks.items()}
    res = solve_cpsat(state.scn.plant, _cp_tasks(state, sol0), state.st0, state.scn.ctx, state.t_r, time_limit,
                      objective="repair", lam=0.0, seed=P.seed, workers=workers, w1=P.w1, w2=P.w2, w3=P.w3,
                      hints=hints)
    if res is None or not res.assign:
        r = dispatch_result(state, P, "EDD")
        r.method = "full_resolve"
        r.meta = {"status": "NO_SOLUTION_FALLBACK_EDD"}
        r.runtime = time.time() - t0
        return r
    return _finish(state, _from_cp(state, sol0, res.assign), "full_resolve", t0, P,
                   {"status": res.status, "bound": res.best_bound})


def exact_repair(state: RepairState, P: RepairParams, time_limit: float, workers: int = 1) -> RepairResult:
    """CP-SAT repair: tasks not affected by the disruption are pinned to their do-nothing times."""
    t0 = time.time()
    sol0 = state.initial_solution()
    acts0, met0 = state.run(sol0)
    late_orders = {o for o, r in met0.per_order.items() if r.late}
    free = {tid for tid, t in sol0.tasks.items() if tid in state.affected or t.order in late_orders
            or t.kind == "new"}
    fixed = set(sol0.tasks) - free
    hints = {tid: (t.machine, t.mold, acts0[tid].ps) for tid, t in sol0.tasks.items()}
    kw = dict(objective="repair", lam=P.lam, seed=P.seed, workers=workers, w1=P.w1, w2=P.w2, w3=P.w3, hints=hints)
    res = solve_cpsat(state.scn.plant, _cp_tasks(state, sol0, fixed, acts0), state.st0, state.scn.ctx, state.t_r,
                      time_limit * 0.5, **kw)
    pinned = bool(res and res.assign)
    if not pinned:  # pinned start times clash with the model's outage approximation -> unpinned model
        left = max(1.0, time_limit - (time.time() - t0))
        res = solve_cpsat(state.scn.plant, _cp_tasks(state, sol0), state.st0, state.scn.ctx, state.t_r, left, **kw)
    if res is None or not res.assign:
        r = do_nothing_result(state, P)
        r.method = "exact_repair"
        r.meta = {"status": "NO_SOLUTION_FALLBACK_DO_NOTHING"}
        r.runtime = time.time() - t0
        return r
    return _finish(state, _from_cp(state, sol0, res.assign), "exact_repair", t0, P,
                   {"status": res.status, "bound": res.best_bound})
