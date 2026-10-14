"""Gantt charts: old plan / disrupted (do-nothing) / repaired plan, stacked with a shared time axis."""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle

from .events import CAVITY_BLOCK, MACHINE_DOWN, MOLD_UNAVAILABLE
from .sim import task_changed

PALETTE = plt.get_cmap("tab20").colors


def _color(key: str, cache: Dict[str, tuple]):
    if key not in cache:
        cache[key] = PALETTE[len(cache) % len(PALETTE)]
    return cache[key]


def select_machines(state, result, max_rows: int = 12) -> List[str]:
    scn, plant = state.scn, state.scn.plant
    prio: Dict[str, int] = {}
    e = scn.event
    if e.machine:
        prio[e.machine] = 0
    base = state.initial_solution()
    for tid, t in result.sol.tasks.items():
        b = base.tasks.get(tid)
        if task_changed(t, result.acts.get(tid)) or b is None:
            prio.setdefault(t.machine, 1)
            if b is not None:
                prio.setdefault(b.machine, 1)
    if e.mold:
        for t in base.tasks.values():
            if t.mold == e.mold:
                prio.setdefault(t.machine, 1)
    for o, r in result.metrics.per_order.items():
        if r.late:
            for t in list(result.sol.tasks.values()):
                if t.order == o:
                    prio.setdefault(t.machine, 2)
    for tid, t in base.tasks.items():
        prio.setdefault(t.machine, 5)
    rows = sorted(prio, key=lambda m: (prio[m], m))[:max_rows]
    return sorted(rows)


def draw_gantt(ax, plant, tasks, acts, orders, per_order, machines: List[str], event, t_r: Optional[float],
               title: str, tmax: float, changed: Iterable[str] = (), cache=None, show_labels: bool = True):
    cache = cache if cache is not None else {}
    changed = set(changed)
    row = {m: i for i, m in enumerate(machines)}
    # outage / cavity windows of the event
    if event is not None and event.type in (MACHINE_DOWN, MOLD_UNAVAILABLE, CAVITY_BLOCK) and event.t_end:
        for m in machines:
            hit = (event.type == MACHINE_DOWN and m == event.machine)
            if event.type in (MOLD_UNAVAILABLE, CAVITY_BLOCK):
                hit = any(tasks[tid].machine == m and tasks[tid].mold == event.mold for tid in tasks if tid in acts)
            if hit:
                ax.add_patch(Rectangle((event.t, row[m] - 0.45), event.t_end - event.t, 0.9, fill=False,
                                       hatch="////", edgecolor="#c0392b", lw=0, alpha=0.55, zorder=1))
    for tid, t in tasks.items():
        if t.machine not in row or tid not in acts:
            continue
        a = acts[tid]
        y = row[t.machine]
        o = orders.get(t.order)
        late = per_order.get(t.order).late if per_order and t.order in per_order else False
        col = _color(o.product if o else t.order, cache)
        if a.ps > a.cs + 1e-6:  # changeover
            ax.add_patch(Rectangle((a.cs, y - 0.3), a.ps - a.cs, 0.6, color="#888888", alpha=0.6, lw=0, zorder=2))
        for s, e, r in a.segs:
            if r <= 0 or e <= s:
                continue
            ax.add_patch(Rectangle((s, y - 0.4), e - s, 0.8, facecolor=col, alpha=1.0 if r > 0 else 0.3,
                                   edgecolor="#d62728" if late else "white", lw=1.8 if late else 0.5, zorder=3))
        if tid in changed:
            ax.add_patch(Rectangle((a.ps, y - 0.43), max(0.1, min(a.end, tmax) - a.ps), 0.86, fill=False,
                                   edgecolor="black", lw=2.2, zorder=5))
        if show_labels and a.end - a.ps > 0.05 * tmax:
            ax.text((a.ps + min(a.end, tmax)) / 2, y, t.order[-5:] + ("*" if t.kind == "new" else ""),
                    ha="center", va="center", fontsize=6.5, zorder=6,
                    color="#d62728" if late else "black", fontweight="bold" if late else "normal")
    if event is not None:
        ax.axvline(event.t, color="#c0392b", ls="--", lw=1.2, zorder=4)
    if t_r is not None:
        ax.axvspan(event.t if event else 0, t_r, color="#3498db", alpha=0.08, zorder=0)
        ax.axvline(t_r, color="#2980b9", ls=":", lw=1.2, zorder=4)
    ax.set_xlim(0, tmax)
    ax.set_ylim(len(machines) - 0.5, -0.6)
    ax.set_yticks(range(len(machines)))
    ax.set_yticklabels(machines, fontsize=8)
    ax.set_xticks(range(0, int(tmax) + 1, 24))
    ax.set_xticklabels([f"D{d // 24 + 1}" for d in range(0, int(tmax) + 1, 24)], fontsize=8)
    ax.grid(axis="x", color="#dddddd", lw=0.6)
    ax.set_title(title, fontsize=10, loc="left")


def before_after(scn, imp, state, result, path: str, max_rows: int = 12, title: str = ""):
    plant = scn.plant
    machines = select_machines(state, result, max_rows)
    tmax = plant.horizon + 24.0
    merged, _ = state.merged(result.sol)
    cache: Dict[str, tuple] = {}
    changed = [tid for tid, t in result.sol.tasks.items() if task_changed(t, result.acts.get(tid))]
    fig, axes = plt.subplots(3, 1, figsize=(13, 1.5 + 0.62 * len(machines) * 3 / 1.6 + 2), sharex=True)
    draw_gantt(axes[0], plant, scn.old.tasks, scn.base_acts, scn.orders, scn.base_metrics.per_order, machines,
               None, None, "1. Original plan (no disruption)", tmax, cache=cache)
    draw_gantt(axes[1], plant, imp.solution.tasks, imp.acts, scn.orders, imp.metrics.per_order, machines,
               scn.event, state.t_r, f"2. Disruption, nothing changed: {imp.metrics.late_orders} late order(s)  "
               f"[{scn.event.describe()}]", tmax, cache=cache)
    draw_gantt(axes[2], plant, merged, result.acts, scn.orders, result.metrics.per_order, machines, scn.event,
               state.t_r, f"3. Minimal-change repair: {result.metrics.late_orders} late order(s), "
               f"{result.metrics.changes} plan change(s)", tmax, changed=changed, cache=cache)
    handles = [Patch(facecolor="#888888", alpha=0.6, label="changeover"),
               Patch(fill=False, hatch="////", edgecolor="#c0392b", label="outage window"),
               Patch(fill=False, edgecolor="#d62728", lw=2, label="late order"),
               Patch(fill=False, edgecolor="black", lw=2, label="changed by repair"),
               Patch(color="#3498db", alpha=0.15, label="frozen zone (kept as is)")]
    fig.legend(handles=handles, loc="lower center", ncol=5, fontsize=8, frameon=False)
    if title:
        fig.suptitle(title, fontsize=12, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.04, 1, 0.97))
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path
