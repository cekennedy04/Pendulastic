"""Re-measure the optical corpus on the SCORER's own loader.

The previous pass used workbench_engine.load_optitrack_trial_detailed, which
tries analysis_pipeline._optitrack_knee_angle_series first (rigid_body) and
only falls back to pendulastic_pt_score.load_optitrack_detailed (marker_pca).
The gate's derivation corpus came from the scorer's path. So the earlier
numbers mixed two reconstructions and are not comparable to the constants.

This computes A0 both ways per trial and records which path each took, so the
question "does it matter" gets an answer rather than an assumption.

Read-only.
"""
import csv
import json
import math
import os
import sys

BASE = r"C:\Users\cladi\Pendulastic"
sys.path.insert(0, BASE)
os.chdir(BASE)

CACHE = os.path.join(BASE, "Model_Analysis_Outputs", "_cache", "optical_recheck.json")
LABELS = os.path.join(BASE, "Model_Analysis_Outputs", "Spasticity_Stratified", "leg_labels.csv")


def main():
    import numpy as np
    import batch_imu_vs_optitrack_rmse as batch
    import workbench_engine as engine
    import pendulastic_pt_score as pts

    labels = {}
    with open(LABELS, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            labels[(r["participant"].strip(), r["leg"].strip().lower())] = (
                r["spasticity"].strip(), r["source"].strip())

    trials = [t for t in batch.discover_trials() if t.get("optitrack_path")]
    try:
        rows = json.load(open(CACHE, encoding="utf-8"))
    except (OSError, ValueError):
        rows = {}

    def a0_of(t, ang):
        p = pts.compute_pt_params(np.asarray(t, float), np.asarray(ang, float))
        if not p:
            return None
        v = p.get("A0_deg")
        return float(v) if v is not None and math.isfinite(v) else None

    for i, tr in enumerate(trials):
        key = tr["trial_key"]
        if key in rows:
            continue
        rec = {}
        path = tr["optitrack_path"]
        # What the workbench pass used.
        try:
            g = engine.load_optitrack_trial_detailed(path)
            rec["method"] = g[2]
            rec["a0_workbench"] = a0_of(g[0], g[1])
            q = g[3] if len(g) > 3 else None
            cov = getattr(q, "coverage", None)
            rec["coverage"] = float(cov) if cov is not None and math.isfinite(cov) else None
            rec["warnings"] = [str(w) for w in (getattr(q, "warnings", ()) or ())]
        except Exception as e:
            rec["err_wb"] = f"{type(e).__name__}"[:40]
        # What the SCORER uses.
        try:
            t2, a2, q2 = pts.load_optitrack_detailed(path)
            rec["a0_scorer"] = a0_of(t2, a2)
            if rec.get("coverage") is None:
                c2 = getattr(q2, "coverage", None)
                rec["coverage"] = float(c2) if c2 is not None and math.isfinite(c2) else None
                rec["warnings"] = [str(w) for w in (getattr(q2, "warnings", ()) or ())]
        except Exception as e:
            rec["err_sc"] = f"{type(e).__name__}"[:40]
        rows[key] = rec
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
        os.replace(tmp, CACHE)
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(trials)}", flush=True)

    meth = {}
    for r in rows.values():
        meth[r.get("method", "?")] = meth.get(r.get("method", "?"), 0) + 1
    print(f"\nreconstruction taken by the workbench loader: {meth}")

    def leg_of(k):
        p = k.split("_")
        return (p[0], p[1].lower()) if len(p) > 1 else None

    for which in ("a0_scorer", "a0_workbench"):
        sp, ns = [], []
        for k, r in rows.items():
            v = r.get(which)
            if not isinstance(v, float):
                continue
            lg = leg_of(k)
            if lg not in labels:
                continue
            (sp if labels[lg][0] == "spastic" else
             ns if labels[lg][0] == "non-spastic" else []).append((v, k, labels[lg][1]))
        print(f"\n--- {which}")
        if sp:
            lo = min(sp)
            a = np.array([x[0] for x in sp])
            print(f"  spastic n={len(a)}  LOWEST {lo[0]:.2f} ({lo[1]}, {lo[2]})"
                  f"   below 25: {(a<25).sum()}")
        if ns:
            a = np.array([x[0] for x in ns])
            print(f"  non-spastic n={len(a)}  mean {a.mean():.1f} sd {a.std(ddof=1):.1f}")
            # under the filter the shipped constants name
            keep = [x[0] for x in ns
                    if (rows[x[1]].get("coverage") is None or rows[x[1]]["coverage"] >= 0.80)
                    and not any("area" in w.lower() for w in rows[x[1]].get("warnings", []))]
            if keep:
                b = np.array(keep)
                print(f"    filtered (cov>=80%, no area warning): "
                      f"mean {b.mean():.1f} sd {b.std(ddof=1):.1f} n {len(b)}")

    both = [(r["a0_scorer"], r["a0_workbench"]) for r in rows.values()
            if isinstance(r.get("a0_scorer"), float)
            and isinstance(r.get("a0_workbench"), float)]
    if both:
        d = np.array([abs(s - w) for s, w in both])
        print(f"\npaired both ways: {len(both)} trials, "
              f"identical on {(d < 1e-9).sum()}, median |diff| {np.median(d):.3f} deg, "
              f"max {d.max():.2f}")


if __name__ == "__main__":
    main()
