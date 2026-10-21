"""THROWAWAY PROBE -- not production code.

Two questions in one pass over the optical corpus (the loader costs ~2.7 s per
trial, so they share it):

1. How noisy is the OPTICAL path? The noise-aware `min_amp` guard added to
   `compute_pt_params` engages at 3x the settled-tail SD. On the phone that SD
   runs 0.09-0.38 deg and the guard is inert. Optical trials carry marker
   dropout, so this measures whether the guard actually engages there -- and
   whether the path sits near the 2.5 deg regime where counting broke.

2. What should HEALTHY_REF["N"] be now? The old 3.5 was the 4 s cap itself.
   This recomputes control-arm N under the new 10 s window.

Writes Model_Analysis_Outputs/status_figures/optical_noise_and_N.csv.
"""
from __future__ import annotations
import glob, os, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
import pendulastic_pt_score as pt

OUT = "Model_Analysis_Outputs/status_figures/optical_noise_and_N.csv"
DANGER_SD, CAUTION_SD = 2.5, 1.0


def tail_sd(a):
    a = np.asarray(a, float); fin = a[np.isfinite(a)]
    if fin.size < 100: return None
    tail = fin[int(0.85 * fin.size):]
    if tail.size < 30: return None
    x = np.arange(tail.size, dtype=float)
    try:
        s, i = np.polyfit(x, tail, 1); r = tail - (s * x + i)
    except Exception:
        r = tail - np.median(tail)
    return float(np.std(r))


def main():
    files = sorted(glob.glob("OptiTrack_Recordings/**/*_optitrack.csv", recursive=True))
    print(f"scanning {len(files)} optical trials", flush=True)
    rows = []
    for k, f in enumerate(files):
        parts = os.path.normpath(f).split(os.sep)
        folder = parts[1] if len(parts) > 1 else "?"
        arm = folder.split("_")[0]
        try:
            t, a = pt.load_optitrack(f)
        except Exception:
            continue
        if a is None or len(a) < 100:
            continue
        sd = tail_sd(a)
        try:
            p = pt.compute_pt_params(np.asarray(t, float), np.asarray(a, float))
        except Exception:
            p = None
        rows.append({"file": f, "arm": arm, "folder": folder, "tail_sd": sd,
                     "nan_frac": float(1 - np.isfinite(np.asarray(a, float)).mean()),
                     "N": (p or {}).get("N"), "f": (p or {}).get("f"),
                     "A0": (p or {}).get("A0_deg")})
        if (k + 1) % 40 == 0:
            print(f"  ...{k+1}/{len(files)}", flush=True)
    d = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    d.to_csv(OUT, index=False)
    print(f"\nscored {len(d)} trials -> {OUT}\n", flush=True)

    sd = d.tail_sd.dropna()
    print("=== settled-tail noise, OPTICAL path (deg) ===")
    for q in (10, 50, 75, 90, 95, 99):
        print(f"  p{q:<3d} {np.percentile(sd, q):8.3f}")
    print(f"  max  {sd.max():8.3f}")
    print("  phone IMU, same metric: 0.09 - 0.38 deg\n")
    print(f"  sd >= {CAUTION_SD} deg: {(sd>=CAUTION_SD).sum():4d}/{len(sd)} ({(sd>=CAUTION_SD).mean()*100:.1f}%)")
    print(f"  sd >= {DANGER_SD} deg: {(sd>=DANGER_SD).sum():4d}/{len(sd)} ({(sd>=DANGER_SD).mean()*100:.1f}%)")
    print(f"\n  guard engages (3*sd > 0.05*A0) on: "
          f"{((3*d.tail_sd) > (0.05*d.A0)).sum()}/{d.A0.notna().sum()} trials\n", flush=True)

    print("=== N by arm under the NEW 10 s window ===")
    for arm, g in d.groupby("arm"):
        if g.N.notna().sum() < 3: continue
        print(f"  {arm:10s} n={g.N.notna().sum():3d}  median N={g.N.median():5.2f}  "
              f"IQR {g.N.quantile(.25):.2f}-{g.N.quantile(.75):.2f}")
    ctrl = d[d.arm == "Control"].N.dropna()
    if len(ctrl):
        print(f"\n  ==> HEALTHY_REF['N'] candidate (control median) = {ctrl.median():.2f}"
              f"   (was 3.5 = the old cap)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
