"""Scenarios: an old plan + one disruption event; impact detection; the frozen/open split for repair."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .events import (CAVITY_BLOCK, MACHINE_DOWN, MOLD_UNAVAILABLE, QUALITY_HOLD, RUSH_ORDER, Event,
                     generate_event)
from .model import Order, Plant
from .sim import (CHANGE_TOL, Actual, HoldInfo, Metrics, SimContext, Solution, State0, Task, evaluate,
                  initial_state, simulate, simulate_solution)
from .sorter import release_schedule


@dataclass
class Scenario:
    plant: Plant
    old: Solution
    base_ctx: SimContext
    base_acts: Dict[str, Actual]
    event: Event
    ctx: SimContext
    orders: Dict[str, Order]
    new_tasks: List[Task]  # work created by the event: rush order task / quality-hold make-up task
    t_event: float
    base_metrics: Metrics = None


def build_scenario(plant: Plant, old: Solution, event: Event) -> Scenario:
    base_ctx = SimContext(plant)
    base_acts = simulate_solution(base_ctx, old)
    orders = dict(plant.orders)
    new_tasks: List[Task] = []
    holds: Dict[str, HoldInfo] = {}
    events: List[Event] = []
    if event.type == RUSH_ORDER:
        o = event.order
        orders[o.id] = o
        new_tasks.append(Task(id=f"T:{o.id}", order=o.id, qty=o.qty, machine="", mold="",
                              lb=max(o.release, event.t), kind="new"))
    elif event.type == QUALITY_HOLD:
        o = orders[event.hold_order]
        lots = list(event.backlog) + [{"id": "HOLD", "qty": event.hold_qty, "arrival": event.t, "priority": o.priority}]
        credits = release_schedule(plant.sorter, lots)["HOLD"]
        good = sum(q for _, q in credits)
        makeup = max(0.0, event.hold_qty - good)
        holds[o.id] = HoldInfo(o.id, event.t, event.hold_qty, credits, makeup)
        if makeup > 1.0:
            new_tasks.append(Task(id=f"U:{o.id}", order=o.id, qty=round(makeup), machine="", mold="",
                                  lb=event.t, kind="new"))
    else:
        events.append(event)
    ctx = SimContext(plant, events, holds)
    sc = Scenario(plant, old, base_ctx, base_acts, event, ctx, orders, new_tasks, event.t)
    sc.base_metrics = evaluate(base_ctx, old.tasks, base_acts, plant.orders)
    return sc


def place_unplanned(scn: Scenario, sol: Solution, acts: Dict[str, Actual]) -> None:
    """Do-nothing handling of new work: append at the end of the machine that would finish it soonest."""
    plant, ctx = scn.plant, scn.ctx
    for t in scn.new_tasks:
        best = None
        for (m, k) in plant.options(plant.orders[t.order].product if t.order in plant.orders else scn.orders[t.order].product):
            seq = sol.seq.get(m, [])
            mfree = max((acts[x].end for x in seq), default=0.0)
            last = max(seq, key=lambda x: acts[x].end) if seq else None
            prev_mold = sol.tasks[last].mold if last else plant.machines[m].init_mold
            co = plant.co(prev_mold, k, m)
            ps = max(ctx.co_end(m, mfree, co), t.lb)
            e, _ = ctx.qty_end(m, k, ps, t.qty)
            if best is None or e < best[0]:
                best = (e, m, k, ps)
        e, m, k, ps = best
        nt = t.clone(machine=m, mold=k)
        sol.tasks[nt.id] = nt
        sol.seq.setdefault(m, []).append(nt.id)
        acts[nt.id] = Actual(ps, ps, e, ())  # placeholder; caller re-simulates


def do_nothing(scn: Scenario) -> Tuple[Solution, Dict[str, Actual]]:
    """The old plan left untouched (new work appended at the end). Returns (solution, simulated actuals)."""
    sol = scn.old.copy()
    sol.tasks = {k: v.clone() for k, v in sol.tasks.items()}
    acts = simulate_solution(scn.ctx, sol)
    if scn.new_tasks:
        place_unplanned(scn, sol, acts)
        acts = simulate_solution(scn.ctx, sol)
    return sol, acts


@dataclass
class Impact:
    solution: Solution
    acts: Dict[str, Actual]
    metrics: Metrics
    late: List[dict]
    affected: Set[str]


def detect_impact(scn: Scenario) -> Impact:
    """Forward-simulate the old plan under the event: late orders, late quantity, affected tasks."""
    sol, acts = do_nothing(scn)
    met = evaluate(scn.ctx, sol.tasks, acts, scn.orders)
    late = []
    for oid, r in met.per_order.items():
        if r.late:
            o = scn.orders[oid]
            ms = sorted({sol.tasks[t].machine for t in sol.tasks if sol.tasks[t].order == oid})
            late.append({"order": oid, "product": o.product, "machines": ms, "due": o.due, "completion": r.completion,
                         "tardiness_h": r.tard, "late_qty": r.late_qty, "priority": o.priority,
                         "was_late_in_old_plan": scn.base_metrics.per_order.get(oid).late if oid in scn.base_metrics.per_order else False})
    late.sort(key=lambda d: (-d["priority"], -d["tardiness_h"]))
    e = scn.event
    aff: Set[str] = set()
    for tid, a in acts.items():
        b = scn.base_acts.get(tid)
        if b is None:
            aff.add(tid)  # new work
        elif abs(a.ps - b.ps) > CHANGE_TOL or (a.end == float("inf")) or abs(a.end - b.end) > CHANGE_TOL:
            aff.add(tid)
    for tid, t in sol.tasks.items():  # downstream on the same machine / mold after the event
        b = scn.base_acts.get(tid)
        if b is None:
            continue
        if e.type == MACHINE_DOWN and t.machine == e.machine and b.end > e.t:
            aff.add(tid)
        if e.type in (MOLD_UNAVAILABLE, CAVITY_BLOCK) and t.mold == e.mold and b.end > e.t:
            aff.add(tid)
        if e.type == QUALITY_HOLD and t.order == e.hold_order:
            aff.add(tid)
    return Impact(sol, acts, met, late, aff)


def make_scenario(plant: Plant, old: Solution, etype: str, seed: int, require_impact: bool = True,
                  max_tries: int = 12) -> Tuple[Scenario, Impact]:
    """Seeded scenario. With require_impact the event is redrawn (deterministically) until the old plan
    gets at least one order that was on time before the event but is late under it."""
    base_ctx = SimContext(plant)
    base_acts = simulate_solution(base_ctx, old)
    last = None
    for attempt in range(max_tries):
        ev = generate_event(plant, old.tasks, base_acts, etype, seed, attempt)
        scn = build_scenario(plant, old, ev)
        imp = detect_impact(scn)
        newly = [l for l in imp.late if not l["was_late_in_old_plan"]]
        last = (scn, imp)
        if newly or not require_impact:
            return scn, imp
    return last


# ----------------------------------------------------------------------------- repair state
@dataclass
class RepairState:
    scn: Scenario
    t_r: float
    frozen: Dict[str, Task]
    hist_acts: Dict[str, Actual]
    open_tasks: Dict[str, Task]  # existing open work (including tails of in-progress runs)
    new_tasks: Dict[str, Task]  # unplaced new work (machine == '')
    st0: State0
    seq0: Dict[str, List[str]]  # do-nothing sequences of open + new tasks
    affected: Set[str]
    order_tasks_frozen: Dict[str, List[str]] = field(default_factory=dict)

    def merged(self, sol: Solution) -> Tuple[Dict[str, Task], Dict[str, List[str]]]:
        tasks = dict(self.frozen)
        tasks.update(sol.tasks)
        ot: Dict[str, List[str]] = {}
        for tid, t in tasks.items():
            ot.setdefault(t.order, []).append(tid)
        return tasks, ot

    def run(self, sol: Solution) -> Tuple[Dict[str, Actual], Metrics]:
        acts = simulate(self.scn.ctx, list(self.scn.plant.machines), sol.seq, sol.tasks, self.st0)
        tasks, ot = self.merged(sol)
        all_acts = dict(self.hist_acts)
        all_acts.update(acts)
        return all_acts, evaluate(self.scn.ctx, tasks, all_acts, self.scn.orders, ot)

    def initial_solution(self) -> Solution:
        tasks = {t: self._open_or_new(t) for s in self.seq0.values() for t in s}
        return Solution(tasks, {m: list(s) for m, s in self.seq0.items()})

    def _open_or_new(self, tid: str) -> Task:
        t = self.open_tasks.get(tid) or self.new_tasks_placed.get(tid)
        return t.clone()

    new_tasks_placed: Dict[str, Task] = field(default_factory=dict)


def prepare_repair_state(scn: Scenario, impact: Impact, frozen_h: float = 4.0) -> RepairState:
    plant = scn.plant
    sol0, acts0 = impact.solution, impact.acts
    t_r = scn.t_event + frozen_h
    frozen: Dict[str, Task] = {}
    hist: Dict[str, Actual] = {}
    open_t: Dict[str, Task] = {}
    new_placed: Dict[str, Task] = {}
    rename: Dict[str, str] = {}
    for tid, t in sol0.tasks.items():
        a = acts0[tid]
        if t.kind == "new":
            new_placed[tid] = t.clone(lb=max(t.lb, t_r))
            continue
        if a.ps < t_r - 1e-9:
            qh = a.qty_by(t_r)
            if a.end > t_r + 1e-9 and qh < t.qty - 1.0:
                head = t.clone(qty=qh, kind="head", frozen=True)
                frozen[tid] = head
                hist[tid] = a.truncate(t_r)
                tail = t.clone(id=tid + "#t", qty=t.qty - qh, lb=t_r, kind="tail", parent=tid, frozen=False)
                open_t[tail.id] = tail
                rename[tid] = tail.id
            else:
                frozen[tid] = t.clone(frozen=True)
                hist[tid] = a
        else:
            open_t[tid] = t.clone(lb=max(t.lb, t_r))
    st0 = State0({m: 0.0 for m in plant.machines}, {m: mc.init_mold for m, mc in plant.machines.items()}, {})
    last_end: Dict[str, float] = {}
    for tid, t in frozen.items():
        a = hist[tid]
        if a.end >= last_end.get(t.machine, -1.0):
            last_end[t.machine] = a.end
            st0.machine_free[t.machine] = a.end
            st0.machine_mold[t.machine] = t.mold
        st0.mold_free[t.mold] = max(st0.mold_free.get(t.mold, 0.0), a.end)
    seq0: Dict[str, List[str]] = {}
    for m, seq in sol0.seq.items():
        for tid in seq:
            tt = rename.get(tid, tid)
            if tt in open_t or tt in new_placed:
                seq0.setdefault(m, []).append(tt)
    aff = {rename.get(x, x) for x in impact.affected}
    new_unplaced = {t.id: t for t in scn.new_tasks}
    return RepairState(scn, t_r, frozen, hist, open_t, new_unplaced, st0, seq0, aff,
                       new_tasks_placed=new_placed)
