"""Disruption events and the seeded generator.

Five event types: machine down [t1,t2]; cavities blocked (k of n) on a mold; mold unavailable;
rush order; quality hold on a lot.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .model import Order, Plant

MACHINE_DOWN = "machine_down"
CAVITY_BLOCK = "cavity_block"
MOLD_UNAVAILABLE = "mold_unavailable"
RUSH_ORDER = "rush_order"
QUALITY_HOLD = "quality_hold"
EVENT_TYPES = [MACHINE_DOWN, CAVITY_BLOCK, MOLD_UNAVAILABLE, RUSH_ORDER, QUALITY_HOLD]


@dataclass
class Event:
    type: str
    t: float  # event time: start of the disruption / arrival
    t_end: Optional[float] = None
    machine: Optional[str] = None
    mold: Optional[str] = None
    k: int = 0  # cavities blocked
    n: int = 0  # cavities total
    order: Optional[Order] = None  # the rush order
    hold_order: Optional[str] = None
    hold_qty: float = 0.0
    backlog: List[dict] = field(default_factory=list)  # sorter lots already queued
    seed: int = 0

    def describe(self) -> str:
        if self.type == MACHINE_DOWN:
            return f"{self.machine} down for {self.t_end - self.t:.0f}h from t={self.t:.0f}h"
        if self.type == CAVITY_BLOCK:
            return f"{self.k} of {self.n} cavities blocked on mold {self.mold} for {self.t_end - self.t:.0f}h"
        if self.type == MOLD_UNAVAILABLE:
            return f"mold {self.mold} unavailable for {self.t_end - self.t:.0f}h"
        if self.type == RUSH_ORDER:
            return f"rush order {self.order.id} ({self.order.qty:,.0f} pcs, due t={self.order.due:.0f}h)"
        return f"quality hold on {self.hold_qty:,.0f} pcs of order {self.hold_order}"


def generate_event(plant: Plant, tasks: dict, acts: dict, etype: str, seed: int, attempt: int = 0) -> Event:
    """Draw one event of the given type. `tasks`/`acts` describe the no-event run of the old plan, used
    so that disruptions hit resources that are actually busy. Deterministic in (etype, seed, attempt)."""
    rng = random.Random(f"{plant.name}|{etype}|{seed}|{attempt}")
    H = plant.horizon
    t = round(rng.uniform(0.12 * H, 0.55 * H), 1)
    active = [(tid, a) for tid, a in acts.items() if a.ps <= t < a.end]
    upcoming = [(tid, a) for tid, a in acts.items() if t <= a.ps <= t + 48]
    pool = active or upcoming or list(acts.items())
    tid, a = rng.choice(pool)
    tk = tasks[tid]
    if etype == MACHINE_DOWN:
        return Event(etype, t, t + round(rng.uniform(8, 48), 1), machine=tk.machine, seed=seed)
    if etype == CAVITY_BLOCK:
        n = plant.molds[tk.mold].cavities
        k = max(1, int(round(n * rng.uniform(0.25, 0.5))))
        return Event(etype, t, t + round(rng.uniform(24, 72), 1), machine=tk.machine, mold=tk.mold, k=k, n=n, seed=seed)
    if etype == MOLD_UNAVAILABLE:
        tid2, a2 = rng.choice(pool)
        return Event(etype, t, t + round(rng.uniform(12, 60), 1), mold=tasks[tid2].mold, seed=seed)
    if etype == RUSH_ORDER:
        prods = sorted(plant.molds_of_product)
        prod = rng.choice(prods)
        best_rate = max(plant.rate(m, k) for m, k in plant.options(prod))
        dur = rng.uniform(5, 18)  # a rush lot is a short run: 5-18 h of production at the best rate
        qty = round(best_rate * dur, -2)
        due = round(t + 4.0 + 1.25 * dur + rng.uniform(10, 36), 1)  # achievable only if work is rearranged
        o = Order(id=f"RUSH{seed:03d}", product=prod, qty=float(qty), due=due,
                  priority=3, release=t, customer="RUSH", kind="rush")
        return Event(etype, t, order=o, seed=seed)
    if etype == QUALITY_HOLD:
        cands = []
        for tid3, a3 in acts.items():
            produced = a3.qty_by(t)
            o = plant.orders.get(tasks[tid3].order)
            if o and produced >= 20000 and o.due >= t + 6 and tasks[tid3].order == o.id:
                cands.append((tasks[tid3].order, produced))
        if not cands:  # fall back: take the largest producer so far
            best = max(acts.items(), key=lambda kv: kv[1].qty_by(t))
            cands = [(tasks[best[0]].order, best[1].qty_by(t))]
        oid, produced = rng.choice(cands)
        q = round(rng.uniform(0.3, 0.9) * produced, -2)
        backlog = []
        for i in range(rng.randint(1, 3)):  # other lots already waiting at the sorter
            backlog.append({"id": f"BL{i}", "qty": round(rng.uniform(40000, 160000), -2),
                            "arrival": max(0.0, t - rng.uniform(1, 10)), "priority": rng.choice([1, 2, 3])})
        return Event(etype, t, hold_order=oid, hold_qty=float(q), backlog=backlog, seed=seed)
    raise ValueError(etype)
