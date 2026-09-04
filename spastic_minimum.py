"""What does the excursion gate's floor actually hang off, today?

MIN_INTERPRETABLE_A0_DEG = 25.0 is documented as a conservative floor set as
high as it could go while clearing THE LOWEST OBSERVED SPASTIC LEG, quoted at
28.7 deg with ~13% of margin. That 28.7 was measured on a reconstruction that
has since changed twice -- the flex-axis fix (6a327d5) and the polarity fix in
windowed_pt_params -- both of which moved A0.

If 28.7 has moved, every option on the table moves with it, including leaving
25.0 exactly where it is.

Optical only, deliberately: the reference population behind the gate is 53
non-spastic OPTITRACK trials, and the 100 Hz analysis-grid change affects IMU
reconstruction, not OptiTrack. Mixing modalities here is the error that made
the first attempt at this wrong.

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

CACHE = os.path.join(BASE, "Model_Analysis_Outputs", "_cache", "spastic_minimum.json")
LABELS = os.path.join(BASE, "Model_Analysis_Outputs", "Spasticity_Stratified", "leg_labels.csv")


def load_labels():
    """(pid, leg) -> (spasticity, source)."""
    out = {}
    with open(LABELS, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            out[(str(r["participant"]).strip(), r["leg"].strip().lower())] = (
                r["spasticity"].strip(), r["source"].strip())
    return out


def key_to_leg(trial_key):
    """'15_left_pre_T2' -> ('15', 'left'). Returns None when it does not parse."""
    parts = str(trial_key).split("_")
    if len(parts) < 2:
        return None
    pid, leg = parts[0], parts[1].lower()
    return (pid, leg) if leg in ("left", "right") else None


def main():
    import numpy as np
    import batch_imu_vs_optitrack_rmse as batch
    import workbench_engine as engine
    import pendulastic_pt_score as pts

    labels = load_labels()
    print(f"labels: {len(labels)} legs", flush=True)

    trials = [t for t in batch.discover_trials() if t.get("optitrack_path")]
    print(f"optical trials discovered: {len(trials)}", flush=True)

    try:
        rows = json.load(open(CACHE, encoding="utf-8"))
    except (OSError, ValueError):
        rows = {}

    for i, t in enumerate(trials):
        key = t["trial_key"]
        if key in rows:
            continue
        try:
            got = engine.load_optitrack_trial_detailed(t["optitrack_path"])
            tt, ang = got[0], got[1]
            p = pts.compute_pt_params(np.asarray(tt, float), np.asarray(ang, float))
            a0 = p.get("A0_deg") if p else None
            rows[key] = float(a0) if a0 is not None and math.isfinite(a0) else None
        except Exception as e:
            rows[key] = {"error": f"{type(e).__name__}: {e}"[:100]}
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
        os.replace(tmp, CACHE)
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(trials)}", flush=True)

    by_group = {}
    unlabelled = 0
    for key, a0 in rows.items():
        if not isinstance(a0, float):
            continue
        leg = key_to_leg(key)
        if leg is None or leg not in labels:
            unlabelled += 1
            continue
        spas, src = labels[leg]
        by_group.setdefault(spas, []).append((a0, key, src))

    print(f"\nscored optical trials: "
          f"{sum(len(v) for v in by_group.values())} "
          f"({unlabelled} could not be joined to a leg label)")

    for grp in sorted(by_group):
        vals = sorted(by_group[grp])
        a = np.array([v[0] for v in vals])
        print(f"\n{grp}  n={len(a)}")
        print(f"  min {a.min():6.2f}   p5 {np.percentile(a,5):6.2f}   "
              f"median {np.median(a):6.2f}   max {a.max():6.2f}")
        print("  five lowest:")
        for a0, key, src in vals[:5]:
            print(f"    {a0:6.2f}  {key:28s} {src}")

    spastic = by_group.get("spastic", [])
    if spastic:
        lo = min(spastic)
        print(f"\n{'='*62}")
        print(f"LOWEST SPASTIC LEG TODAY: {lo[0]:.2f} deg  ({lo[1]}, {lo[2]})")
        print(f"  documented when the gate was set: 28.7 deg")
        print(f"  current floor 25.0 -> margin {100*(lo[0]-25.0)/lo[0]:.1f}% "
              f"(was ~13% at 28.7)")
        # Clinician-examined only: the same provenance rule that governs the
        # rest of the plan. An assumed label should not move a clinical gate.
        exam = [v for v in spastic if v[2] == "clinical-mas"]
        if exam:
            le = min(exam)
            print(f"\n  restricted to clinician-examined spastic legs (n={len(exam)}):")
            print(f"    lowest {le[0]:.2f} deg  ({le[1]})")
            print(f"    a floor clearing it by 13% would sit at {le[0]*0.87:.2f}")


if __name__ == "__main__":
    main()
