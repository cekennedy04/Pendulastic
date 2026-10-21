"""Why did 23 trials return None from compute_pt_params?

They were dropped from the test-retest cohort. If they are systematically the
collapsed swings, excluding them flatters the reliability numbers, so the
reason matters before anything is written up.

compute_pt_params has exactly three None paths:
  :2714  fewer than 40 finite angle samples
  :2819  fewer than 25 post-release samples
  :2846  |A0_raw| < 3 deg -- no measurable excursion

Rather than reimplement the pipeline's internals to guess which fired, this
traces the real call and records the last line executed before the return.

Run:  ~/miniconda3/python.exe diagnose_scoring_failures.py
"""
from __future__ import annotations

import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.chdir(BASE)

CACHE = os.path.join(BASE, "Model_Analysis_Outputs", "_cache",
                     "test_retest_params.json")
REASONS = {2714: "under 40 finite angle samples",
           2819: "under 25 post-release samples",
           2846: "|A0| under 3 deg (no measurable excursion)"}


def main() -> int:
    import numpy as np
    import batch_imu_vs_optitrack_rmse as batch
    import imu_calibration_tuner as tuner
    import rmse_pipeline_common as rpc
    import pendulastic_pt_score as pts
    from imu_calibration_config import load_config

    cache = json.load(open(CACHE, encoding="utf-8"))
    failed = {k for k, v in cache.items() if v and "error" in v}
    print(f"{len(failed)} trials to diagnose\n", flush=True)

    cfg = load_config()
    target = pts.compute_pt_params.__code__
    state = {"last": None}

    def tracer(frame, event, arg):
        if frame.f_code is not target:
            return None
        if event == "line":
            state["last"] = frame.f_lineno
        return tracer

    rows = []
    for tr in batch.discover_trials():
        key = tr["trial_key"]
        if key not in failed:
            continue
        rec = {"trial_key": key, "reason": "?", "n_raw": 0, "n_finite": 0,
               "span_s": 0.0, "a0_raw": None}
        try:
            samples = rpc.reconstruct_trial(tr["accel"], tr["gyro"], tr["mag"])
            t, ang = tuner.replay_trial(samples, dict(cfg),
                                        tick_s=tuner.ANALYSIS_TICK_S)
            t = np.asarray(t, float)
            ang = np.asarray(ang, float)
            fin = np.isfinite(ang)
            rec["n_raw"] = int(len(ang))
            rec["n_finite"] = int(fin.sum())
            rec["span_s"] = float(t[-1] - t[0]) if len(t) > 1 else 0.0
            if fin.sum():
                a = ang[fin]
                rec["range_deg"] = float(np.nanmax(a) - np.nanmin(a))
            state["last"] = None
            sys.settrace(tracer)
            try:
                p = pts.compute_pt_params(t, ang)
            finally:
                sys.settrace(None)
            line = state["last"]
            rec["exit_line"] = line
            rec["reason"] = REASONS.get(line, f"returned at line {line}"
                                        if p is None else "scored on retry")
        except Exception as e:
            rec["reason"] = f"raised {type(e).__name__}: {e}"[:90]
        rows.append(rec)
        print(f"  {key:28s} {rec['reason']}", flush=True)

    print()
    import collections
    tally = collections.Counter(r["reason"] for r in rows)
    print("=== why they failed ===")
    for reason, n in tally.most_common():
        print(f"  {n:3d}  {reason}")

    print("\n=== are these the collapsed swings? ===")
    rng = [r.get("range_deg") for r in rows if r.get("range_deg") is not None]
    if rng:
        rng = np.array(rng)
        print(f"  angle range of failed trials: median {np.median(rng):6.2f} deg, "
              f"min {rng.min():5.2f}, max {rng.max():6.2f}")
    kept = [v for k, v in cache.items() if v and "error" not in v
            and v.get("A0_deg")]
    if kept:
        a0 = np.array([v["A0_deg"] for v in kept])
        print(f"  A0 of trials that DID score:  median {np.median(a0):6.2f} deg, "
              f"min {a0.min():5.2f}, max {a0.max():6.2f}")

    print("\n=== spread across participants ===")
    per = collections.Counter(r["trial_key"].split("_")[0] for r in rows)
    print("  " + "  ".join(f"P{k}:{v}" for k, v in sorted(per.items())))

    out = os.path.join(BASE, "Model_Analysis_Outputs",
                       "scoring_failures_diagnosis.csv")
    import pandas as pd
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
