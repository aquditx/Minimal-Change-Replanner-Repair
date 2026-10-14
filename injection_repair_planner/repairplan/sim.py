"""Forward discrete-event simulation of a plan (sequences per machine) under disruption events.

Semantics
  * Each machine processes its tasks in sequence. A task needs its machine AND its mold.
  * Production start = max(planned start `lb`, machine free + changeover, mold free, order release).
  * Production rate = mold rate x machine speed x capacity factor(t). The factor drops to 0 while the
    machine is down or the mold is unavailable and to (n-k)/n while k of n cavities are blocked.
    A run that hits an outage pauses and resumes (progress is kept).
  * Tasks are dispatched globally in order of earliest start so mold exclusivity across machines holds.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .events import CAVITY_BLOCK, MACHINE_DOWN, MOLD_UNAVAILABLE, Event
from .model import Order, Plant

INF = float("inf")
CHANGE_TOL = 0.5  # hours: start-time differences below this are not counted as plan changes
LATE_EPS = 1e-6


@dataclass
class Task:
    id: str
    order: str
    qty: float
    machine: str
    mold: str
    lb: float  # planned (earliest) production start
    ref_machine: Optional[str] = None  # machine / start in the old plan (None for new tasks)
    ref_start: Optional[float] = None
    kind: str = "old"  # old | head | tail | piece | new
    parent: Optional[str] = None
    frozen: bool = False
    split: bool = False  # created or shortened by a split-lot move (always counts as a change)

    def clone(self, **kw) -> "Task":
        return replace(self, **kw)


class Actual:
    __slots__ = ("cs", "ps", "end", "segs")

    def __init__(self, cs: float, ps: float, end: float, segs: tuple):
        self.cs, self.ps, self.end, self.segs = cs, ps, end, segs

    def qty_by(self, t: float) -> float:
        q = 0.0
        for s, e, r in self.segs:
            if t <= s:
                break
            q += r * (min(e, t) - s)
        return q

    def truncate(self, t: float) -> "Actual":
        segs = []
        for s, e, r in self.segs:
            if s >= t:
                break
            segs.append((s, min(e, t), r))
        end = segs[-1][1] if segs else t
        return Actual(self.cs, self.ps, end, tuple(segs))


@dataclass
class State0:
    machine_free: Dict[str, float] = field(default_factory=dict)
    machine_mold: Dict[str, Optional[str]] = field(default_factory=dict)
    mold_free: Dict[str, float] = field(default_factory=dict)


def initial_state(plant: Plant) -> State0:
    return State0({m: 0.0 for m in plant.machines}, {m: mc.init_mold for m, mc in plant.machines.items()}, {})


@dataclass
class HoldInfo:
    order: str
    t: float
    qty: float
    credits: List[Tuple[float, float]]  # (release time, credited good pieces)
    makeup: float  # pieces that never come back (must be re-produced)


class SimContext:
    """Capacity windows derived from events, plus quality-hold supply."""

    def __init__(self, plant: Plant, events: Iterable[Event] = (), holds: Optional[Dict[str, HoldInfo]] = None):
        self.plant = plant
        self.events = list(events)
        self.down: Dict[str, List[Tuple[float, float]]] = {}
        self.off: Dict[str, List[Tuple[float, float]]] = {}
        self.cav: Dict[str, List[Tuple[float, float, float]]] = {}
        for e in self.events:
            if e.type == MACHINE_DOWN:
                self.down.setdefault(e.machine, []).append((e.t, e.t_end))
            elif e.type == MOLD_UNAVAILABLE:
                self.off.setdefault(e.mold, []).append((e.t, e.t_end))
            elif e.type == CAVITY_BLOCK:
                f = max(0.0, (e.n - e.k) / e.n)
                self.cav.setdefault(e.mold, []).append((e.t, e.t_end, f))
        self.holds = holds or {}
        self._seg: Dict[Tuple[str, str], Optional[list]] = {}

    def _segments(self, m: str, k: str):
        key = (m, k)
        if key in self._seg:
            return self._seg[key]
        wins = [(a, b, 0.0) for a, b in self.down.get(m, ())]
        wins += [(a, b, 0.0) for a, b in self.off.get(k, ())]
        wins += list(self.cav.get(k, ()))
        if not wins:
            seg = None
        else:
            pts = sorted({x for a, b, _ in wins for x in (a, b)})
            seg = [(-INF, pts[0], 1.0)]
            for i in range(len(pts) - 1):
                a, b = pts[i], pts[i + 1]
                f = 1.0
                for wa, wb, wf in wins:
                    if wa <= a and b <= wb:
                        f *= wf
                seg.append((a, b, f))
            seg.append((pts[-1], INF, 1.0))
        self._seg[key] = seg
        return seg

    def qty_end(self, m: str, k: str, start: float, qty: float):
        r0 = self.plant.rate(m, k)
        seg = self._segments(m, k)
        if seg is None:
            e = start + qty / r0
            return e, ((start, e, r0),)
        t, rem, out = start, qty, []
        for a, b, f in seg:
            if b <= t:
                continue
            s = t if t > a else a
            if f <= 0:
                t = b
                continue
            r = r0 * f
            cap = (b - s) * r
            if cap >= rem - 1e-9:
                e = s + rem / r
                out.append((s, e, r))
                return e, tuple(out)
            out.append((s, b, r))
            rem -= cap
            t = b
        return INF, tuple(out)

    def co_end(self, m: str, start: float, hours: float) -> float:
        wins = self.down.get(m)
        if not wins or hours <= 0:
            return start + hours
        t, rem = start, hours
        for a, b in sorted(wins):
            if b <= t:
                continue
            if t >= a:
                t = b
                continue
            if t + rem <= a:
                break
            rem -= a - t
            t = b
        return t + rem


def simulate(ctx: SimContext, machines: Sequence[str], seq: Dict[str, List[str]], tasks: Dict[str, Task],
             st0: State0) -> Dict[str, Actual]:
    plant = ctx.plant
    mfree = {m: st0.machine_free.get(m, 0.0) for m in machines}
    mmold = {m: st0.machine_mold.get(m) for m in machines}
    moldfree: Dict[str, float] = {}
    pos = {m: 0 for m in machines}
    out: Dict[str, Actual] = {}
    remaining = sum(len(seq.get(m, ())) for m in machines)
    while remaining:
        best = None
        for m in machines:
            s = seq.get(m)
            i = pos[m]
            if not s or i >= len(s):
                continue
            t = tasks[s[i]]
            cs = mfree[m]
            x = moldfree.get(t.mold)
            if x is None:
                x = st0.mold_free.get(t.mold, 0.0)
            if x > cs:
                cs = x
            co = plant.co(mmold[m], t.mold, m)
            ps0 = ctx.co_end(m, cs, co) if co > 0 else cs
            ps = ps0 if ps0 > t.lb else t.lb
            key = (ps, t.lb, t.id)
            if best is None or key < best[0]:
                best = (key, m, t, cs, co, ps, ps0)
        key, m, t, cs, co, ps, ps0 = best
        occ = cs if ps <= ps0 + 1e-12 else max(cs, ps - co)
        end, segs = ctx.qty_end(m, t.mold, ps, t.qty)
        out[t.id] = Actual(occ, ps, end, segs)
        mfree[m] = end
        moldfree[t.mold] = end
        mmold[m] = t.mold
        pos[m] += 1
        remaining -= 1
    return out


# ----------------------------------------------------------------------------- order evaluation
@dataclass
class OrderResult:
    late: bool
    tard: float
    late_qty: float
    completion: float


def order_result(order: Order, tids: Sequence[str], tasks: Dict[str, Task], acts: Dict[str, Actual],
                 hold: Optional[HoldInfo], cap: float) -> OrderResult:
    """Lateness of one order. Without a quality hold the order completes when its last task ends.
    With a hold, supply = produced - held + sorter credits; the order completes when supply
    reaches the required quantity for good (never, if the shortfall is not re-produced)."""
    need = order.qty
    planned = sum(tasks[t].qty for t in tids)
    due = order.due
    if hold is None:
        if not tids or planned < need - 1.0:
            produced = sum(acts[t].qty_by(due) for t in tids)
            return OrderResult(True, max(0.0, cap - due), max(0.0, need - produced), INF)
        comp = max(acts[t].end for t in tids)
        if comp != INF and comp <= due + LATE_EPS:
            return OrderResult(False, 0.0, 0.0, comp)
        produced = sum(acts[t].qty_by(due) for t in tids)
        tard = max(0.0, cap - due) if comp == INF else comp - due
        return OrderResult(True, tard, max(0.0, need - produced), comp)

    def stock(t: float, left: bool = False) -> float:
        s = sum(acts[x].qty_by(t) for x in tids)
        if t > hold.t or (t == hold.t and not left):
            s -= hold.qty
        for ct, cq in hold.credits:
            if ct < t or (ct == t and not left):
                s += cq
        return s

    pts = {hold.t}
    for x in tids:
        for s0, e0, _ in acts[x].segs:
            pts.add(s0)
            pts.add(e0)
    for ct, _ in hold.credits:
        pts.add(ct)
    pts = sorted(p for p in pts if hold.t <= p < INF)
    final = stock(pts[-1])
    if final < need - 1.0:
        comp = INF
    else:
        last_bad = None
        for p in pts:
            if stock(p) < need - 1.0:
                last_bad = p
        if last_bad is None:
            comp = max(acts[x].end for x in tids)
        else:
            q = next(p for p in pts if p > last_bad)
            lo, hi = stock(last_bad), stock(q, left=True)
            if hi >= need - 1.0 and hi > lo + 1e-9:
                comp = last_bad + (q - last_bad) * (need - lo) / (hi - lo)
            else:
                comp = q
    late_qty = max(0.0, need - stock(due))
    if comp == INF:
        return OrderResult(True, max(0.0, cap - due), max(late_qty, need - final), INF)
    if comp <= due + LATE_EPS:
        return OrderResult(False, 0.0, 0.0, comp)
    return OrderResult(True, comp - due, late_qty, comp)


def task_changed(t: Task, a: Optional[Actual]) -> bool:
    """Plan-change definition: machine or start time differs from the old plan."""
    if t.ref_machine is None or t.frozen:
        return False
    if t.split:
        return True
    if t.machine != t.ref_machine:
        return True
    if t.kind in ("tail", "head"):
        return False  # remainder of a run that already started: resuming on the same machine is not a change
    return a is not None and abs(a.ps - t.ref_start) > CHANGE_TOL


@dataclass
class Metrics:
    late_orders: int
    late_qty: float
    wtard: float  # priority-weighted tardiness (hours)
    changes: int
    tard_total: float = 0.0
    per_order: Dict[str, OrderResult] = field(default_factory=dict)


def evaluate(ctx: SimContext, tasks: Dict[str, Task], acts: Dict[str, Actual], orders: Dict[str, Order],
             order_tasks: Optional[Dict[str, List[str]]] = None) -> Metrics:
    if order_tasks is None:
        order_tasks = {}
        for tid, t in tasks.items():
            order_tasks.setdefault(t.order, []).append(tid)
    cap = ctx.plant.horizon + 48.0
    lo = lq = wt = tt = 0.0
    per = {}
    for oid, o in orders.items():
        r = order_result(o, order_tasks.get(oid, []), tasks, acts, ctx.holds.get(oid), cap)
        per[oid] = r
        if r.late:
            lo += 1
            lq += r.late_qty
            wt += o.weight * r.tard
            tt += r.tard
    ch = sum(1 for tid, t in tasks.items() if task_changed(t, acts.get(tid)))
    return Metrics(int(lo), lq, wt, ch, tt, per)


# ----------------------------------------------------------------------------- solutions
@dataclass
class Solution:
    tasks: Dict[str, Task]
    seq: Dict[str, List[str]]

    def copy(self) -> "Solution":
        return Solution(dict(self.tasks), {m: list(s) for m, s in self.seq.items()})


def simulate_solution(ctx: SimContext, sol: Solution, st0: Optional[State0] = None) -> Dict[str, Actual]:
    st0 = st0 or initial_state(ctx.plant)
    return simulate(ctx, list(ctx.plant.machines), sol.seq, sol.tasks, st0)


def sequences_from_starts(tasks: Dict[str, Task], starts: Dict[str, float]) -> Dict[str, List[str]]:
    seq: Dict[str, List[str]] = {}
    for tid, t in tasks.items():
        seq.setdefault(t.machine, []).append(tid)
    for m in seq:
        seq[m].sort(key=lambda x: (starts.get(x, tasks[x].lb), x))
    return seq
