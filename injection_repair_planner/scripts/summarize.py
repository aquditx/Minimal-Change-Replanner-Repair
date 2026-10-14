"""Turn results_raw.csv into the metrics table, scaling summary and a scaling chart.
usage: python scripts/summarize.py [results_dir]"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

d = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "results"
df = pd.read_csv(d / "results_raw.csv")
n0 = len(df)
df = df.drop_duplicates(["size", "seed", "event", "method"], keep="last")  # re-runs append to the same file
err = df["status"].fillna("").str.startswith("ERROR").sum()
print(f"rows: {n0} read, {len(df)} after removing duplicates; ERROR fallback rows: {err}")
print("seeds per size:", df.groupby("size")["seed"].nunique().to_dict(), "\n")
order = ["do_nothing", "manual_rule", "dispatch_edd", "dispatch_atc", "full_resolve", "exact_repair",
         "minimal_change_repair"]
df["method"] = pd.Categorical(df["method"], [m for m in order if m in set(df["method"])], ordered=True)

g = df.groupby(["size", "event", "method"], observed=True)
table = g.agg(cases=("seed", "count"), late_orders=("late_orders", "mean"), late_qty=("late_qty", "mean"),
              changes=("changes", "mean"), runtime_s=("runtime_s", "mean")).round(2).reset_index()
table.to_csv(d / "metrics_table.csv", index=False)

scal = df.groupby(["size", "method"], observed=True).agg(
    cases=("seed", "count"), late_orders=("late_orders", "mean"), late_qty=("late_qty", "mean"),
    changes=("changes", "mean"), runtime_median_s=("runtime_s", "median"),
    runtime_p90_s=("runtime_s", lambda x: x.quantile(0.9))).round(2).reset_index()
scal.to_csv(d / "scaling_summary.csv", index=False)

# win rate: share of cases where minimal-change repair is no worse on (late orders, then changes)
key = ["size", "seed", "event"]
mc = df[df.method == "minimal_change_repair"].set_index(key)
rows = []
for m in order:
    if m == "minimal_change_repair" or m not in set(df["method"]):
        continue
    o = df[df.method == m].set_index(key)
    j = mc.join(o, rsuffix="_o", how="inner")
    better_or_equal = (j.late_orders < j.late_orders_o) | ((j.late_orders == j.late_orders_o) & (j.changes <= j.changes_o))
    fewer_changes = j.changes < j.changes_o
    rows.append(dict(baseline=m, cases=len(j), repair_no_worse_pct=round(100 * better_or_equal.mean(), 1),
                     repair_fewer_changes_pct=round(100 * fewer_changes.mean(), 1)))
pd.DataFrame(rows).to_csv(d / "win_rate.csv", index=False)

fig, ax = plt.subplots(1, 3, figsize=(14, 4))
for m, sub in scal.groupby("method", observed=True):
    if len(sub) == 0:
        continue
    ax[0].plot(sub["size"], sub["late_orders"], marker="o", label=m)
    ax[1].plot(sub["size"], sub["changes"], marker="o", label=m)
    ax[2].plot(sub["size"], sub["runtime_median_s"], marker="o", label=m)
for a, t in zip(ax, ["Late orders (mean)", "Plan changes (mean)", "Runtime, median (s)"]):
    a.set_title(t); a.set_xlabel("machines"); a.set_xscale("log"); a.set_xticks(sorted(df["size"].unique()))
    a.set_xticklabels(sorted(df["size"].unique())); a.grid(alpha=0.3)
ax[2].set_yscale("log")
ax[0].legend(fontsize=7)
fig.tight_layout(); fig.savefig(d / "scaling.png", dpi=130)
print(scal.to_string(index=False)); print(); print(pd.DataFrame(rows).to_string(index=False))
