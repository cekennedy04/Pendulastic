"""THROWAWAY PROBE -- not production code.

Which scoring rate is most accurate: the native stream, or the 50 ms tick grid
the app decimates to first?

`resample.rs` justifies the decimation by saying `compute_pt_params` expresses
its Savitzky-Golay windows and minimum peak separations in SAMPLES, so feeding
it a 100 Hz series would rescale every window. That is no longer true: the
reference's smoothing is a 0.10 s duration (`_SG_WINDOW_S`) and its peak
separation is `max(3, int(fps_eff / 3.5))`, both rate-aware. The remaining
sample-count constants are floors paired with duration checks, so a higher
rate satisfies them more easily, not less.

Synthetic ground truth already says native wins on N (exact at 100 Hz, half a
cycle low at 20 Hz). Real trials have no ground-truth N, so this uses the
measurable proxy: WITHIN-SESSION REPEATABILITY. The same leg swung five times
in one sitting should score the same each time; whichever rate produces less
spread is the better measurement.

Run:  ~/miniconda3/python.exe probe_native_vs_tick.py
"""
from __future__ import annotations

import glob
import os
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

import pendulastic_pt_score as pt

TICK_S = 0.05
PARAMS = ["N", "R2n", "phi_max_ratio", "omega_max_n", "f", "area_ratio"]


def tick_resample(t, v, tick_s=TICK_S):
    """Zero-order hold onto a fixed grid; tick 0 is NaN by contract.

    Mirrors `mobile-imu-core/src/resample.rs::tick_resample`.
    """
    t = np.asarray(t, float)
    v = np.asarray(v, float)
    if t.size == 0:
        return t, v
    n_ticks = max(1, int((t[-1] - t[0]) / tick_s) + 1)
    ticks = t[0] + np.arange(n_ticks) * tick_s
    held = np.full(n_ticks, np.nan)
    idx = np.searchsorted(t, ticks, side="left")
    for i in range(1, n_ticks):
        j = idx[i]
        if j > 0:
            held[i] = v[j - 1]
    return ticks - t[0], held


def main() -> int:
    rows = []
    files = sorted(glob.glob("Recordings/**/*_imu.csv", recursive=True))
    for f in files:
        parts = os.path.normpath(f).split(os.sep)
        try:
            i = parts.index("Recordings")
            grp = (parts[i + 1], parts[i + 2], parts[i + 3])
        except (ValueError, IndexError):
            continue
        try:
            d = pd.read_csv(f, comment="#")
        except Exception:
            continue
        if "prox_pitch" not in d or "phone_ts_ms" not in d or len(d) < 200:
            continue
        t = d.phone_ts_ms.to_numpy(float) / 1000.0
        t -= t[0]
        a = d.prox_pitch.to_numpy(float)
        ok = np.isfinite(t) & np.isfinite(a)
        t, a = t[ok], a[ok]
        if t.size < 200:
            continue

        rec = {"grp": "/".join(grp), "arm": grp[0].split("_")[0]}
        try:
            pn = pt.compute_pt_params(t, a)
        except Exception:
            pn = None
        tt, at = tick_resample(t, a)
        fin = np.isfinite(at)
        try:
            pk = pt.compute_pt_params(tt[fin], at[fin]) if fin.sum() > 30 else None
        except Exception:
            pk = None
        if pn is None or pk is None:
            continue
        for k in PARAMS:
            rec[f"native_{k}"] = pn.get(k)
            rec[f"tick_{k}"] = pk.get(k)
        rec["native_A0"] = pn.get("A0_deg")
        rec["tick_A0"] = pk.get("A0_deg")
        rows.append(rec)

    d = pd.DataFrame(rows)
    print(f"scored {len(d)} trials both ways "
          f"({d.grp.nunique()} participant/leg/session groups)\n")

    grp_n = d.groupby("grp").size()
    keep = set(grp_n[grp_n >= 3].index)
    r = d[d.grp.isin(keep)]
    print(f"repeatability computed on {len(r)} trials in "
          f"{r.grp.nunique()} groups with >=3 repeats\n")

    def within_cv(frame, col):
        """Median within-group coefficient of variation (spread / level)."""
        out = []
        for _, g in frame.groupby("grp"):
            v = g[col].dropip if False else g[col].dropna()
            if len(v) < 3:
                continue
            m = np.abs(np.mean(v))
            if m > 1e-9:
                out.append(float(np.std(v, ddof=1) / m))
        return float(np.median(out)) if out else float("nan")

    print("=== within-session spread (lower = more repeatable = better) ===")
    print(f"{'parameter':16s} {'native':>9s} {'20 Hz tick':>11s} {'winner':>9s}")
    wins = {"native": 0, "tick": 0}
    for k in PARAMS + ["A0"]:
        cn = within_cv(r, f"native_{k}")
        ct = within_cv(r, f"tick_{k}")
        if not (np.isfinite(cn) and np.isfinite(ct)):
            continue
        w = "native" if cn < ct else "tick"
        wins[w] += 1
        print(f"{k:16s} {cn:9.4f} {ct:11.4f} {w:>9s}")
    print(f"\n  parameters more repeatable at native rate: "
          f"{wins['native']} of {wins['native'] + wins['tick']}")

    print("\n=== level shift: what decimation does to each parameter ===")
    print(f"{'parameter':16s} {'native med':>11s} {'tick med':>9s} {'tick/native':>12s}")
    for k in PARAMS + ["A0"]:
        a_, b_ = d[f"native_{k}"].median(), d[f"tick_{k}"].median()
        if not (np.isfinite(a_) and np.isfinite(b_)) or abs(a_) < 1e-9:
            continue
        print(f"{k:16s} {a_:11.3f} {b_:9.3f} {b_ / a_:12.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
