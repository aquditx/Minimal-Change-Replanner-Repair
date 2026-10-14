"""Minimal-change repair: freeze the near future, then local search + large-neighbourhood search.

Objective   w1*late_orders + w2*late_qty(per million pcs) + w3*weighted_tardiness_h + lam*plan_changes
(plan change = a task whose machine or start time differs from the old plan; see sim.task_changed).

Moves       shift (re-position in a machine sequence), swap, move to another machine/mold, split lot.
Evaluation  incremental: only the machines connected (through shared molds) to a move are re-simulated.
"""
from __future__ import annotations

import random
import time
from collections import ChainMap, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .scenario import RepairState
from .sim import (Actual, Metrics, OrderResult, Solution, Task, order_result, simulate, task_changed)


@dataclass
class RepairParams:
    frozen_h: float = 4.0
    w1: float = 100.0  # per late order
    w2: float = 100.0  # per million late pieces
    w3: float = 0.5  # per priority-weighted tardiness hour
    lam: float = 8.0  # per plan change
    time_limit: float = 3.0
    max_iter: int = 3000
    seed: int = 0


@dataclass
class Trial:
    total: float
    seq_upd: Dict[str, List[str]]
    task_upd: Dict[str, Optional[Task]]
    new_acts: Dict[str, Actual]
    clos: Set[str]
    res: Dict[str, OrderResult]
    ot_new: Dict[str, List[str]]
    chg_new: Dict[str, int]
    removed_old: Set[str]


