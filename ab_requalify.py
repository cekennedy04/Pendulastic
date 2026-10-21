"""THROWAWAY PROBE -- A/B the A2 re-qualification relaxation.

10 corpus trials arm the release gate after a calm hold, are revoked by a
sub-threshold wobble, and the real swing then arrives before a fresh full calm
window can requalify them. PENDULASTIC_RELAX_REQUALIFY keeps eligibility so
the next departure re-arms immediately.

The question is not "does it recover trials" -- it will. It is whether the
recovered trials are measurements or fabrications. A zero captured mid-handling
produces a wrong A0, and a wrong A0 inside a repeat group raises within-group
variance. So test-retest is the discriminator: a genuine recovery should leave
ICC roughly where it was or improve it; a fabricated one degrades it.

Usage:  ~/miniconda3/python.exe ab_requalify.py <out.csv>
"""
from __future__ import annotations

import os
import sys
import warnings

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.chdir(BASE)

PARAMS = ["R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n",
          "f", "area_ratio", "A0_deg"]


def main() -> int:
    import numpy as np
    import pandas as pd
    import batch_imu_vs_optitrack_rmse as batch
    import imu_calibration_tuner as tuner
    import rmse_pipeline_common as rpc
    import pendulastic_pt_score as pts
    from imu_calibration_config import load_config

    out_path = sys.argv[1] if len(sys.argv) > 1 else "ab_out.csv"
    cfg = load_config()
    print(f"relax_requalify = {tuner._RELAX_REQUALIFY}", flush=True)

    rows = []
    for tr in batch.discover_trials():
        key = tr["trial_key"]
        if not key:            # one corpus trial has no derivable key
            continue
        rec = {"key": key, "group": key.rsplit("_", 1)[0],
               "scored": False, "reason": ""}
        try:
            samples = rpc.reconstruct_trial(tr["accel"], tr["gyro"], tr["mag"])
            t, ang, q = tuner.replay_trial_detailed(
                samples, dict(cfg), tick_s=tuner.ANALYSIS_TICK_S)
            if not q.ok:
                rec["reason"] = q.reason
            else:
                p = pts.compute_pt_params(np.asarray(t, float),
                                          np.asarray(ang, float))
                if p is None:
                    rec["reason"] = "params_none"
                else:
                    rec["scored"] = True
                    for f in PARAMS:
                        rec[f] = p.get(f)
        except Exception as e:
            rec["reason"] = f"exc_{type(e).__name__}"
        rows.append(rec)

    d = pd.DataFrame(rows)
    d.to_csv(out_path, index=False)
    print(f"scored {int(d.scored.sum())} of {len(d)}  -> {out_path}")
    if (~d.scored).any():
        print("refusals:", dict(d[~d.scored].reason.value_counts()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
