"""Demo outputs: the real 'Tuesday 7 AM' replay + one synthetic example per disruption type.
Writes Gantt PNGs, plain-language explanations and a method comparison table to results/demo/."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd  # noqa: E402

from repairplan import methods as M  # noqa: E402
from repairplan.cases import real_case, synth_base, synth_case  # noqa: E402
from repairplan.events import EVENT_TYPES  # noqa: E402
from repairplan.explain import describe_repair, explain_orders  # noqa: E402
from repairplan.repair import RepairParams, minimal_change_repair  # noqa: E402
from repairplan.scenario import prepare_repair_state  # noqa: E402
from repairplan.viz import before_after  # noqa: E402

OUT = ROOT / "results" / "demo"
OUT.mkdir(parents=True, exist_ok=True)


def run_all(st, P, tl=5.0, exact=True):
    rs = [M.do_nothing_result(st, P), M.manual_rule(st, P), M.dispatch_result(st, P, "EDD"),
          M.dispatch_result(st, P, "ATC"), M.full_resolve(st, P, tl), minimal_change_repair(st, P)]
    if exact:
        rs.append(M.exact_repair(st, P, tl))
    return rs


def table(rs):
    return pd.DataFrame([dict(method=r.method, late_orders=r.metrics.late_orders, late_qty=round(r.metrics.late_qty),
                              plan_changes=r.metrics.changes, runtime_s=round(r.runtime, 2)) for r in rs])


def report(name, plant, scn, imp, st, P, lines, exact=True, title=""):
    rs = run_all(st, P, exact=exact)
    mc = next(r for r in rs if r.method == "minimal_change_repair")
    before_after(scn, imp, st, mc, str(OUT / f"gantt_{name}.png"), title=title or scn.event.describe())
    t = table(rs)
    t.to_csv(OUT / f"methods_{name}.csv", index=False)
    lines += [f"## {title or name}", f"Disruption: {scn.event.describe()}", "",
              f"Impact if nothing is done: {imp.metrics.late_orders} late order(s), "
              f"{imp.metrics.late_qty:,.0f} late pieces.", ""]
    for e in explain_orders(st, P, max_orders=4):
        lines += [f"- {e.text}"]
    lines += ["", "Repaired plan (minimal change):"] + [f"  {i+1}. {s}" for i, s in enumerate(describe_repair(st, mc))]
    lines += ["", t.to_string(index=False), ""]


def main():
    P = RepairParams()
    lines = ["# Demo outputs", ""]
    plant, old, scn, imp, notes = real_case(36)
    st = prepare_repair_state(scn, imp, P.frozen_h)
    report("real_tuesday_0700", plant, scn, imp, st, P, lines,
           title="REAL WK41 replay: Tuesday 07:00, IMP07 breakdown (36 h)")
    lines += ["Due-date calibration notes: " + ("; ".join(notes) if notes else "none"), ""]
    base = synth_base(10, 3, 5.0, 1)
    for et in EVENT_TYPES:
        plant, old, scn, imp = synth_case(10, 3, et, 5.0, 1, base)
        st = prepare_repair_state(scn, imp, P.frozen_h)
        report(f"synth10_{et}", plant, scn, imp, st, P, lines, title=f"Synthetic 10 machines, {et}")
    (OUT / "demo_outputs.md").write_text("\n".join(lines))
    print("\n".join(lines[:60]))


if __name__ == "__main__":
    main()
