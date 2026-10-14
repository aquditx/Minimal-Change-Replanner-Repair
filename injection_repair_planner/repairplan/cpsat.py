"""CP-SAT models.

One builder serves three uses:
  * baseline plan           (all orders, t0 = 0, minimise weighted tardiness)
  * full re-solve           (open work after a disruption, from scratch, minimise lateness)
  * exact repair comparison (unaffected tasks fixed, deviation penalty per changed task)

Modelling
  * time is discretised in 15-minute slots
  * one optional interval per (task, machine, mold) option -> machine choice AND mold choice
  * no-overlap per machine and per mold (dual resource); machine down / mold unavailable windows are
    fixed obstacle intervals (so runs cannot straddle an outage; the simulator may still pause a run)
  * sequence-dependent changeovers: for every pair of options on the same machine, an ordering literal
    implies end_i + changeover(i,j) <= start_j  (a transition constraint)
  * cavity blocks stretch durations by the average capacity factor over the near-term window
Plans are always re-played through the exact simulator, so CP-SAT approximations never leak into metrics.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ortools.sat.python import cp_model

from .model import Plant
from .sim import CHANGE_TOL, SimContext, State0

SLOT = 0.25


def slots_up(h: float) -> int:
    return int(math.ceil(h / SLOT - 1e-9))


@dataclass
class CpTask:
    id: str
    order: str
    qty: float
    due: float
    weight: float
    lb: float  # earliest production start
    options: List[Tuple[str, str]]
    ref_machine: Optional[str] = None
    ref_start: Optional[float] = None
    only_machine_ref: bool = False  # tails: unchanged iff same machine
    fixed: Optional[Tuple[str, str, float]] = None  # (machine, mold, start) fixed in the model


@dataclass
class CpResult:
    assign: Dict[str, Tuple[str, str, float]]
    status: str
    objective: float
    best_bound: float
    seconds: float
    n_vars: int = 0


def _cav_avg(ctx: SimContext, mold: str, a: float, b: float) -> float:
    wins = ctx.cav.get(mold)
    if not wins or b <= a:
        return 1.0
    tot = 0.0
    for wa, wb, f in wins:
        lo, hi = max(a, wa), min(b, wb)
        if hi > lo:
            tot += (hi - lo) * (1.0 - f)
    return max(0.1, 1.0 - tot / (b - a))


def solve_cpsat(plant: Plant, tasks: List[CpTask], st0: State0, ctx: SimContext, t0: float,
                time_limit: float, objective: str = "wtard", lam: float = 8.0, seed: int = 0,
                workers: int = 1, w1: float = 100.0, w2: float = 100.0, w3: float = 0.5,
                hints: Optional[Dict[str, Tuple[str, str, float]]] = None) -> Optional[CpResult]:
    """objective: 'wtard' (weighted tardiness + compaction) or 'repair' (w1*late + w2*late_qty + w3*wtard + lam*changes)."""
    t_wall = time.time()
    m = cp_model.CpModel()
    max_lb = max([t.lb for t in tasks] + [t.fixed[2] for t in tasks if t.fixed] + [t0, 0.0])
    max_end = slots_up(max(plant.horizon + 120.0, max_lb + 96.0))  # horizon must cover every earliest start
    t0s = slots_up(t0) if t0 > 0 else 0
    start, end, dur_var = {}, {}, {}
    pres: Dict[Tuple[str, str, str], cp_model.IntVar] = {}  # (task, machine, mold) -> literal
    ivs_m: Dict[str, list] = {}
    ivs_k: Dict[str, list] = {}
    opts_on_m: Dict[str, list] = {}
    opt_dur: Dict[Tuple[str, str, str], int] = {}

    for t in tasks:
        lb = max(slots_up(t.lb), t0s)
        if t.fixed:
            fm, fk, fs = t.fixed
            s = m.NewIntVar(slots_up(fs), slots_up(fs), f"s_{t.id}")
            options = [(fm, fk)]
        else:
            s = m.NewIntVar(lb, max_end, f"s_{t.id}")
            options = t.options
        start[t.id] = s
        e = m.NewIntVar(0, max_end + 4000, f"e_{t.id}")
        end[t.id] = e
        lits = []
        for (mid, kid) in options:
            r = plant.rate(mid, kid)
            base = t.qty / r
            f = _cav_avg(ctx, kid, t0, t0 + 2.0 * base + 1.0)
            d = max(1, slots_up(base / f))
            lit = m.NewBoolVar(f"b_{t.id}_{mid}_{kid}")
            pres[(t.id, mid, kid)] = lit
            opt_dur[(t.id, mid, kid)] = d
            iv = m.NewOptionalIntervalVar(s, d, s + d, lit, f"i_{t.id}_{mid}_{kid}")
            ivs_m.setdefault(mid, []).append(iv)
            ivs_k.setdefault(kid, []).append(iv)
            opts_on_m.setdefault(mid, []).append((t, kid, lit, d))
            m.Add(e == s + d).OnlyEnforceIf(lit)
            # machine / mold readiness from the simulation snapshot
            mf = st0.machine_free.get(mid, 0.0)
            co0 = plant.co(st0.machine_mold.get(mid), kid, mid)
            m.Add(s >= slots_up(mf + co0)).OnlyEnforceIf(lit)
            kf = st0.mold_free.get(kid, 0.0)
            if kf > 0:
                m.Add(s >= slots_up(kf)).OnlyEnforceIf(lit)
            lits.append(lit)
        m.AddExactlyOne(lits)
        dur_var[t.id] = e - s

    # outage obstacles (fixed intervals): machine down and mold unavailable windows
    for mid, wins in ctx.down.items():
        for a, b in wins:
            if b > t0 and mid in ivs_m:
                a2, b2 = max(slots_up(a) if a > t0 else t0s, 0), slots_up(b)
                ivs_m[mid].append(m.NewIntervalVar(a2, max(1, b2 - a2), a2 + max(1, b2 - a2), f"dn_{mid}_{a}"))
    for kid, wins in ctx.off.items():
        for a, b in wins:
            if b > t0 and kid in ivs_k:
                a2, b2 = max(slots_up(a) if a > t0 else t0s, 0), slots_up(b)
                ivs_k[kid].append(m.NewIntervalVar(a2, max(1, b2 - a2), a2 + max(1, b2 - a2), f"off_{kid}_{a}"))
    for mid, ivs in ivs_m.items():
        m.AddNoOverlap(ivs)
    for kid, ivs in ivs_k.items():
        m.AddNoOverlap(ivs)

    # sequence-dependent changeovers (pairwise transition constraints)
    n_pairs = 0
    for mid, lst in opts_on_m.items():
        for i in range(len(lst)):
            ti, ki, li, di = lst[i]
            for j in range(i + 1, len(lst)):
                tj, kj, lj, dj = lst[j]
                if ti.id == tj.id:
                    continue
                if ti.fixed and tj.fixed:
                    continue
                cij = slots_up(plant.co(ki, kj, mid))
                cji = slots_up(plant.co(kj, ki, mid))
                bij = m.NewBoolVar("")
                bji = m.NewBoolVar("")
                m.AddBoolOr([bij, bji, li.Not(), lj.Not()])
                m.Add(end[ti.id] + cij <= start[tj.id]).OnlyEnforceIf(bij)
                m.Add(end[tj.id] + cji <= start[ti.id]).OnlyEnforceIf(bji)
                m.AddImplication(bij, li)
                m.AddImplication(bij, lj)
                m.AddImplication(bji, li)
                m.AddImplication(bji, lj)
                m.AddBoolOr([bij.Not(), bji.Not()])
                n_pairs += 1

    obj = []
    sc = 40  # integer scaling of the repair objective
    for t in tasks:
        if t.fixed:
            continue
        due_s = slots_up(t.due)
        tard = m.NewIntVar(0, max_end + 4000, f"td_{t.id}")
        m.Add(tard >= end[t.id] - due_s)
        wi = max(1, int(round(t.weight * 100)))
        if objective == "wtard":
            obj.append(wi * tard)
            obj.append(start[t.id])  # compaction: left-shift (tiny weight)
        else:
            late = m.NewBoolVar(f"late_{t.id}")
            m.Add(end[t.id] <= due_s).OnlyEnforceIf(late.Not())
            m.Add(end[t.id] >= due_s + 1).OnlyEnforceIf(late)
            durs = [opt_dur[(t.id, a, b)] for (a, b) in t.options if (t.id, a, b) in opt_dur]
            avg_dur = max(1.0, sum(durs) / len(durs))
            rate_slot = t.qty / avg_dur  # pieces per slot
            capped = m.NewIntVar(0, max_end + 4000, f"cap_{t.id}")
            m.AddMinEquality(capped, [tard, dur_var[t.id]])
            kpcs = max(1, int(round(rate_slot / 1000.0)))
            obj.append(int(round(w1 * sc)) * late)
            obj.append(max(1, int(round(w2 * sc * kpcs / 1000.0))) * capped)
            obj.append(max(1, int(round(w3 * sc * t.weight / 4.0))) * tard)
            if lam > 0 and t.ref_machine is not None:
                same_m = [pres[(t.id, a, b)] for (a, b) in t.options if a == t.ref_machine and (t.id, a, b) in pres]
                unch = m.NewBoolVar(f"un_{t.id}")
                if not same_m:
                    m.Add(unch == 0)
                else:
                    m.Add(sum(same_m) == 1).OnlyEnforceIf(unch)
                    if not t.only_machine_ref and t.ref_start is not None:
                        rs = slots_up(t.ref_start)
                        tol = int(CHANGE_TOL / SLOT)
                        m.Add(start[t.id] <= rs + tol).OnlyEnforceIf(unch)
                        m.Add(start[t.id] >= rs - tol).OnlyEnforceIf(unch)
                obj.append(int(round(lam * sc)) * (1 - unch))
    m.Minimize(sum(obj))
    by_task: Dict[str, list] = {}
    for (a, mm, kk), lit in pres.items():
        by_task.setdefault(a, []).append((mm, kk, lit))
    if hints:
        for tid, (mid, kid, st) in hints.items():
            if tid in start:
                m.AddHint(start[tid], slots_up(st))
                for mm, kk, lit in by_task.get(tid, ()):
                    m.AddHint(lit, 1 if (mm == mid and kk == kid) else 0)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit)
    solver.parameters.num_workers = int(workers)
    solver.parameters.random_seed = int(seed)
    solver.parameters.log_search_progress = False
    err = m.Validate()
    if err:
        raise RuntimeError(f'CP-SAT model invalid: {err[:300]}')
    status = solver.Solve(m)
    name = solver.StatusName(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return CpResult({}, name, math.inf, -math.inf, time.time() - t_wall, len(pres))
    assign = {}
    for t in tasks:
        for mid, kid, lit in by_task.get(t.id, ()):
            if solver.Value(lit):
                assign[t.id] = (mid, kid, solver.Value(start[t.id]) * SLOT)
                break
    return CpResult(assign, name, solver.ObjectiveValue(), solver.BestObjectiveBound(),
                    time.time() - t_wall, len(pres))
