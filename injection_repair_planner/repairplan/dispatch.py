"""Priority-rule list scheduling (EDD / ATC) used for baselines, the repair fallback and warm starts."""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .model import Order, Plant
from .sim import SimContext, Solution, State0, Task


def dispatch_core(plant: Plant, ctx: SimContext, orders: Dict[str, Order], tasks: Dict[str, Task], st0: State0,
                  t_floor: float = 0.0, rule: str = "ATC", K: float = 2.0,
                  options_fn: Optional[Callable[[Task], Sequence[Tuple[str, str]]]] = None) -> Solution:
    """Repeatedly pick the task with the best priority index, put it on the (machine, mold) option that
    finishes it first (exact capacity profile, changeovers and mold exclusivity included)."""
    options_fn = options_fn or (lambda t: plant.options(orders[t.order].product))
    todo = dict(tasks)
    mfree = {m: st0.machine_free.get(m, 0.0) for m in plant.machines}
    mmold = {m: st0.machine_mold.get(m) for m in plant.machines}
    kfree: Dict[str, float] = dict(st0.mold_free)
    seq: Dict[str, List[str]] = {}
    out: Dict[str, Task] = {}
    pbar = 0.0
    for t in todo.values():
        pbar += t.qty / max(1.0, max(plant.rate(m, k) for m, k in options_fn(t)))
    pbar = max(1.0, pbar / max(1, len(todo)))
    while todo:
        tnow = max(min(mfree.values()), t_floor)

        def index(t: Task):
            o = orders[t.order]
            if rule == "EDD":
                return (o.due, t.id)
            p = t.qty / max(1.0, max(plant.rate(m, k) for m, k in options_fn(t)))
            slack = max(o.due - p - tnow, 0.0)
            return (-(o.weight / p) * math.exp(-slack / (K * pbar)), t.id)

        t = min(todo.values(), key=index)
        best = None
        for (m, k) in options_fn(t):
            s0 = max(mfree[m], kfree.get(k, 0.0))
            ps = max(ctx.co_end(m, s0, plant.co(mmold[m], k, m)), t.lb)
            e, _ = ctx.qty_end(m, k, ps, t.qty)
            if best is None or e < best[0]:
                best = (e, m, k, ps)
        e, m, k, ps = best
        mfree[m] = e
        kfree[k] = e
        mmold[m] = k
        seq.setdefault(m, []).append(t.id)
        out[t.id] = t.clone(machine=m, mold=k)
        del todo[t.id]
    return Solution(out, seq)
