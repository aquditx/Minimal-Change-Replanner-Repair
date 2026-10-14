"""Synthetic plants calibrated to the real WK41 plant (see data/real_wk41.json).

Calibration targets taken from the real data:
  * family mix            IMP 6 / SIPA 7 / cap machines 3 (+1 idle press)
  * std output (pcs/h)    BC molds 13.6k-31.4k, TO/PC molds 21.6k-99.7k, cap molds 61.7k-123.4k
  * run length            15-130 h, median ~40 h
  * colours / resins      mostly Clear, CSD/WTR/SOFT/rPET resins, HDPE caps
Cavities, compatibility, changeovers, priorities and due dates are synthetic (documented assumptions).
"""
from __future__ import annotations

import math
import random
from typing import List

from .model import Machine, Mold, Order, Plant, Sorter

FAMILY_SHARE = {"IMP": 0.35, "SIPA": 0.40, "CAP": 0.25}
COLORS = (["Clear", "Blue", "White", "Green", "DarkBlue"], [0.58, 0.18, 0.09, 0.08, 0.07])
RESINS = (["CSD", "WTR", "SOFT", "RPET"], [0.45, 0.35, 0.10, 0.10])
CAVITY_CHOICES = [4, 8, 12, 16, 24, 32, 48, 64, 72, 96, 128, 144]


def _pick(rng: random.Random, spec):
    return rng.choices(spec[0], weights=spec[1])[0]


def _cavities(rate: float) -> int:
    t = rate / 850.0
    return min(CAVITY_CHOICES, key=lambda n: abs(n - t))


def _split_family(n: int):
    imp = max(1, round(n * FAMILY_SHARE["IMP"]))
    cap = max(1, round(n * FAMILY_SHARE["CAP"]))
    sipa = max(1, n - imp - cap)
    return {"IMP": imp, "SIPA": sipa, "CAP": n - imp - sipa}


def generate_plant(n_machines: int, seed: int, horizon: float = 192.0, utilization: float = 0.62,
                   tight_share: float = 0.12) -> Plant:
    rng = random.Random(10_000 * n_machines + seed)
    counts = _split_family(n_machines)
    machines: List[Machine] = []
    by_fam = {"IMP": [], "SIPA": [], "CAP": []}
    for fam, c in counts.items():
        for i in range(c):
            mid = f"{fam[:3]}{len(by_fam[fam]) + 1:02d}"
            m = Machine(id=mid, family=fam, speed=round(rng.uniform(0.92, 1.08), 3))
            machines.append(m)
            by_fam[fam].append(mid)

    # products and molds
    n_products = max(6, int(1.25 * n_machines))
    fam_names = list(counts)
    fam_w = [counts[f] for f in fam_names]
    molds: List[Mold] = []
    product_rate = {}
    mold_fam = {}
    for j in range(n_products):
        fam = rng.choices(fam_names, weights=fam_w)[0]
        if fam == "IMP":
            rate = rng.uniform(13000, 32000)
            cands = list(by_fam["IMP"])
            neck = "BC"
        elif fam == "SIPA":
            rate = math.exp(rng.uniform(math.log(21000), math.log(100000)))
            cands = list(by_fam["SIPA"]) + [m for m in by_fam["IMP"] if rng.random() < 0.15]
            neck = rng.choice(["TO", "PC"])
        else:
            rate = rng.uniform(60000, 125000)
            cands = list(by_fam["CAP"])
            neck = "CAP"
        rate = round(rate)
        prod = f"P{j + 1:03d}-{neck}"
        color = _pick(rng, COLORS)
        resin = "HDPE" if fam == "CAP" else _pick(rng, RESINS)
        n_twin = 2 if rng.random() < 0.25 else 1
        for t in range(n_twin):
            k = min(len(cands), rng.randint(2, 4))
            comp = tuple(sorted(rng.sample(cands, k)))
            molds.append(Mold(id=f"M{j + 1:03d}" + (f"#{t + 1}" if n_twin > 1 else ""), product=prod,
                              cavities=_cavities(rate), rate=float(rate), compatible=comp,
                              color=color, resin=resin, neck=neck))
        product_rate[prod] = rate
        mold_fam[prod] = fam

    # make sure every machine can run something
    comp_union = {m.id: False for m in machines}
    for mo in molds:
        for mid in mo.compatible:
            comp_union[mid] = True
    for m in machines:
        if not comp_union[m.id]:
            cand = [i for i, mo in enumerate(molds) if mold_fam[mo.product] == m.family]
            if cand:
                i = rng.choice(cand)
                mo = molds[i]
                molds[i] = Mold(**{**mo.__dict__, "compatible": tuple(sorted(set(mo.compatible) | {m.id}))})

    # orders until target load is reached
    products = list(product_rate)
    prod_w = [counts[mold_fam[p]] for p in products]
    target = utilization * n_machines * horizon
    orders: List[Order] = []
    load = 0.0
    i = 0
    n_molds_of = {}
    for mo in molds:
        n_molds_of[mo.product] = n_molds_of.get(mo.product, 0) + 1
    mold_load = {p: 0.0 for p in products}
    cap_h = 0.65 * horizon  # no product may need more than 65 % of the horizon on its mold(s): keeps instances feasible
    tries = 0
    while load < target:
        prod = rng.choices(products, weights=prod_w)[0]
        tries += 1
        if mold_load[prod] / n_molds_of[prod] > cap_h and tries < 50 * len(products) + 5000:
            continue
        dur = min(110.0, max(6.0, rng.lognormvariate(math.log(34.0), 0.55)))
        rate = product_rate[prod]
        qty = round(rate * dur, -2)
        pr = rng.choices([1, 2, 3], weights=[0.5, 0.35, 0.15])[0]
        release = 0.0 if rng.random() < 0.85 else round(rng.uniform(0, 0.3 * horizon), 1)
        slack = rng.uniform(1.05, 1.35) if rng.random() < tight_share else rng.uniform(1.3, 2.4)
        earliest = release + dur * slack + 4.0
        due = max(earliest, rng.uniform(0.3 * horizon, horizon))
        due = round(min(due, horizon + 24.0), 1)
        i += 1
        orders.append(Order(id=f"O{i:03d}", product=prod, qty=float(qty), due=due, priority=pr,
                            release=release, customer=f"C{rng.randint(1, 12):02d}"))
        load += dur
        mold_load[prod] += dur
    # initial mold on each machine
    final_machines = []
    for m in machines:
        comp = [mo.id for mo in molds if m.id in mo.compatible]
        init = rng.choice(comp) if comp and rng.random() < 0.8 else None
        final_machines.append(Machine(m.id, m.family, m.speed, init))
    return Plant(final_machines, molds, orders, horizon, Sorter(), name=f"synth{n_machines}_s{seed}")
