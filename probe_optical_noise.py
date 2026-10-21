"""THROWAWAY PROBE -- not production code.

The open question blocking the `_ACTIVE_WINDOW_CAP_SEC` change: how noisy is
the OPTICAL path?

`probe_settle_window.py` showed both candidate window rules stay honest at
IMU-like noise (0.3-1.0 deg) but blow up at 2.5 deg -- N reads 8-12 on a
no-rebound trial whose true count is 0. The phone's settled-tail sd is
0.09-0.38 deg, comfortably safe. But the optical path carries marker dropout,
and `cohort_stats.csv` -- the basis for the whole direction analysis -- is
built from it.

Measures, per OptiTrack trial: settled-tail sd (same metric used on the phone),
NaN fraction, and how many trials sit in the regime where the window rules
fail.

Run:  ~/miniconda3/python.exe probe_optical_noise.py
"""
from __future__ import annotations

import glob
import warnings

import numpy as np

warnings.filterwarnings("ignore")

import pendulastic_pt_score as pt

DANGER_SD = 2.5     # where both window rules broke in probe_settle_window
CAUTION_SD = 1.0    # highest sd still clean in that probe


def tail_sd(t, a):
    """SD of the settled tail -- the quantity that drove the failure."""
    ok = np.isfinite(a)
    if ok.sum() < 100:
        return None, None
    a2 = a[ok]
    n = len(a2)
    tail = a2[int(0.85 * n):]
    if len(tail) < 30:
        return None, None
    # detrend the tail so slow drift is not counted as noise
    x = np.arange(len(tail), dtype=float)
    try:
        slope, icpt = np.polyfit(x, tail, 1)
        resid = tail - (slope * x + icpt)
    except Exception:
        resid = tail - np.median(tail)
    return float(np.std(resid)), float(1.0 - ok.mean())


def main() -> int:
    files = sorted(glob.glob("OptiTrack_Recordings/**/*_optitrack.csv",
                             recursive=True))
    print(f"scanning {len(files)} OptiTrack trials\n")
    sds, nans, failed = [], [], 0
    for f in files:
        try:
            t, a = pt.load_optitrack(f)
        except Exception:
            failed += 1
            continue
        if t is None or a is None or len(a) < 100:
            failed += 1
            continue
        sd, nanfrac = tail_sd(t, np.asarray(a, dtype=float))
        if sd is None:
            failed += 1
            continue
        sds.append(sd)
        nans.append(nanfrac)

    sds = np.array(sds)
    nans = np.array(nans)
    print(f"scored {len(sds)} trials ({failed} unreadable/too short)\n")
    if not len(sds):
        return 1

    print("=== settled-tail noise, OPTICAL path (deg) ===")
    for q in (10, 25, 50, 75, 90, 95, 99):
        print(f"  p{q:<3d} {np.percentile(sds, q):8.3f}")
    print(f"  max  {sds.max():8.3f}")
    print()
    print("  phone IMU, same metric, for comparison: 0.09 - 0.38 deg")
    print()
    print("=== how many trials sit in the regime where the window rules fail? ===")
    print(f"  sd >= {CAUTION_SD} deg (caution): "
          f"{(sds >= CAUTION_SD).sum():4d} / {len(sds)} "
          f"({(sds >= CAUTION_SD).mean() * 100:.1f}%)")
    print(f"  sd >= {DANGER_SD} deg (both rules broke): "
          f"{(sds >= DANGER_SD).sum():4d} / {len(sds)} "
          f"({(sds >= DANGER_SD).mean() * 100:.1f}%)")
    print()
    print("=== marker dropout (NaN fraction) ===")
    for q in (50, 75, 90, 95):
        print(f"  p{q:<3d} {np.percentile(nans, q) * 100:6.1f}%")
    print(f"  trials >20% dropout: {(nans > 0.20).sum()} / {len(nans)}")
    print()
    if len(sds) > 5:
        c = np.corrcoef(nans, sds)[0, 1]
        print(f"=== does dropout predict tail noise?  corr = {c:+.3f} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
