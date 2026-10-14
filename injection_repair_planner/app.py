"""Live demo: pick a plant and a disruption, press Run, watch the planner repair the plan.
Start with:  streamlit run app.py"""
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from repairplan import methods as M  # noqa: E402
from repairplan.cases import real_case, synth_base  # noqa: E402
from repairplan.events import EVENT_TYPES  # noqa: E402
from repairplan.explain import describe_repair, explain_orders  # noqa: E402
from repairplan.repair import RepairParams, minimal_change_repair  # noqa: E402
from repairplan.scenario import make_scenario, prepare_repair_state  # noqa: E402
from repairplan.viz import before_after  # noqa: E402

st.set_page_config(page_title="Minimal-change disruption repair", layout="wide")
st.title("Minimal-change disruption repair")
st.caption("Injection-molding plant: fix the disruption, not the whole week.")


@st.cache_resource(show_spinner="Building the weekly plan (CP-SAT + dispatch)...")
def get_base(size: int, seed: int):
    return synth_base(size, seed, {10: 4.0, 25: 6.0, 50: 10.0}[size], 1)


with st.sidebar:
    source = st.radio("Plant", ["Real WK41 replay (Tue 07:00)", "Synthetic plant"])
    if source.startswith("Real"):
        hours = st.slider("IMP07 breakdown (hours)", 12, 72, 36, 4)
    else:
        size = st.selectbox("Machines", [10, 25, 50], index=0)
        seed = st.number_input("Seed", 0, 99, 3)
        etype = st.selectbox("Disruption", EVENT_TYPES)
    lam = st.slider("Cost of one plan change (lambda)", 0.0, 40.0, 8.0, 1.0,
                    help="Higher = repair keeps the old plan more strictly")
    show_baselines = st.checkbox("Compare with baselines", True)
    run = st.button("Run repair", type="primary")

if not run:
    st.info("Choose a plant and a disruption on the left, then press **Run repair**.")
    st.stop()

P = RepairParams(lam=lam, time_limit=3.0)
t0 = time.time()
if source.startswith("Real"):
    plant, old, scn, imp, notes = real_case(hours)
else:
    plant, old, notes, _ = get_base(size, int(seed))
    scn, imp = make_scenario(plant, old, etype, int(seed))
state = prepare_repair_state(scn, imp, P.frozen_h)

with st.spinner("Repairing..."):
    res = minimal_change_repair(state, P)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Disruption", scn.event.describe())
c2.metric("Late orders", f"{res.metrics.late_orders}", f"{res.metrics.late_orders - imp.metrics.late_orders} vs doing nothing",
          delta_color="inverse")
c3.metric("Plan changes", res.metrics.changes)
c4.metric("Repair time", f"{res.runtime:.1f} s")

with tempfile.TemporaryDirectory() as d:
    png = before_after(scn, imp, state, res, str(Path(d) / "g.png"), max_rows=10 if source.startswith("Synth") else 12)
    st.image(png)

left, right = st.columns(2)
with left:
    st.subheader("Why late, and the cheapest fix")
    exps = explain_orders(state, P, max_orders=4)
    if not exps:
        st.success("No order is late: the old plan still works.")
    for e in exps:
        st.write("- " + e.text)
with right:
    st.subheader("What the repair changes")
    for i, s in enumerate(describe_repair(state, res), 1):
        st.write(f"{i}. {s}")

if show_baselines:
    st.subheader("Compared with other methods")
    with st.spinner("Running baselines..."):
        rs = [M.do_nothing_result(state, P), M.manual_rule(state, P), M.dispatch_result(state, P, "ATC"),
              M.full_resolve(state, P, 4.0), res]
    st.dataframe(pd.DataFrame([dict(method=r.method, late_orders=r.metrics.late_orders,
                                    late_pieces=round(r.metrics.late_qty), plan_changes=r.metrics.changes,
                                    seconds=round(r.runtime, 2)) for r in rs]), hide_index=True)
