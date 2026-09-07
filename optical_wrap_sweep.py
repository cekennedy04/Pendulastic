"""How many optical reference curves contain a discontinuity?

A parallel session found CHAT_19 left T1 spanning 172.7 deg -- not a knee
swing. Inspecting it: 31 samples (2.4%) sit below -100 deg in NON-contiguous
bursts between 2.12s and 3.31s, while the median is -10.9. That is bimodal,
which is the signature of a sign flip or an angle wrap rather than motion.

If the reference curve is corrupt on a subset of trials, every accuracy number
computed against it inherits that. This measures prevalence.

A wrap shows up as a large single-sample jump: real knee motion at ~120 Hz
cannot move 100 deg between consecutive samples. Read-only.
"""
import json
import math
import os
import sys

BASE = r"C:\Users\cladi\Pendulastic"
sys.path.insert(0, BASE)
os.chdir(BASE)

CACHE = os.path.join(BASE, "Model_Analysis_Outputs", "_cache", "optical_wrap.json")
JUMP_DEG = 60.0     # a knee cannot move this far between consecutive samples
PLAUSIBLE_SPAN = 150.0


def main():
    import numpy as np
    import batch_imu_vs_optitrack_rmse as batch
    import pendulastic_pt_score as pts

    trials = [t for t in batch.discover_trials() if t.get("optitrack_path")]
    try:
        rows = json.load(open(CACHE, encoding="utf-8"))
    except (OSError, ValueError):
        rows = {}

    for i, t in enumerate(trials):
        key = t["trial_key"]
        if key in rows:
            continue
        try:
            tt, ang = pts.load_optitrack(t["optitrack_path"])
            a = np.asarray(ang, float)
            f = np.isfinite(a)
            a = a[f]
            if len(a) < 10:
                rows[key] = None
            else:
                d = np.abs(np.diff(a))
                rows[key] = {
                    "span": float(a.max() - a.min()),
                    "median": float(np.median(a)),
                    "max_jump": float(d.max()),
                    "n_jumps": int((d > JUMP_DEG).sum()),
                    "frac_far": float(np.mean(np.abs(a - np.median(a)) > 90.0)),
                }
        except Exception as e:
            rows[key] = {"error": f"{type(e).__name__}"[:40]}
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
        os.replace(tmp, CACHE)
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(trials)}", flush=True)

    ok = {k: v for k, v in rows.items() if v and "error" not in v}
    print(f"\noptical trials measured: {len(ok)}")
    if not ok:
        return

    spans = np.array([v["span"] for v in ok.values()])
    jumps = np.array([v["n_jumps"] for v in ok.values()])
    print(f"\nspan (deg): median {np.median(spans):.1f}   "
          f"p90 {np.percentile(spans, 90):.1f}   max {spans.max():.1f}")
    print(f"  implausible (> {PLAUSIBLE_SPAN:.0f} deg): "
          f"{(spans > PLAUSIBLE_SPAN).sum()} of {len(spans)} "
          f"({100 * (spans > PLAUSIBLE_SPAN).mean():.0f}%)")
    print(f"\ncurves containing a jump > {JUMP_DEG:.0f} deg between consecutive "
          f"samples: {(jumps > 0).sum()} of {len(jumps)} "
          f"({100 * (jumps > 0).mean():.0f}%)")
    print(f"  of those, median jump count: "
          f"{np.median(jumps[jumps > 0]) if (jumps > 0).any() else 0:.0f}")

    bad = sorted(((v["span"], k, v) for k, v in ok.items()), reverse=True)[:8]
    print("\nworst spans:")
    for span, k, v in bad:
        print(f"  {span:7.1f}  {k:30s} jumps {v['n_jumps']:3d}  "
              f"max_jump {v['max_jump']:6.1f}  far {100*v['frac_far']:4.1f}%")

    clean = [k for k, v in ok.items() if v["n_jumps"] == 0]
    print(f"\ncurves with NO large jump: {len(clean)} of {len(ok)}")
    if clean:
        cs = np.array([ok[k]["span"] for k in clean])
        print(f"  their spans: median {np.median(cs):.1f}  max {cs.max():.1f}"
              f"   <- what a clean swing actually looks like")


if __name__ == "__main__":
    main()
