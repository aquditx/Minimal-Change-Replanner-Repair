"""Quality-hold supply model.

A held lot enters a sorter queue (FIFO or priority). The sorter works only inside daily shift
windows, at rate r pieces/hour, releasing sorted pieces in batches. Each batch is credited to the
order as `yield * batch` at the batch release time: delayed, partial supply.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

from .model import Sorter


def working_advance(s: Sorter, t: float, hours: float) -> float:
    """Time at which `hours` of sorter work started at/after t are finished (shift calendar aware)."""
    day = math.floor(t / 24.0)
    while True:
        w0 = day * 24.0 + s.shift_start
        w1 = day * 24.0 + s.shift_end + s.overtime_hours
        if t < w0:
            t = w0
        if t >= w1:
            day += 1
            continue
        avail = w1 - t
        if hours <= avail + 1e-12:
            return t + hours
        hours -= avail
        day += 1
        t = day * 24.0


def release_schedule(s: Sorter, lots: List[dict]) -> Dict[str, List[Tuple[float, float]]]:
    """lots: [{'id','qty','arrival','priority'}] -> {id: [(release_time, credited_qty), ...]}"""
    remaining = {l["id"]: float(l["qty"]) for l in lots}
    by_id = {l["id"]: l for l in lots}
    out: Dict[str, List[Tuple[float, float]]] = {l["id"]: [] for l in lots}
    if not lots:
        return out
    t = min(l["arrival"] for l in lots)
    while any(v > 1e-9 for v in remaining.values()):
        avail = [i for i, v in remaining.items() if v > 1e-9 and by_id[i]["arrival"] <= t + 1e-12]
        if not avail:
            t = min(by_id[i]["arrival"] for i, v in remaining.items() if v > 1e-9)
            continue
        if s.discipline == "priority":
            lot = min(avail, key=lambda i: (-by_id[i]["priority"], by_id[i]["arrival"], i))
        else:
            lot = min(avail, key=lambda i: (by_id[i]["arrival"], i))
        b = min(s.batch, remaining[lot])
        t = working_advance(s, t, b / s.rate)
        out[lot].append((t, s.yield_ * b))
        remaining[lot] -= b
    return out
