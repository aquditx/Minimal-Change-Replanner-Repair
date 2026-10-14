"""Export synthetic plants as CSV and compare their statistics with the real WK41 plant (calibration table)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from repairplan.realdata import load_real  # noqa: E402
from repairplan.synth import generate_plant  # noqa: E402

OUT = ROOT / "results" / "dataset"
OUT.mkdir(parents=True, exist_ok=True)


def frames(plant):
    machines = pd.DataFrame([dict(machine=m.id, family=m.family, speed=m.speed, init_mold=m.init_mold)
                             for m in plant.machines.values()])
    molds = pd.DataFrame([dict(mold=k.id, product=k.product, cavities=k.cavities, rate_pcs_h=k.rate,
                               color=k.color, resin=k.resin, neck=k.neck, compatible=";".join(k.compatible))
                          for k in plant.molds.values()])
    orders = pd.DataFrame([dict(order=o.id, product=o.product, qty=o.qty, due_h=o.due, priority=o.priority,
                                release_h=o.release, customer=o.customer) for o in plant.orders.values()])
    return machines, molds, orders


def stats(plant, label):
    ords = list(plant.orders.values())
    dur = [o.qty / max(plant.rate(m, k) for m, k in plant.options(o.product)) for o in ords]
    rates = [k.rate for k in plant.molds.values()]
    fam = pd.Series([m.family for m in plant.machines.values()]).value_counts(normalize=True)
    return dict(dataset=label, machines=len(plant.machines), molds=len(plant.molds), orders=len(ords),
                run_len_h_median=round(float(np.median(dur)), 1), run_len_h_p10=round(float(np.percentile(dur, 10)), 1),
                run_len_h_p90=round(float(np.percentile(dur, 90)), 1), mold_rate_min=round(min(rates)),
                mold_rate_median=round(float(np.median(rates))), mold_rate_max=round(max(rates)),
                share_IMP=round(float(fam.get("IMP", 0)), 2), share_SIPA=round(float(fam.get("SIPA", 0)), 2),
                share_CAP=round(float(fam.get("CAP", 0)), 2),
                load_h_per_machine=round(sum(dur) / len(plant.machines), 1))


rows = []
real, _, _ = load_real()
rows.append(stats(real, "REAL WK41"))
for n in (10, 25, 50, 100):
    p = generate_plant(n, 0)
    m, k, o = frames(p)
    m.to_csv(OUT / f"synth{n}_machines.csv", index=False)
    k.to_csv(OUT / f"synth{n}_molds.csv", index=False)
    o.to_csv(OUT / f"synth{n}_orders.csv", index=False)
    rows.append(stats(p, f"synthetic {n} (seed 0)"))
cal = pd.DataFrame(rows)
cal.to_csv(OUT / "calibration_real_vs_synthetic.csv", index=False)
print(cal.T.to_string())
