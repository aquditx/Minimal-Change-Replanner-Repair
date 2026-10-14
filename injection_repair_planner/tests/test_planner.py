import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from repairplan.cases import real_case, synth_base, synth_case
from repairplan.events import (CAVITY_BLOCK, EVENT_TYPES, MACHINE_DOWN, QUALITY_HOLD, RUSH_ORDER, Event,
                               generate_event)
from repairplan.methods import do_nothing_result, dispatch_result, manual_rule
from repairplan.repair import (Evaluator, RepairParams, minimal_change_repair, objective_of, relocations)
from repairplan.scenario import build_scenario, detect_impact, prepare_repair_state
from repairplan.sim import SimContext, Task, simulate_solution, task_changed
from repairplan.synth import generate_plant


@pytest.fixture(scope="module")
def real():
    plant, old, scn, imp, notes = real_case(36)
    return plant, old, scn, imp, prepare_repair_state(scn, imp, 4.0)


@pytest.fixture(scope="module")
def small():
    base = synth_base(10, 1, 3.0, 1)
    return base, synth_case(10, 1, MACHINE_DOWN, 3.0, 1, base)


def test_old_plan_on_time_before_disruption(real):
    plant, old, scn, imp, st = real
    assert all(not r.late for r in scn.base_metrics.per_order.values())


def test_machine_down_stops_production(real):
    plant, old, scn, imp, st = real
    e = scn.event
    for tid, a in imp.acts.items():
        t = imp.solution.tasks[tid]
        if t.machine == e.machine:
            for s, en, r in a.segs:
                if r > 0:  # no production inside the outage window
                    assert en <= e.t + 1e-6 or s >= e.t_end - 1e-6


def test_cavity_block_reduces_rate():
    plant = generate_plant(10, 1)
    ctx0 = SimContext(plant)
    m, k = plant.options(next(iter(plant.orders.values())).product)[0]
    mold = plant.molds[k]
    ev = Event(CAVITY_BLOCK, 0.0, 100.0, machine=m, mold=k, k=mold.cavities // 2, n=mold.cavities)
    ctx = SimContext(plant, [ev])
    e0, _ = ctx0.qty_end(m, k, 0.0, 100000)
    e1, _ = ctx.qty_end(m, k, 0.0, 100000)
    assert e1 > e0 * 1.5  # about half the cavities -> about double the time


def test_plan_change_definition(real):
    plant, old, scn, imp, st = real
    base = st.initial_solution()
    moved = [t for t in base.tasks.values() if t.kind == "old"][0]
    t_same = moved.clone(machine=moved.ref_machine)
    other = [m for m in plant.machines if m != moved.ref_machine][0]
    assert task_changed(moved.clone(machine=other), None)       # different machine = change
    assert task_changed(t_same.clone(split=True), None)         # split = change
    tail = [t for t in base.tasks.values() if t.kind == "tail"][0]
    assert not task_changed(tail, st.hist_acts.get(tail.id))    # tail resumes on same machine: no change
    assert not task_changed(Task("x", "o", 1, "m", "k", 0.0), None)  # new work has no reference


def test_real_replay_repair(real):
    plant, old, scn, imp, st = real
    assert imp.metrics.late_orders == 2
    r = minimal_change_repair(st, RepairParams(time_limit=2.0))
    assert r.metrics.late_orders == 0
    assert r.metrics.changes <= 2


def test_evaluator_matches_exact_replay(real):
    plant, old, scn, imp, st = real
    P = RepairParams()
    ev = Evaluator(st, st.initial_solution(), P)
    rng = random.Random(0)
    for _ in range(15):
        tid = rng.choice(list(ev.sol.tasks))
        moves = relocations(ev, tid)
        if not moves:
            continue
        seq_upd, task_upd = rng.choice(moves)
        ev.commit(ev.trial(seq_upd, task_upd))
        _, met = st.run(ev.sol)
        assert abs(ev.total - objective_of(met, P)) < 1e-6


def test_tail_keeps_its_mold(real):
    plant, old, scn, imp, st = real
    r = minimal_change_repair(st, RepairParams(time_limit=1.5))
    for tid, t in r.sol.tasks.items():
        if t.kind == "tail":
            assert t.mold == st.open_tasks[tid].mold


def test_determinism_of_events():
    plant, old, _, _ = synth_base(10, 2, 2.0, 1)
    ctx = SimContext(plant)
    acts = simulate_solution(ctx, old)
    for et in EVENT_TYPES:
        a = generate_event(plant, old.tasks, acts, et, 5)
        b = generate_event(plant, old.tasks, acts, et, 5)
        assert a.describe() == b.describe()


def test_all_methods_run_and_never_crash(small):
    base, (plant, old, scn, imp) = small
    st = prepare_repair_state(scn, imp, 4.0)
    P = RepairParams(time_limit=1.0)
    for r in (do_nothing_result(st, P), manual_rule(st, P), dispatch_result(st, P, "EDD"),
              dispatch_result(st, P, "ATC"), minimal_change_repair(st, P)):
        assert r.metrics.late_orders >= 0 and r.runtime >= 0


def test_repair_never_worse_than_do_nothing(small):
    base, (plant, old, scn, imp) = small
    st = prepare_repair_state(scn, imp, 4.0)
    P = RepairParams(time_limit=1.5)
    assert minimal_change_repair(st, P).objective <= do_nothing_result(st, P).objective + 1e-6


@pytest.mark.parametrize("et", [RUSH_ORDER, QUALITY_HOLD])
def test_new_work_event_types(et):
    base = synth_base(10, 1, 2.0, 1)
    plant, old, scn, imp = synth_case(10, 1, et, 2.0, 1, base)
    assert scn.new_tasks
    st = prepare_repair_state(scn, imp, 4.0)
    r = minimal_change_repair(st, RepairParams(time_limit=1.0))
    assert r.objective <= do_nothing_result(st, RepairParams()).objective + 1e-6
