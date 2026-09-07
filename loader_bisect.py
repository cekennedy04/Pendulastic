"""Did load_optitrack's curve SHAPE change after 2026-08-28, and where?

imu_vs_optitrack_rmse.csv (2026-08-28) has the IMU at 6.05 deg offset-removed
over 101 trials. The same loader and method measures 21.88 today -- ~3x worse.
A parallel session ruled out, by direct measurement: reference corruption (3% of
trials, excluding them changes nothing), the replay path (identical to 0.00 deg),
participant selection, the datum (both figures are offset-removed), and the
analysis grid (18.78 vs 18.75).

What is left is the reference curve's SHAPE. This diffs the loader across the
candidate revisions to find where it moved.

Uses `git show <rev>:file` into a temp module rather than checking anything out
-- three sessions are working in this tree and a checkout would disrupt them.
Read-only.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile

BASE = r"C:\Users\cladi\Pendulastic"
sys.path.insert(0, BASE)
os.chdir(BASE)

# Loader revisions between the stored table (2026-08-28) and today, oldest
# first. 3b44f4d is the prime suspect on its subject line alone: "measure the
# knee about a joint centre found from the swing".
REVS = [
    ("0da21d4", "2026-08-28 baseline, what the stored table used"),
    ("3b44f4d", "joint centre found from the swing"),
    ("6ddf649", "detect a reference window that is not a hold"),
    ("87d7645", "relative knee angle, never invent a zero"),
    ("742e804", "restore the P21 tripwire"),
    ("46e05bb", "show how marginal an ill-conditioned trial is"),
]
N_TRIALS = 8


def load_rev(rev):
    """pendulastic_pt_score as of `rev`, without touching the working tree."""
    # Bytes, not text: the file contains non-cp1252 characters (degree signs and
    # the like) and Windows' default decode chokes on them mid-stream.
    raw = subprocess.run(["git", "show", f"{rev}:pendulastic_pt_score.py"],
                         cwd=BASE, capture_output=True, check=True).stdout
    src = raw.decode("utf-8", errors="replace")
    d = tempfile.mkdtemp()
    path = os.path.join(d, f"pts_{rev}.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    spec = importlib.util.spec_from_file_location(f"pts_{rev}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"pts_{rev}"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    import numpy as np
    import batch_imu_vs_optitrack_rmse as batch

    trials = [t for t in batch.discover_trials() if t.get("optitrack_path")][:N_TRIALS]
    print(f"trials: {len(trials)}\n", flush=True)

    curves = {}
    for rev, note in REVS:
        try:
            mod = load_rev(rev)
        except Exception as e:
            print(f"{rev}  LOAD FAILED  {type(e).__name__}: {str(e)[:70]}")
            continue
        got = []
        for t in trials:
            try:
                _tt, ang = mod.load_optitrack(t["optitrack_path"])
                a = np.asarray(ang, float)
                got.append(a[np.isfinite(a)])
            except Exception:
                got.append(None)
        curves[rev] = got
        ok = [g for g in got if g is not None and len(g)]
        spans = [float(g.max() - g.min()) for g in ok]
        print(f"{rev}  {note}")
        print(f"    loaded {len(ok)}/{len(trials)}   "
              f"median span {np.median(spans):6.1f}" if spans else "    none", flush=True)

    print("\nCurve-to-curve difference against the 2026-08-28 baseline.")
    print("Offset removed before comparing, so this is SHAPE only:\n")
    base = curves.get("0da21d4")
    if not base:
        print("  baseline did not load; cannot compare")
        return
    print(f"{'rev':10}{'note':44}{'median shape RMSE':>18}{'n':>5}")
    for rev, note in REVS:
        if rev not in curves:
            continue
        ds = []
        for b, c in zip(base, curves[rev]):
            if b is None or c is None or not len(b) or not len(c):
                continue
            n = min(len(b), len(c))
            if n < 10:
                continue
            d = c[:n] - b[:n]
            ds.append(float(np.sqrt(np.mean((d - d.mean()) ** 2))))
        med = np.median(ds) if ds else float("nan")
        print(f"{rev:10}{note[:42]:44}{med:>18.2f}{len(ds):>5}")

    print("\nA jump between consecutive rows is where the shape moved.")


if __name__ == "__main__":
    main()
