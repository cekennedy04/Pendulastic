"""Re-derive EXCURSION_REF_* under the SAME filter the originals used.

The shipped constants say:

    Over 53 non-spastic OptiTrack trials passing the quality filter (coverage
    >= 80%, no area-ratio warning): mean 46.6 deg, sd 11.1

An earlier pass measured 36.5 / 19.6 / n=45 without that filter, so it is not a
like-for-like replacement -- part of the difference would be the filter, not the
reconstruction. This applies the documented filter so the new numbers can
actually stand in for the old ones in the operator-facing refusal message.

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

CACHE = os.path.join(BASE, "Model_Analysis_Outputs", "_cache", "excursion_ref.json")
LABELS = os.path.join(BASE, "Model_Analysis_Outputs", "Spasticity_Stratified", "leg_labels.csv")
MIN_COVERAGE = 0.80


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

    for i, t in enumerate(trials):
        key = t["trial_key"]
        if key in rows:
            continue
        rec = {}
        try:
            got = engine.load_optitrack_trial_detailed(t["optitrack_path"])
            tt, ang, quality = got[0], got[1], (got[3] if len(got) > 3 else None)
            p = pts.compute_pt_params(np.asarray(tt, float), np.asarray(ang, float))
            a0 = p.get("A0_deg") if p else None
            rec["a0"] = float(a0) if a0 is not None and math.isfinite(a0) else None
            cov = getattr(quality, "coverage", None)
            rec["coverage"] = float(cov) if cov is not None and math.isfinite(cov) else None
            warns = getattr(quality, "warnings", ()) or ()
            rec["warnings"] = [str(w) for w in warns]
        except Exception as e:
            rec = {"error": f"{type(e).__name__}: {e}"[:100]}
        rows[key] = rec
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
        os.replace(tmp, CACHE)
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(trials)}", flush=True)

    def leg_of(key):
        p = key.split("_")
        return (p[0], p[1].lower()) if len(p) > 1 else None

    kept, dropped_cov, dropped_area, no_label = [], 0, 0, 0
    for key, rec in rows.items():
        if not isinstance(rec, dict) or "error" in rec or rec.get("a0") is None:
            continue
        lg = leg_of(key)
        if lg not in labels:
            no_label += 1
            continue
        if labels[lg][0] != "non-spastic":
            continue
        cov = rec.get("coverage")
        if cov is not None and cov < MIN_COVERAGE:
            dropped_cov += 1
            continue
        if any("area" in w.lower() for w in rec.get("warnings", [])):
            dropped_area += 1
            continue
        kept.append((rec["a0"], key))

    a = np.array([k[0] for k in kept])
    print(f"\nnon-spastic optical trials passing the documented filter:")
    print(f"  kept {len(a)}   dropped for coverage < {MIN_COVERAGE:.0%}: {dropped_cov}"
          f"   dropped for area-ratio warning: {dropped_area}")
    if not len(a):
        return
    print(f"\n  mean {a.mean():.1f}   sd {a.std(ddof=1):.1f}   n {len(a)}")
    print(f"  median {np.median(a):.1f}   min {a.min():.1f}   max {a.max():.1f}")
    print(f"\n  shipped constants: mean 46.6  sd 11.1  n 53")
    print(f"  two SD below the new mean: {a.mean() - 2*a.std(ddof=1):.1f}")
    print(f"  trials below the 25.0 floor: {(a < 25).sum()} of {len(a)}")


if __name__ == "__main__":
    main()
