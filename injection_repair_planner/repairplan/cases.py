"""Ready-made cases: the real WK41 replay and seeded synthetic cases."""
from __future__ import annotations

from typing import Tuple

from .events import MACHINE_DOWN, Event
from .plans import baseline_plan, calibrate_due_dates, human_plan
from .realdata import load_real
from .scenario import Impact, Scenario, build_scenario, detect_impact, make_scenario
from .synth import generate_plant


def real_case(down_hours: float = 36.0, machine: str = "IMP07", t_event: float | None = None):
    """Replay of Tue 06-Oct 07:00: `machine` breaks down (default IMP07, as in the Status sheet)."""
    plant, hseq, raw = load_real()
    old = human_plan(plant, hseq, raw["_lb"])
    notes = calibrate_due_dates(plant, old)
    t = float(raw["tue_breakdown_t"]) if t_event is None else t_event
    ev = Event(MACHINE_DOWN, t, t + down_hours, machine=machine)
    scn = build_scenario(plant, old, ev)
    return plant, old, scn, detect_impact(scn), notes


def synth_base(n_machines: int, seed: int, plan_time: float = 10.0, workers: int = 1):
    """Plant + baseline CP-SAT plan, due dates calibrated so the undisturbed plan is on time
    (an order the baseline cannot finish on time gets due = plan completion + 6 h)."""
    plant = generate_plant(n_machines, seed)
    old, res = baseline_plan(plant, time_limit=plan_time, seed=seed, workers=workers)
    notes = calibrate_due_dates(plant, old, buffer_h=6.0)
    return plant, old, notes, res


def synth_case(n_machines: int, seed: int, etype: str, plan_time: float = 10.0, workers: int = 1, base=None):
    plant, old, notes, _ = base or synth_base(n_machines, seed, plan_time, workers)
    scn, imp = make_scenario(plant, old, etype, seed)
    return plant, old, scn, imp