class Evaluator:
    def __init__(self, state: RepairState, sol: Solution, P: RepairParams):
        self.state, self.P = state, P
        self.scn = state.scn
        self.ctx = state.scn.ctx
        self.orders = state.scn.orders
        self.cap = self.scn.plant.horizon + 48.0
        self.sol = sol
        self.machines = list(self.scn.plant.machines)
        self.hist_acts = state.hist_acts
        self.order_tasks: Dict[str, List[str]] = defaultdict(list)
        for tid, t in state.frozen.items():
            self.order_tasks[t.order].append(tid)
        for tid, t in sol.tasks.items():
            self.order_tasks[t.order].append(tid)
        self.mold_mach: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for m, seq in sol.seq.items():
            for x in seq:
                self.mold_mach[sol.tasks[x].mold][m] += 1
        self.acts = simulate(self.ctx, self.machines, sol.seq, sol.tasks, state.st0)
        self.res: Dict[str, OrderResult] = {}
        self.ocost: Dict[str, float] = {}
        self.chg: Dict[str, int] = {}
        tasks_all = ChainMap(sol.tasks, state.frozen)
        acts_all = ChainMap(self.acts, self.hist_acts)
        for oid, o in self.orders.items():
            r = order_result(o, self.order_tasks.get(oid, []), tasks_all, acts_all, self.ctx.holds.get(oid), self.cap)
            self.res[oid] = r
            self.ocost[oid] = self._ocost(oid, r)
        for tid, t in sol.tasks.items():
            self.chg[tid] = 1 if task_changed(t, self.acts[tid]) else 0
        self.total = sum(self.ocost.values()) + P.lam * sum(self.chg.values())

    def _ocost(self, oid: str, r: OrderResult) -> float:
        if not r.late:
            return 0.0
        o = self.orders[oid]
        return self.P.w1 + self.P.w2 * r.late_qty / 1e6 + self.P.w3 * o.weight * r.tard

    # ------------------------------------------------------------------ trial / commit
    def trial(self, seq_upd: Dict[str, List[str]], task_upd: Dict[str, Optional[Task]]) -> Trial:
        sol, st = self.sol, self.state
        mod = set(seq_upd)
        added = {k: v for k, v in task_upd.items() if v is not None}
        tview = ChainMap(added, sol.tasks)
        new_molds = {m: {tview[x].mold for x in seq_upd[m]} for m in mod}
        old_molds = {m: {sol.tasks[x].mold for x in sol.seq.get(m, [])} for m in mod}
        clos = set(mod)
        frontier = list(mod)
        seen: Set[str] = set()
        while frontier:
            m = frontier.pop()
            if m in mod:
                ms = new_molds[m] | old_molds[m]
            else:
                ms = {sol.tasks[x].mold for x in sol.seq.get(m, [])}
            for k in ms:
                if k in seen:
                    continue
                seen.add(k)
                users = {mm for mm, c in self.mold_mach.get(k, {}).items() if c > 0 and mm not in mod}
                users |= {mm for mm in mod if k in new_molds[mm]}
                for mm in users:
                    if mm not in clos:
                        clos.add(mm)
                        frontier.append(mm)
        seq_c = {m: (seq_upd[m] if m in mod else sol.seq.get(m, [])) for m in clos}
        new_acts = simulate(self.ctx, sorted(clos), seq_c, tview, st.st0)
        removed = {k for k, v in task_upd.items() if v is None and k in sol.tasks}
        old_touched = {x for m in clos for x in sol.seq.get(m, [])} | removed
        orders_aff = {tview[x].order for x in new_acts} | {sol.tasks[x].order for x in old_touched}
        ot_new: Dict[str, List[str]] = {}
        for oid in orders_aff:
            lst = [x for x in self.order_tasks.get(oid, []) if x not in removed]
            for k, v in added.items():
                if v.order == oid and k not in lst:
                    lst.append(k)
            ot_new[oid] = lst
        tasks_all = ChainMap(added, sol.tasks, st.frozen)
        acts_all = ChainMap(new_acts, self.acts, self.hist_acts)
        res, d_orders = {}, 0.0
        for oid in orders_aff:
            r = order_result(self.orders[oid], ot_new[oid], tasks_all, acts_all, self.ctx.holds.get(oid), self.cap)
            res[oid] = r
            d_orders += self._ocost(oid, r) - self.ocost.get(oid, 0.0)
        chg_new = {x: (1 if task_changed(tview[x], new_acts[x]) else 0) for x in new_acts}
        d_chg = sum(chg_new.values()) - sum(self.chg.get(x, 0) for x in old_touched)
        return Trial(self.total + d_orders + self.P.lam * d_chg, seq_upd, task_upd, new_acts, clos, res, ot_new,
                     chg_new, removed)

    def commit(self, tr: Trial) -> None:
        sol = self.sol
        for m in tr.seq_upd:  # molds bookkeeping
            for x in sol.seq.get(m, []):
                self.mold_mach[sol.tasks[x].mold][m] -= 1
        for k, v in tr.task_upd.items():
            if v is None:
                sol.tasks.pop(k, None)
                self.acts.pop(k, None)
                self.chg.pop(k, None)
            else:
                sol.tasks[k] = v
        for m, s in tr.seq_upd.items():
            sol.seq[m] = list(s)
            for x in s:
                self.mold_mach[sol.tasks[x].mold][m] += 1
        self.acts.update(tr.new_acts)
        self.chg.update(tr.chg_new)
        for oid, lst in tr.ot_new.items():
            self.order_tasks[oid] = lst
        for oid, r in tr.res.items():
            self.res[oid] = r
            self.ocost[oid] = self._ocost(oid, r)
        self.total = tr.total

    def snapshot(self):
        return (self.sol.copy(), dict(self.acts), dict(self.res), dict(self.ocost), dict(self.chg),
                {k: list(v) for k, v in self.order_tasks.items()},
                {k: dict(v) for k, v in self.mold_mach.items()}, self.total)

    def restore(self, snap) -> None:
        sol, acts, res, oc, chg, ot, mm, tot = snap
        self.sol, self.acts, self.res, self.ocost, self.chg = sol, acts, res, oc, chg
        self.order_tasks = defaultdict(list, {k: list(v) for k, v in ot.items()})
        self.mold_mach = defaultdict(lambda: defaultdict(int))
        for k, v in mm.items():
            for m, c in v.items():
                self.mold_mach[k][m] = c
        self.total = tot

    def late_tasks(self) -> List[str]:
        out = []
        for oid, r in self.res.items():
            if r.late:
                out.extend(x for x in self.order_tasks.get(oid, []) if x in self.sol.tasks)
        return out


# ---------------------------------------------------------------------------------- move generators
def _opts(ev: Evaluator, t: Task):
    """Machine/mold options of a task. The tail of a run in progress keeps its mold (the mold travels with the job)."""
    o = ev.orders[t.order]
    opts = ev.scn.plant.options(o.product)
    if t.kind == "tail":
        opts = [(m, k) for (m, k) in opts if k == t.mold]
    return opts


def _lb_for(ev: Evaluator, t: Task) -> float:
    o = ev.orders[t.order]
    return max(ev.state.t_r, o.release, t.lb if t.kind == "new" else 0.0)


def _positions(ev: Evaluator, seq: List[str], due: float) -> List[int]:
    n = len(seq)
    idx = sum(1 for x in seq if ev.orders[ev.sol.tasks[x].order].due <= due)
    return sorted({0, n, max(0, min(n, idx - 1)), min(n, idx), min(n, idx + 1)})


