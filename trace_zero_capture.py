"""THROWAWAY PROBE -- why does zero capture never latch on 15 trials?

Two hypotheses already died to measurement:
  - role assignment: identical (all 'proximal') on failing and scoring trials
  - "never held still": FALSE. Failed trials have a median longest calm run of
    3.38 s against the 1.0 s window needed; scored trials 4.07 s.

So stop inferring and observe. The zero latches only when ALL of:
    flex_axis_armed
    (is_distal or is_solo)
    (pending_departure or flex_est.n_samples)
    omega_mag >= _FLEX_CAPTURE_THRESHOLD   (1.0 rad/s)

This traces the real call and records, per trial, how far down that chain
execution actually got.

Run:  ~/miniconda3/python.exe trace_zero_capture.py
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


def main() -> int:
    import numpy as np
    import pandas as pd
    import batch_imu_vs_optitrack_rmse as batch
    import imu_calibration_tuner as tuner
    import rmse_pipeline_common as rpc
    from imu_calibration_config import load_config

    cache = json.load(open(os.path.join(
        BASE, "Model_Analysis_Outputs", "_cache",
        "test_retest_params.json"), encoding="utf-8"))
    failed = {k for k, v in cache.items() if v and "error" in v}

    code = tuner.replay_trial.__code__
    # the outer gate is the line beginning "if flex_axis_armed and"
    src = open(os.path.join(BASE, "imu_calibration_tuner.py"),
               encoding="utf8").read().splitlines()
    gate_line = next(i + 1 for i, l in enumerate(src)
                     if "if flex_axis_armed and (is_distal or is_solo)" in l)
    print(f"outer gate at line {gate_line}\n", flush=True)

    cfg = load_config()
    rows = []
    for tr in batch.discover_trials():
        key = tr["trial_key"]
        st = {"gate_evals": 0, "gate_true": 0, "max_w_at_gate": 0.0,
              "armed_seen": False, "departure_seen": False}

        def tracer(frame, event, arg, st=st, gate_line=gate_line):
            if frame.f_code is not code:
                return None
            if event == "line" and frame.f_lineno == gate_line:
                loc = frame.f_locals
                st["gate_evals"] += 1
                if loc.get("flex_axis_armed"):
                    st["armed_seen"] = True
                s = loc.get("st")
                if s is not None and getattr(s, "pending_departure", False):
                    st["departure_seen"] = True
                fe = loc.get("flex_est")
                ok = (loc.get("flex_axis_armed")
                      and (loc.get("is_distal") or loc.get("is_solo"))
                      and (getattr(s, "pending_departure", False)
                           or getattr(fe, "n_samples", 0)))
                if ok:
                    st["gate_true"] += 1
                    w = float(loc.get("omega_mag") or 0.0)
                    st["max_w_at_gate"] = max(st["max_w_at_gate"], w)
            return tracer

        try:
            samples = rpc.reconstruct_trial(tr["accel"], tr["gyro"], tr["mag"])
            sys.settrace(tracer)
            try:
                t, ang = tuner.replay_trial(samples, dict(cfg),
                                            tick_s=tuner.ANALYSIS_TICK_S)
            finally:
                sys.settrace(None)
        except Exception as e:
            rows.append({"key": key, "failed": key in failed,
                         "note": f"{type(e).__name__}"})
            continue
        rows.append({"key": key, "failed": key in failed,
                     "n_out": len(t), **st})

    d = pd.DataFrame(rows)
    d.to_csv(os.path.join(BASE, "Model_Analysis_Outputs",
                          "zero_capture_trace.csv"), index=False)
    f = d[d.failed & d.n_out.notna()]
    s = d[~d.failed & d.n_out.notna()]
    print(f"{len(f)} failed / {len(s)} scored traced\n")
    print(f"{'':22s} {'gate evals':>11s} {'gate TRUE':>10s} "
          f"{'max |w| when true':>18s} {'armed?':>7s} {'departure?':>11s}")
    for lbl, sub in (("FAILED (median)", f), ("SCORED (median)", s)):
        if sub.empty:
            continue
        print(f"{lbl:22s} {sub.gate_evals.median():11.0f} "
              f"{sub.gate_true.median():10.0f} "
              f"{sub.max_w_at_gate.median():18.3f} "
              f"{sub.armed_seen.mean()*100:6.0f}% {sub.departure_seen.mean()*100:10.0f}%")
    print(f"\n_FLEX_CAPTURE_THRESHOLD = 1.0 rad/s -- max |w| while the outer gate")
    print("was open must reach it for the zero to latch.")
    print(f"\nfailed trials whose gate NEVER opened: "
          f"{int((f.gate_true == 0).sum())} of {len(f)}")
    print(f"failed trials where gate opened but |w| stayed under 1.0: "
          f"{int(((f.gate_true > 0) & (f.max_w_at_gate < 1.0)).sum())}")
    print(f"failed trials where gate opened AND |w| reached 1.0: "
          f"{int(((f.gate_true > 0) & (f.max_w_at_gate >= 1.0)).sum())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
