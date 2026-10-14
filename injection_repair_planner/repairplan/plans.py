"""Building old plans: CP-SAT baseline plan and the planners' (human) plan for the real plant."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .cpsat import CpTask, solve_cpsat
from .dispatch import dispatch_core
from .model import Plant
from .sim import (SimContext, Solution, State0, Task, initial_state, sequences_from_starts, simulate_solution)


def task_id(order_id: str) -> str:
    return f"T:{order_id}"


def solution_from_assignment(plant: Plant, assign: Dict[str, Tuple[str, str, float]], orders=None) -> Solution:
    """assign: task id -> (machine, mold, planned start). Replays without events to publish actual starts."""
    tasks, starts = {}, {}
    orders = orders or plant.orders
    for tid, (m, k, s) in assign.items():
        oid = tid[2:] if tid.startswith("T:") else tid
        o = orders[oid]
        tasks[tid] = Task(id=tid, order=oid, qty=o.qty, machine=m, mold=k, lb=s)
        starts[tid] = s
    sol = Solution(tasks, sequences_from_starts(tasks, starts))
    return publish(plant, sol)


def publish(plant: Plant, sol: Solution) -> Solution:
    """Replay with no events; set each task's planned start and reference to the replayed start."""
    ctx = SimContext(plant)
    acts = simulate_solution(ctx, sol)
    for tid, t in sol.tasks.items():
        a = acts[tid]
        t.lb = a.ps
        t.ref_machine = t.machine
        t.ref_start = a.ps
    for m in sol.seq:
        sol.seq[m].sort(key=lambda x: (acts[x].ps, x))
    return sol


def baseline_plan(plant: Plant, time_limit: float = 10.0, seed: int = 0, workers: int = 1):
    """Weekly plan: CP-SAT (min weighted tardiness) warm-started from an ATC dispatch plan.
    The better of the two after exact replay is returned. Returns (Solution, CpResult | None)."""
    ctx = SimContext(plant)
    st0 = initial_state(plant)
    ts = {task_id(o.id): Task(id=task_id(o.id), order=o.id, qty=o.qty, machine="", mold="", lb=o.release)
          for o in plant.orders.values()}
    disp = dispatch_core(plant, ctx, plant.orders, ts, st0, 0.0, "ATC")
    dacts = simulate_solution(ctx, disp)
    disp_assign = {tid: (t.machine, t.mold, dacts[tid].ps) for tid, t in disp.tasks.items()}
    cps = []
    for o in plant.orders.values():
        cps.append(CpTask(id=task_id(o.id), order=o.id, qty=o.qty, due=o.due, weight=o.weight,
                          lb=o.release, options=plant.options(o.product)))
    res = solve_cpsat(plant, cps, st0, ctx, 0.0, time_limit, objective="wtard", seed=seed, workers=workers,
                      hints=disp_assign)
    cand = [("dispatch", solution_from_assignment(plant, disp_assign))]
    if res is not None and res.assign:
        cand.append(("cpsat", solution_from_assignment(plant, res.assign)))

    def score(sol):
        acts = simulate_solution(ctx, sol)
        return sum(plant.orders[t.order].weight * max(0.0, acts[tid].end - plant.orders[t.order].due)
                   for tid, t in sol.tasks.items())
    name, best = min(cand, key=lambda c: score(c[1]))
    if res is not None:
        res.status = f"{res.status}|plan_from={name}"
    return best, res


def human_plan(plant: Plant, human_seq: Dict[str, List[str]], lbs: Dict[str, float]) -> Solution:
    """The planners' weekly plan: machine sequence as in the workbook, planned start = StartOn day."""
    tasks, starts = {}, {}
    usage: Dict[str, int] = {}
    for m, seq in human_seq.items():
        for oid in seq:
            o = plant.orders[oid]
            opts = [k for (mm, k) in plant.options(o.product) if mm == m]
            if not opts:
                raise ValueError(f"order {oid} ({o.product}) has no mold compatible with {m}")
            k = min(opts, key=lambda x: (usage.get(x, 0), x))  # twin molds: spread over machines
            usage[k] = usage.get(k, 0) + 1
            tid = task_id(oid)
            tasks[tid] = Task(id=tid, order=oid, qty=o.qty, machine=m, mold=k, lb=lbs.get(oid, 0.0))
            starts[tid] = lbs.get(oid, 0.0) + len(starts) * 1e-6
    sol = Solution(tasks, sequences_from_starts(tasks, starts))
    return publish(plant, sol)


def calibrate_due_dates(plant: Plant, sol: Solution, buffer_h: float = 12.0) -> List[str]:
    """Where the human plan overruns an order's due date (day-level FinishOn), move the due date to the
    plan completion + buffer, so that the old plan is on time before any disruption. Returns notes."""
    ctx = SimContext(plant)
    acts = simulate_solution(ctx, sol)
    notes = []
    for tid, t in sol.tasks.items():
        o = plant.orders[t.order]
        if acts[tid].end > o.due:
            new = round(acts[tid].end + buffer_h, 1)
            notes.append(f"{o.id}: due moved {o.due:.0f}h -> {new:.0f}h (plan completes at {acts[tid].end:.0f}h)")
            o.due = new
    return notes
