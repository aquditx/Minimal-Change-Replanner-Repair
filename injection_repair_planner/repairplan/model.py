"""Plant model: orders, machines, molds, sequence-dependent changeovers, sorter.

All times are in hours from a common origin (t=0). Quantities are pieces.
A task needs one machine AND one mold at the same time (dual resource).
Production rate = (cycle rate per cavity) x (available cavities) x (machine speed).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

PRIORITY_WEIGHT = {1: 1.0, 2: 2.0, 3: 4.0}
COLOR_RANK = {"Clear": 0, "White": 1, "Green": 2, "Blue": 3, "DarkBlue": 4}
INF = float("inf")


@dataclass(frozen=True)
class Machine:
    id: str
    family: str  # 'IMP' (preform, large closure), 'SIPA' (preform), 'CAP' (HDPE caps)
    speed: float = 1.0
    init_mold: Optional[str] = None  # mold mounted at t=0


@dataclass(frozen=True)
class Mold:
    id: str
    product: str  # FG code; twin molds share a product
    cavities: int
    rate: float  # pcs/hour with all cavities available on a speed-1.0 machine
    compatible: Tuple[str, ...]  # machine ids that can run this mold
    color: str = "Clear"
    resin: str = "CSD"
    neck: str = ""

    @property
    def per_cavity_rate(self) -> float:
        return self.rate / self.cavities


@dataclass
class Order:
    id: str
    product: str
    qty: float
    due: float
    priority: int = 2
    release: float = 0.0  # earliest start (e.g. resin arrival)
    customer: str = ""
    kind: str = "base"  # 'base' | 'rush'

    @property
    def weight(self) -> float:
        return PRIORITY_WEIGHT.get(self.priority, 1.0)


class ChangeoverModel:
    """Sequence-dependent changeover hours between consecutive molds on a machine.

    changeover = mold change (family dependent)  [0 if same mold]
               + colour purge (asymmetric: dark -> light costs more)
               + resin purge (if resin differs)
    """

    BASE = {"IMP": 4.0, "SIPA": 3.0, "CAP": 2.0}
    RESIN_PURGE = 1.5

    def __init__(self, molds: Dict[str, Mold]):
        self.molds = molds
        self._cache: Dict[Tuple[Optional[str], str, str], float] = {}

    def hours(self, prev: Optional[str], nxt: str, family: str) -> float:
        key = (prev, nxt, family)
        v = self._cache.get(key)
        if v is not None:
            return v
        if prev is None or prev == nxt:
            v = 0.0
        else:
            a, b = self.molds[prev], self.molds[nxt]
            v = self.BASE.get(family, 3.0)
            ra, rb = COLOR_RANK.get(a.color, 0), COLOR_RANK.get(b.color, 0)
            if ra > rb:
                purge = 1.0 + 0.75 * (ra - rb)
            elif rb > ra:
                purge = 0.5 + 0.25 * (rb - ra)
            else:
                purge = 0.0
            if family == "CAP":
                purge *= 0.5
            v += purge
            if a.resin != b.resin:
                v += self.RESIN_PURGE
            v = round(v * 4) / 4.0  # quarter hours
        self._cache[key] = v
        return v


@dataclass
class Sorter:
    """Quality-hold sorting station: limited rate, shift calendar, batch release, yield."""

    rate: float = 10000.0  # pcs per working hour
    batch: float = 25000.0  # release batch size (pcs)
    yield_: float = 0.92  # fraction of sorted pieces that is good
    shift_start: float = 6.0  # hour of day
    shift_end: float = 22.0
    discipline: str = "fifo"  # 'fifo' | 'priority'
    overtime_hours: float = 0.0  # extra hours appended to every shift


class Plant:
    def __init__(self, machines, molds, orders, horizon: float,
                 sorter: Optional[Sorter] = None, name: str = "plant"):
        self.machines: Dict[str, Machine] = {m.id: m for m in machines}
        self.molds: Dict[str, Mold] = {k.id: k for k in molds}
        self.orders: Dict[str, Order] = {o.id: o for o in orders}
        self.horizon = float(horizon)
        self.sorter = sorter or Sorter()
        self.name = name
        self.changeover = ChangeoverModel(self.molds)
        self.molds_of_product: Dict[str, List[str]] = {}
        for k in self.molds.values():
            self.molds_of_product.setdefault(k.product, []).append(k.id)
        for v in self.molds_of_product.values():
            v.sort()
        self._opt_cache: Dict[str, List[Tuple[str, str]]] = {}

    def options(self, product: str) -> List[Tuple[str, str]]:
        """All (machine, mold) pairs that can produce a product."""
        r = self._opt_cache.get(product)
        if r is None:
            r = []
            for kid in self.molds_of_product.get(product, []):
                for mid in self.molds[kid].compatible:
                    if mid in self.machines:
                        r.append((mid, kid))
            r.sort()
            self._opt_cache[product] = r
        return r

    def rate(self, machine_id: str, mold_id: str) -> float:
        return self.molds[mold_id].rate * self.machines[machine_id].speed

    def co(self, prev_mold: Optional[str], mold: str, machine_id: str) -> float:
        return self.changeover.hours(prev_mold, mold, self.machines[machine_id].family)

    def duration(self, qty: float, machine_id: str, mold_id: str) -> float:
        return qty / self.rate(machine_id, mold_id)

    def summary(self) -> Dict[str, float]:
        fam: Dict[str, int] = {}
        for m in self.machines.values():
            fam[m.family] = fam.get(m.family, 0) + 1
        return {"machines": len(self.machines), "molds": len(self.molds),
                "orders": len(self.orders), "horizon_h": self.horizon, **{f"n_{k}": v for k, v in fam.items()}}
