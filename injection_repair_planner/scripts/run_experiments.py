"""Run the evaluation grid in parallel; results are appended to results/results_raw.csv as jobs finish.
usage: python scripts/run_experiments.py [--seeds 30] [--sizes 10,25,50,100] [--procs 2] [--out results]"""
import argparse
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from repairplan.experiments import append_rows, run_job  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--sizes", default="10,25,50,100")
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--out", default=str(ROOT / "results"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(exist_ok=True)
    sizes = [int(x) for x in a.sizes.split(",")]
    jobs = [(s, seed) for seed in range(a.seeds) for s in sizes]  # seed-major: partial runs stay balanced
    raw, log = out / "results_raw.csv", out / "progress.log"
    t0 = time.time()
    print(f"{len(jobs)} jobs, {a.procs} processes -> {out}", flush=True)
    with mp.Pool(a.procs) as pool, open(out / "samples.jsonl", "a") as sf:
        for i, r in enumerate(pool.imap_unordered(run_job, jobs), 1):
            append_rows(str(raw), r["rows"])
            for s in r["samples"]:
                sf.write(json.dumps(s) + "\n")
            sf.flush()
            msg = (f"{i}/{len(jobs)} size={r['size']} seed={r['seed']} {r['seconds']:.0f}s "
                   f"plan={r['plan_status']} elapsed={time.time()-t0:.0f}s")
            print(msg, flush=True)
            with open(log, "a") as lf:
                lf.write(msg + "\n")


if __name__ == "__main__":
    mp.freeze_support()
    main()