def relocations(ev: Evaluator, tid: str):
    """All (machine, mold, position) re-insertions of one task (including pure re-sequencing)."""
    sol = ev.sol
    t = sol.tasks[tid]
    o = ev.orders[t.order]
    out = []
    lb = _lb_for(ev, t)
    for (m2, k2) in _opts(ev, t):
        base = [x for x in sol.seq.get(m2, []) if x != tid]
        for pos in _positions(ev, base, o.due):
            if m2 == t.machine and k2 == t.mold:
                cur = sol.seq[m2].index(tid)
                if pos == cur:
                    continue
            m1 = t.machine
            nt = t.clone(machine=m2, mold=k2, lb=lb)
            if m2 == m1:
                s = list(base)
                s.insert(pos, tid)
                out.append(({m1: s}, {tid: nt}))
            else:
                s1 = [x for x in sol.seq[m1] if x != tid]
                s2 = list(base)
                s2.insert(pos, tid)
                out.append(({m1: s1, m2: s2}, {tid: nt}))
    return out


def swaps(ev: Evaluator, tid: str, partners: List[str]):
    sol = ev.sol
    a = sol.tasks[tid]
    pa = ev.orders[a.order].product
    out = []
    for other in partners:
        if other == tid or other not in sol.tasks:
            continue
        b = sol.tasks[other]
        pb = ev.orders[b.order].product
        ka = [k for (m, k) in ev.scn.plant.options(pa) if m == b.machine]
        kb = [k for (m, k) in ev.scn.plant.options(pb) if m == a.machine]
        if a.kind == "tail":
            ka = [k for k in ka if k == a.mold]
        if b.kind == "tail":
            kb = [k for k in kb if k == b.mold]
        if not ka or not kb:
            continue
        mk_a = a.mold if a.mold in ka else ka[0]
        mk_b = b.mold if b.mold in kb else kb[0]
        sa, sb = list(sol.seq[a.machine]), list(sol.seq[b.machine])
        ia, ib = sa.index(tid), sb.index(other)
        na = a.clone(machine=b.machine, mold=mk_a, lb=_lb_for(ev, a))
        nb = b.clone(machine=a.machine, mold=mk_b, lb=_lb_for(ev, b))
        if a.machine == b.machine:
            s = list(sa)
            s[ia], s[ib] = other, tid
            out.append(({a.machine: s}, {tid: na, other: nb}))
        else:
            sa[ia] = other
            sb[ib] = tid
            out.append(({a.machine: sa, b.machine: sb}, {tid: na, other: nb}))
    return out


def splits(ev: Evaluator, tid: str, fracs=(0.5, 0.3)):
    """Split a task into two lots; the new lot goes to another (machine, mold) option."""
    sol = ev.sol
    t = sol.tasks[tid]
    o = ev.orders[t.order]
    if t.qty < 2 * 25000:
        return []
    out = []
    n_existing = sum(1 for x in ev.order_tasks.get(t.order, []) if x.startswith(tid + "/s"))
    new_id = f"{tid}/s{n_existing + 1}"
    lb = _lb_for(ev, t)
    for frac in fracs:
        qb = round(t.qty * frac)
        qa = t.qty - qb
        for (m2, k2) in _opts(ev, t):
            if k2 == t.mold:
                continue  # one mold cannot run on two machines at once
            base = list(sol.seq.get(m2, []))
            for pos in _positions(ev, base, o.due):
                ta = t.clone(qty=qa, split=True)
                tb = t.clone(id=new_id, qty=qb, machine=m2, mold=k2, lb=lb, kind="piece", parent=tid, split=True)
                s2 = list(base)
                s2.insert(pos, new_id)
                if m2 == t.machine:
                    out.append(({m2: s2}, {tid: ta, new_id: tb}))
                else:
                    out.append(({m2: s2}, {tid: ta, new_id: tb}))
    return out


def best_trial(ev: Evaluator, moves) -> Optional[Trial]:
    best = None
    for seq_upd, task_upd in moves:
        tr = ev.trial(seq_upd, task_upd)
        if tr.total < (best.total if best else ev.total - 1e-9):
            best = tr
    return best


def descent(ev: Evaluator, deadline: float, focus: List[str], rng: random.Random, use_split: bool = True) -> int:
    improved_count = 0
    improved = True
    while improved and time.time() < deadline:
        improved = False
        order = list(dict.fromkeys(ev.late_tasks() + [x for x in focus if x in ev.sol.tasks]))
        for tid in order:
            if time.time() > deadline:
                break
            if tid not in ev.sol.tasks:
                continue
            moves = relocations(ev, tid)
            late = ev.late_tasks()
            moves += swaps(ev, tid, [x for x in late if x != tid][:6] + [x for x in focus[:6] if x != tid])
            tr = best_trial(ev, moves)
            if tr is None and use_split and tid in ev.late_tasks():
                tr = best_trial(ev, splits(ev, tid))
            if tr is not None:
                ev.commit(tr)
                improved = True
                improved_count += 1
    return improved_count


