"""Is 87d7645 a fix or a regression? Measured against known ground truth.

The loader bisect established WHERE the reference curve's shape changed: flat
through 6ddf649, then 8.84 deg median at 87d7645. It could not say which side
is right, because both were measured against each other rather than against
truth.

tests/test_optitrack_marker_angle.py already builds synthetic marker
trajectories with a known knee angle, and _shape_err scores a curve against
that truth after quotienting out offset and sign -- which is exactly the
comparison this needs, since the optical curve is relative and its polarity
comes from an eigenvector.

87d7645 was fixing a real defect: the old code seeded the anatomical axis from
the first 60 frames, so a trial starting at rest or mid-motion anchored
"straight" to a flexed pose. Those are the start_state cases below, and they
are where a fix should show.

Read-only, and no checkout: old code is loaded via `git show`.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile

BASE = r"C:\Users\cladi\Pendulastic"
sys.path.insert(0, BASE)
os.chdir(BASE)
sys.path.insert(0, os.path.join(BASE, "tests"))

BEFORE = "6ddf649"   # last revision before the shape moved
AFTER = "87d7645"    # where it moved


def load_rev(rev):
    raw = subprocess.run(["git", "show", f"{rev}:pendulastic_pt_score.py"],
                         cwd=BASE, capture_output=True, check=True).stdout
    d = tempfile.mkdtemp()
    path = os.path.join(d, f"pts_{rev}.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(raw.decode("utf-8", errors="replace"))
    spec = importlib.util.spec_from_file_location(f"pts_{rev}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"pts_{rev}"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    import numpy as np
    import pendulastic_pt_score as current
    import test_optitrack_marker_angle as fx

    old = load_rev(BEFORE)
    new = load_rev(AFTER)
    tmp = tempfile.mkdtemp()

    # The three start states, plus the awkward variants the suite exercises.
    cases = [
        ("held, extended and still", dict(start_state="held")),
        ("at rest, already flexed", dict(start_state="rest")),
        ("starts mid-swing", dict(start_state="mid_motion")),
        ("held, opposite flexion sign", dict(start_state="held", sign=+1.0)),
        ("rest, opposite flexion sign", dict(start_state="rest", sign=+1.0)),
        ("held, hold drifts 8 deg", dict(start_state="held", hold_drift_deg=8.0)),
        ("held, 10 deg out of plane", dict(start_state="held", out_of_plane_deg=10.0)),
        ("mid-swing, out of plane", dict(start_state="mid_motion", out_of_plane_deg=10.0)),
    ]

    print(f"Shape error against ground truth, in degrees. Lower is better.")
    print(f"'before' = {BEFORE}, 'after' = {AFTER} (where the shape moved).\n")
    print(f"{'case':34}{'before':>10}{'after':>10}{'current':>10}   verdict")

    wins = {"after": 0, "before": 0, "tie": 0}
    for name, kw in cases:
        rows, truth = fx._build_trial(**kw)
        p = fx._write_csv(os.path.join(tmp, f"{abs(hash(name))}.csv"), rows)
        errs = {}
        for label, mod in (("before", old), ("after", new), ("current", current)):
            try:
                _t, ang = mod.load_optitrack(p)
                errs[label] = fx._shape_err(np.asarray(ang, float),
                                            np.asarray(truth, float))
            except Exception as e:
                errs[label] = float("nan")
                errs[label + "_err"] = f"{type(e).__name__}"
        b, a = errs.get("before"), errs.get("after")
        if not (np.isfinite(b) and np.isfinite(a)):
            verdict = "one side failed to load"
        elif abs(b - a) < 0.5:
            verdict = "tie"; wins["tie"] += 1
        elif a < b:
            verdict = f"AFTER better by {b - a:.1f}"; wins["after"] += 1
        else:
            verdict = f"BEFORE better by {a - b:.1f}"; wins["before"] += 1
        print(f"{name:34}{b:>10.2f}{a:>10.2f}{errs.get('current', float('nan')):>10.2f}"
              f"   {verdict}")

    print(f"\nafter better on {wins['after']}, before better on {wins['before']}, "
          f"tied on {wins['tie']}")
    print("\n87d7645 was fixing the seed bug, which only bites when the trial does")
    print("NOT start held and extended. If it is a fix, that is where it shows.")


if __name__ == "__main__":
    main()
