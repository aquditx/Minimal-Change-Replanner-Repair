"""Load the real WK41 plant (data/real_wk41.json) as a Plant plus the planners' sequences."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from .model import Machine, Mold, Order, Plant, Sorter

DATA = Path(__file__).resolve().parents[1] / "data" / "real_wk41.json"


def load_real(path: Path = DATA) -> Tuple[Plant, Dict[str, List[str]], dict]:
    """Returns (plant, human_sequences, raw_json). Due dates: end of FinishOn day (else end of horizon)."""
    d = json.loads(Path(path).read_text())
    H = float(d["horizon_h"])
    machines = [Machine(m["id"], m["family"], float(m.get("speed", 1.0)), d["init_mold"].get(m["id"]))
                for m in d["machines"]]
    molds = [Mold(id=k["id"], product=k["product"], cavities=int(k["cavities"]), rate=float(k["rate"]),
                  compatible=tuple(k["compatible"]), color=k["color"], resin=k["resin"], neck=k["neck"])
              for k in d["molds"]]
    orders = []
    for o in d["orders"]:
        fd = o.get("finish_day")
        due = min(H, (fd + 1) * 24.0) if fd is not None else H
        orders.append(Order(id=o["id"], product=o["product"], qty=float(o["qty"]), due=due,
                            priority=int(o["priority"]), release=0.0, customer=o.get("customer", "")))
    plant = Plant(machines, molds, orders, H, Sorter(), name=d.get("name", "real"))
    lb = {}
    for o in d["orders"]:
        sd = o.get("start_day")
        lb[o["id"]] = max(0.0, (sd or 0) * 24.0)
    d["_lb"] = lb
    return plant, d["human_plan"], d
