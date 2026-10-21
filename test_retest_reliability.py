"""Within-session test-retest reliability of the phone-IMU PT parameters.

The one quantitative result available without a reference instrument, a MAS
grade or the bench rig: the same leg, swung several times in one sitting,
should score the same each time. Every input is already on disk.

Scored on the PRODUCTION path -- reconstruct_trial -> replay_trial at
ANALYSIS_TICK_S (100 Hz) -> compute_pt_params -- not on raw prox_pitch, which
bypasses the AHRS and the EMA and is not what the app computes.

Reports per parameter:
  ICC(1,1)   one-way random, because repeat trials are not distinguishable
             raters -- "trial 2" carries no meaning that "trial 1" does not.
  SEM        within-subject SD, the spread of a single measurement.
  MDC95      1.96 * sqrt(2) * SEM -- the change a second measurement must show
             before it can be called real. The clinically useful number.
  CV         SEM / grand mean, for cross-parameter comparison.

Honours excluded_trials.json (operator exclusions only; this never drops a
trial on its own). Caches per-trial params so a re-run is cheap.

Run:  ~/miniconda3/python.exe test_retest_reliability.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import warnings

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.chdir(BASE)

CACHE = os.path.join(BASE, "Model_Analysis_Outputs", "_cache",
                     "test_retest_params.json")
OUT_CSV = os.path.join(BASE, "Model_Analysis_Outputs",
                       "test_retest_reliability.csv")

PARAMS = ["R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n",
          "f", "area_ratio"]
EXTRA = ["A0_deg", "omega_peak_deg_s"]
MIN_REPEATS = 3


def main() -> int:
    import numpy as np
    import pandas as pd
    import batch_imu_vs_optitrack_rmse as batch
    import imu_calibration_tuner as tuner
    import rmse_pipeline_common as rpc
    import pendulastic_pt_score as pts
    from imu_calibration_config import load_config

    cfg = load_config()
    trials = batch.discover_trials()
    print(f"{len(trials)} trials discovered", flush=True)

    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}

    for i, tr in enumerate(trials):
        key = tr["trial_key"]
        if key in cache:
            continue
        try:
            samples = rpc.reconstruct_trial(tr["accel"], tr["gyro"], tr["mag"])
            t, ang = tuner.replay_trial(samples, dict(cfg),
                                        tick_s=tuner.ANALYSIS_TICK_S)
            p = pts.compute_pt_params(np.asarray(t, float),
                                      np.asarray(ang, float))
        except Exception as e:
            cache[key] = {"error": f"{type(e).__name__}: {e}"[:140]}
        else:
            if not p:
                cache[key] = {"error": "compute_pt_params returned None"}
            else:
                rec = {k: (float(p[k]) if p.get(k) is not None
                           and math.isfinite(float(p[k])) else None)
                       for k in PARAMS + EXTRA if k in p}
                hold = p.get("hold_drift") or {}
                rec["hold_status"] = hold.get("status")
                cache[key] = rec
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        os.replace(tmp, CACHE)
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(trials)}", flush=True)

    excluded = set(json.load(open(os.path.join(BASE, "excluded_trials.json"),
                                  encoding="utf-8")))
    rows = []
    for tr in trials:
        key = tr["trial_key"]
        rec = cache.get(key)
        if not rec or "error" in rec:
            continue
        # trial_key is pid_leg_condition_Tn -- the group is everything but Tn
        parts = key.rsplit("_", 1)
        rows.append({"trial_key": key, "group": parts[0],
                     "excluded": key in excluded, **rec})
    d = pd.DataFrame(rows)
    scored = len(d)
    d = d[~d.excluded]
    print(f"\nscored {scored} trials; {scored - len(d)} operator-excluded "
          f"-> {len(d)} kept", flush=True)

    sizes = d.groupby("group").size()
    keep = set(sizes[sizes >= MIN_REPEATS].index)
    r = d[d.group.isin(keep)]
    pids = {g.split("_")[0] for g in keep}
    print(f"{len(keep)} groups with >= {MIN_REPEATS} repeats, "
          f"{len(r)} trials, {len(pids)} participants\n", flush=True)
    if r.empty:
        return 1

    def icc1(frame, col):
        """ICC(1,1): one-way random effects, single measurement."""
        groups = [g[col].dropna().values for _, g in frame.groupby("group")]
        groups = [g for g in groups if len(g) >= 2]
        if len(groups) < 3:
            return None
        k = np.mean([len(g) for g in groups])
        allv = np.concatenate(groups)
        n = len(groups)
        gm = allv.mean()
        msb = sum(len(g) * (g.mean() - gm) ** 2 for g in groups) / (n - 1)
        msw = sum(((g - g.mean()) ** 2).sum() for g in groups) / (len(allv) - n)
        icc = (msb - msw) / (msb + (k - 1) * msw) if (msb + (k - 1) * msw) else np.nan
        sem = math.sqrt(msw)
        return {"icc": icc, "sem": sem, "mdc95": 1.96 * math.sqrt(2) * sem,
                "mean": gm, "n_groups": n, "n_trials": len(allv)}

    print(f"{'parameter':16s} {'ICC(1,1)':>9s} {'SEM':>9s} {'MDC95':>9s} "
          f"{'mean':>9s} {'CV%':>7s} {'groups':>7s}")
    out = []
    for col in PARAMS + EXTRA:
        if col not in r:
            continue
        s = icc1(r, col)
        if not s:
            continue
        cv = (s["sem"] / abs(s["mean"]) * 100) if s["mean"] else float("nan")
        out.append({"parameter": col, **s, "cv_pct": cv})
        print(f"{col:16s} {s['icc']:9.3f} {s['sem']:9.3f} {s['mdc95']:9.3f} "
              f"{s['mean']:9.3f} {cv:7.1f} {s['n_groups']:7d}")

    pd.DataFrame(out).to_csv(OUT_CSV, index=False)
    print(f"\nwrote {OUT_CSV}")

    print("\ninterpretation bands (Koo & Li): "
          "<0.50 poor, 0.50-0.75 moderate, 0.75-0.90 good, >0.90 excellent")
    hs = r.get("hold_status")
    if hs is not None:
        print(f"\npre-release hold across kept trials: "
              f"{dict(hs.value_counts(dropna=False))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