def focus_set(ev: Evaluator) -> List[str]:
    """Open tasks worth touching: affected by the event, late, or sharing a machine/mold with those."""
    st = ev.state
    seeds = [x for x in ev.sol.tasks if x in st.affected or ev.sol.tasks[x].kind == "new"] + ev.late_tasks()
    seeds = list(dict.fromkeys(seeds))
    machines = {ev.sol.tasks[x].machine for x in seeds}
    molds = {ev.sol.tasks[x].mold for x in seeds}
    extra = [x for x, t in ev.sol.tasks.items() if (t.machine in machines or t.mold in molds) and x not in seeds]
    return seeds + extra


def lns(ev: Evaluator, deadline: float, rng: random.Random, max_iter: int) -> Tuple[int, int]:
    accepted = 0
    it = 0
    last_imp = 0
    while time.time() < deadline and it < max_iter and it - last_imp < 400:
        it += 1
        focus = focus_set(ev)
        if not focus:
            break
        seed_t = rng.choice(focus)
        t0 = ev.sol.tasks[seed_t]
        k = rng.choice([2, 3, 4])
        rel = [x for x, t in ev.sol.tasks.items()
               if x != seed_t and (t.machine == t0.machine or t.mold == t0.mold
                                   or abs(ev.orders[t.order].due - ev.orders[t0.order].due) < 24)]
        rng.shuffle(rel)
        R = [seed_t] + rel[:k - 1]
        snap = ev.snapshot()
        cur = ev.total
        # destroy: remove R from the solution
        seq_upd = {}
        for x in R:
            m = ev.sol.tasks[x].machine
            seq_upd.setdefault(m, list(ev.sol.seq[m]))
        for x in R:
            m = ev.sol.tasks[x].machine
            seq_upd[m] = [y for y in seq_upd[m] if y != x]
        removed_tasks = {x: ev.sol.tasks[x] for x in R}
        ev.commit(ev.trial(seq_upd, {x: None for x in R}))
        # repair: greedy best insertion, hardest first
        R.sort(key=lambda x: (-ev.orders[removed_tasks[x].order].weight, ev.orders[removed_tasks[x].order].due))
        if rng.random() < 0.4:
            rng.shuffle(R)
        for x in R:
            t = removed_tasks[x]
            o = ev.orders[t.order]
            best = None
            for (m2, k2) in _opts(ev, t):
                base = list(ev.sol.seq.get(m2, []))
                for pos in _positions(ev, base, o.due):
                    s = list(base)
                    s.insert(pos, x)
                    same = (m2 == t.machine and k2 == t.mold)
                    nt = t if same else t.clone(machine=m2, mold=k2, lb=_lb_for(ev, t))
                    tr = ev.trial({m2: s}, {x: nt})
                    if best is None or tr.total < best.total - 1e-12:
                        best = tr
            ev.commit(best)
        if ev.total < cur - 1e-9:
            accepted += 1
            last_imp = it
        else:
            ev.restore(snap)
    return it, accepted


@dataclass
class RepairResult:
    method: str
    sol: Solution
    acts: Dict[str, Actual]
    metrics: Metrics
    runtime: float
    objective: float
    meta: dict = field(default_factory=dict)


def objective_of(m: Metrics, P: RepairParams) -> float:
    return P.w1 * m.late_orders + P.w2 * m.late_qty / 1e6 + P.w3 * m.wtard + P.lam * m.changes


def minimal_change_repair(state: RepairState, P: RepairParams) -> RepairResult:
    t0 = time.time()
    rng = random.Random(P.seed)
    ev = Evaluator(state, state.initial_solution(), P)
    start_total = ev.total
    deadline = t0 + P.time_limit
    n_desc = descent(ev, deadline, focus_set(ev), rng)
    n_it, n_acc = lns(ev, deadline, rng, P.max_iter)
    n_desc += descent(ev, t0 + P.time_limit * 1.3, focus_set(ev), rng)
    acts, met = state.run(ev.sol)
    return RepairResult("minimal_change_repair", ev.sol, acts, met, time.time() - t0, objective_of(met, P),
                        {"start_objective": start_total, "descent_moves": n_desc, "lns_iters": n_it,
                         "lns_accepted": n_acc, "ev_total": ev.total})
