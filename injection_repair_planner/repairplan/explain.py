"""Plain-language explanations: why an order is late and the cheapest minimal fix.

For every order that is late if nothing is done, candidate fixes are generated (move to another machine,
switch to a twin mold, run it earlier, split into two shipments, sorter overtime for quality holds),
each one is re-simulated with the exact simulator, and the cheapest fix that gets the order on time
(cost = lambda * plan changes + residual lateness penalties) is reported with a template sentence.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .events import CAVITY_BLOCK, MACHINE_DOWN, MOLD_UNAVAILABLE, QUALITY_HOLD, RUSH_ORDER
from .repair import Evaluator, RepairParams, RepairResult, objective_of, relocations, splits
from .scenario import RepairState
from .sim import Metrics, SimContext, Solution, HoldInfo
from .sorter import release_schedule

DAYS = ["Fri", "Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]


def fmt_t(t: float, origin_weekday: int = 0) -> str:
    if t > 0 and abs(t / 24 - round(t / 24)) < 1e-6:
        return f"end of {DAYS[(int(round(t / 24)) - 1 + origin_weekday) % 7]}"
    d = int(t // 24)
    h = t - 24 * d
    return f"{DAYS[(d + origin_weekday) % 7] if origin_weekday >= 0 else 'day'} {int(h):02d}:{int(round((h % 1) * 60)) % 60:02d}"


@dataclass
class Fix:
    kind: str  # move | twin_mold | reorder | split | sorter_overtime
    text: str
    changes: int
    cost: float
    metrics: Metrics
    resolves: bool
    side_effects: List[str] = field(default_factory=list)
    sol: Optional[Solution] = None


@dataclass
class Explanation:
    order: str
    product: str
    tardiness_h: float
    late_qty: float
    cause: str
    best: Optional[Fix]
    alternatives: List[Fix]
    text: str


def _cause(state: RepairState, oid: str, late_tasks: List[str], base_sol: Solution, base_acts) -> str:
    scn, e = state.scn, state.scn.event
    plant = scn.plant
    tasks = [base_sol.tasks[t] for t in late_tasks if t in base_sol.tasks]
    dur = (e.t_end - e.t) if e.t_end else 0
    if e.type == MACHINE_DOWN:
        if any(t.machine == e.machine for t in tasks):
            return f"{e.machine} is down for {dur:.0f} h from {fmt_t(e.t)}, which stops its run"
        return f"it is queued behind work delayed by the {dur:.0f} h breakdown of {e.machine}"
    if e.type == CAVITY_BLOCK:
        pct = 100.0 * (e.n - e.k) / e.n
        if any(t.mold == e.mold for t in tasks):
            return (f"{e.k} of {e.n} cavities on mold {e.mold} are blocked for {dur:.0f} h, "
                    f"so output drops to {pct:.0f}%")
        return f"it waits behind a run slowed by {e.k}/{e.n} blocked cavities on mold {e.mold}"
    if e.type == MOLD_UNAVAILABLE:
        if any(t.mold == e.mold for t in tasks):
            return f"mold {e.mold} is unavailable for {dur:.0f} h from {fmt_t(e.t)}"
        return f"it waits for a machine delayed by the unavailability of mold {e.mold}"
    if e.type == RUSH_ORDER:
        if oid == e.order.id:
            return f"the rush order arrives at {fmt_t(e.t)} and every suitable machine is busy"
        return f"rush order {e.order.id} takes machine time ahead of it"
    if e.type == QUALITY_HOLD:
        h = scn.ctx.holds.get(e.hold_order)
        if oid == e.hold_order and h:
            good = h.qty - h.makeup
            return (f"{h.qty:,.0f} pcs are on quality hold; the sorter (queue, shift hours, {plant.sorter.yield_:.0%} yield) "
                    f"returns only {good:,.0f} good pcs and {h.makeup:,.0f} pcs must be re-made")
        return "it waits behind the make-up run for the quality-held order"
    return "of the disruption"


def _apply(sol: Solution, seq_upd, task_upd) -> Solution:
    s = sol.copy()
    for tid, t in task_upd.items():
        if t is None:
            s.tasks.pop(tid, None)
        else:
            s.tasks[tid] = t
    for m, q in seq_upd.items():
        s.seq[m] = list(q)
    return s


def _side_effects(state: RepairState, base_acts, acts, met_base: Metrics, met: Metrics, oid: str) -> List[str]:
    out = []
    for o, r in met.per_order.items():
        if o == oid:
            continue
        b = met_base.per_order.get(o)
        if b is None:
            continue
        d = r.completion - b.completion
        if d > 1.0:
            od = state.scn.orders[o]
            tail = "still on time" if not r.late else f"becomes {r.tard:.1f} h late"
            out.append(f"{o} finishes {d:.1f} h later ({tail})")
    return out[:4]


def _tag(sol: Solution, seq_upd, task_upd, tid: str) -> str:
    t0 = sol.tasks[tid]
    t1 = task_upd.get(tid, t0)
    if any(k != tid and (v is not None) and k not in sol.tasks for k, v in task_upd.items()):
        return "split"
    if t1.machine != t0.machine:
        return "move"
    if t1.mold != t0.mold:
        return "twin_mold"
    return "reorder"


def _describe_fix(state: RepairState, kind: str, base: Solution, new: Solution, tid: str) -> str:
    t0, t1 = base.tasks[tid], new.tasks[tid]
    o = state.scn.orders[t0.order]
    if kind == "move":
        return (f"move the {t0.qty:,.0f} pcs of {o.id} from {t0.machine} to {t1.machine} "
                f"(mold {t1.mold})")
    if kind == "twin_mold":
        return f"run {o.id} on twin mold {t1.mold} on {t1.machine} instead of {t0.mold}"
    if kind == "split":
        piece = [x for k, x in new.tasks.items() if k not in base.tasks and x.parent == tid]
        p = piece[0]
        return (f"split {o.id} into two shipments: {t1.qty:,.0f} pcs stay on {t1.machine}, "
                f"{p.qty:,.0f} pcs run on {p.machine} (mold {p.mold})")
    seq = new.seq[t1.machine]
    i = seq.index(tid)
    over = [base.tasks[x].order for x in seq[i + 1:] if x in base.tasks and x in base.seq.get(t1.machine, [])
            and base.seq[t1.machine].index(x) < base.seq[t1.machine].index(tid)]
    names = ", ".join(over[:3]) if over else "its queue neighbours"
    return f"run {o.id} on {t1.machine} ahead of {names}"


def _overtime_fix(state: RepairState, base: Solution, base_met: Metrics, P: RepairParams) -> List[Fix]:
    e = state.scn.event
    if e.type != QUALITY_HOLD:
        return []
    plant, scn = state.scn.plant, state.scn
    o = scn.orders[e.hold_order]
    out = []
    for h in (2.0, 4.0):
        sorter = dataclasses.replace(plant.sorter, overtime_hours=h)
        lots = list(e.backlog) + [{"id": "HOLD", "qty": e.hold_qty, "arrival": e.t, "priority": o.priority}]
        credits = release_schedule(sorter, lots)["HOLD"]
        good = sum(q for _, q in credits)
        makeup = max(0.0, e.hold_qty - good)
        hold = HoldInfo(o.id, e.t, e.hold_qty, credits, makeup)
        ctx2 = SimContext(plant, [], {o.id: hold})
        st2 = dataclasses.replace(state, scn=dataclasses.replace(scn, ctx=ctx2))
        sol = base.copy()
        sol.tasks = dict(sol.tasks)
        uid = f"U:{o.id}"
        key = uid if uid in sol.tasks else next((k for k in sol.tasks if k.startswith(uid)), None)
        if key:
            if makeup <= 1.0:
                m = sol.tasks[key].machine
                sol.seq[m] = [x for x in sol.seq[m] if x != key]
                del sol.tasks[key]
            else:
                sol.tasks[key] = sol.tasks[key].clone(qty=round(makeup))
        _, met = st2.run(sol)
        res = met.per_order[o.id]
        out.append(Fix("sorter_overtime",
                       f"approve {h:.0f} h of sorter overtime per shift (returns {good:,.0f} good pcs instead of "
                       f"{sum(q for _, q in scn.ctx.holds[o.id].credits):,.0f}); no production moves",
                       0, objective_of(met, P) - 0.0, met, not res.late, [], sol))
    return out


def explain_orders(state: RepairState, P: Optional[RepairParams] = None, max_orders: int = 6) -> List[Explanation]:
    P = P or RepairParams()
    base = state.initial_solution()
    ev = Evaluator(state, base, P)
    base_acts, base_met = state.run(base)
    late = sorted([(o, r) for o, r in base_met.per_order.items() if r.late],
                  key=lambda x: (-state.scn.orders[x[0]].priority, -x[1].tard))[:max_orders]
    out: List[Explanation] = []
    for oid, r in late:
        order = state.scn.orders[oid]
        tids = [t for t in ev.order_tasks.get(oid, []) if t in base.tasks]
        cause = _cause(state, oid, tids, base, base_acts)
        cands: List[Fix] = []
        for tid in tids:
            moves = list(relocations(ev, tid)) + list(splits(ev, tid))
            best_by_kind: Dict[str, tuple] = {}
            for seq_upd, task_upd in moves:
                tr = ev.trial(seq_upd, task_upd)
                kind = _tag(base, seq_upd, task_upd, tid)
                if kind not in best_by_kind or tr.total < best_by_kind[kind][0]:
                    best_by_kind[kind] = (tr.total, seq_upd, task_upd)
            for kind, (_, seq_upd, task_upd) in best_by_kind.items():
                new = _apply(base, seq_upd, task_upd)
                acts, met = state.run(new)
                resolved = not met.per_order[oid].late
                cands.append(Fix(kind, _describe_fix(state, kind, base, new, tid), met.changes,
                                 objective_of(met, P), met, resolved,
                                 _side_effects(state, base_acts, acts, base_met, met, oid), new))
        cands += _overtime_fix(state, base, base_met, P)
        ok = sorted([c for c in cands if c.resolves], key=lambda c: c.cost)
        alts = ok[1:4]
        best = ok[0] if ok else None
        tard = f"{r.tard:.1f} h late"
        head = (f"Order {oid} ({order.product}, priority {order.priority}) will be {tard} "
                f"(due {fmt_t(order.due)}) because {cause}.")
        if best:
            n = best.changes
            side = "; ".join(best.side_effects) if best.side_effects else "no other order becomes late"
            if best.metrics.late_orders:
                side += f"; {best.metrics.late_orders} other order(s) remain late"
            tail = (f" Cheapest fix: {best.text} - {n} plan change{'s' if n != 1 else ''} ({side}).")
        else:
            ot = [c for c in cands if c.kind == "sorter_overtime"]
            if state.scn.event.type == QUALITY_HOLD and oid == state.scn.event.hold_order and ot:
                res = min(c.metrics.per_order[oid].tard for c in ot)
                tail = (f" The sorter queue, not production, is the bottleneck: even with overtime it is still "
                        f"{res:.1f} h late. It needs more sorting capacity or a customer extension.")
            else:
                tail = " No single-lot change gets it on time; it needs several moves together (see the repair plan)."
        out.append(Explanation(oid, order.product, r.tard, r.late_qty, cause, best, alts, head + tail))
    return out


def describe_repair(state: RepairState, result: RepairResult) -> List[str]:
    """Step list of what the repaired plan changes compared with the (disrupted) old plan."""
    base = state.initial_solution()
    lines = []
    for tid, t in result.sol.tasks.items():
        b = base.tasks.get(tid)
        o = state.scn.orders[t.order]
        a = result.acts[tid]
        if b is None:
            piece_of = t.parent or tid
            lines.append(f"Add {t.qty:,.0f} pcs of {o.id} on {t.machine} (mold {t.mold}) from {fmt_t(a.ps)}")
        elif t.machine != b.machine:
            lines.append(f"Move {o.id} ({t.qty:,.0f} pcs) from {b.machine} to {t.machine} (mold {t.mold}), "
                         f"start {fmt_t(a.ps)}")
        elif t.mold != b.mold:
            lines.append(f"Switch {o.id} on {t.machine} from mold {b.mold} to twin mold {t.mold}")
        elif t.split:
            lines.append(f"Reduce {o.id} on {t.machine} to {t.qty:,.0f} pcs (rest split off)")
        elif abs(a.ps - (t.ref_start or a.ps)) > 0.5 and t.kind not in ("tail", "head"):
            lines.append(f"Re-time {o.id} on {t.machine}: start {fmt_t(a.ps)} (was {fmt_t(t.ref_start)})")
    return lines or ["No changes needed: the old plan still meets every due date."]
