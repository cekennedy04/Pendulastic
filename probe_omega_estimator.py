"""THROWAWAY PROBE -- not production code.

Can angular velocity be BOTH unbiased and repeatable at the native rate?

At native rate omega_max_n is 39% less repeatable than on the 20 Hz tick grid
(CV 0.286 vs 0.175), which is the only thing arguing for keeping the
decimation. But the tick grid is precise because it is biased: it attenuates
the true peak (7.7% low) and the project measured that decimating the 120 Hz
optical reference to 20 Hz reproduces 87% of the IMU-vs-optical omega gap.

The current estimator is `_sg(np.gradient(phi, t_r), ...)` -- a finite
difference, THEN a smoother. That differentiates the noise and then tries to
clean it up. A Savitzky-Golay analytic derivative fits a local polynomial to
phi and differentiates it exactly, which is the standard fix and should give
lower variance at the same effective window without extra attenuation.

Sweeps both estimators over window and polyorder, scoring each on:
  - within-session repeatability (CV, lower better)
  - median omega_max_n level (attenuation, higher = less biased)

Targets to beat: tick CV 0.175 @ level 6.463; native-current CV 0.286 @ 7.005.

Run:  ~/miniconda3/python.exe probe_omega_estimator.py
"""
from __future__ import annotations

import glob
import os
import warnings

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter

warnings.filterwarnings("ignore")

import pendulastic_pt_score as pt

TICK_CV, TICK_LEVEL = 0.1751, 6.463
CUR_CV, CUR_LEVEL = 0.2861, 7.005


def _win(dt, win_s, p, n):
    """The reference's own window-sizing rule, in samples."""
    w = int(round(win_s / dt))
    if w % 2 == 0:
        w += 1
    if w < p + 2:
        w = p + 2 if (p + 2) % 2 == 1 else p + 3
    w = min(w, n - 1 if n % 2 == 0 else n)
    return w if w % 2 == 1 else w - 1


def omega_grad_then_smooth(phi, t_r, win_s, p):
    dt = float(np.median(np.diff(t_r)))
    g = np.gradient(phi, t_r)
    w = _win(dt, win_s, p, len(g))
    return savgol_filter(g, w, p) if w >= p + 2 else g


def omega_sg_derivative(phi, t_r, win_s, p):
    dt = float(np.median(np.diff(t_r)))
    w = _win(dt, win_s, p, len(phi))
    if w < p + 2:
        return np.gradient(phi, t_r)
    return savgol_filter(phi, w, p, deriv=1, delta=dt)


def main() -> int:
    trials = []
    for f in sorted(glob.glob("Recordings/**/*_imu.csv", recursive=True)):
        parts = os.path.normpath(f).split(os.sep)
        try:
            i = parts.index("Recordings")
            grp = "/".join(parts[i + 1:i + 4])
        except (ValueError, IndexError):
            continue
        try:
            d = pd.read_csv(f, comment="#")
        except Exception:
            continue
        if "prox_pitch" not in d or len(d) < 200:
            continue
        t = d.phone_ts_ms.to_numpy(float) / 1000.0
        t -= t[0]
        a = d.prox_pitch.to_numpy(float)
        ok = np.isfinite(t) & np.isfinite(a)
        if ok.sum() < 200:
            continue
        try:
            p = pt.compute_pt_params(t[ok], a[ok])
        except Exception:
            p = None
        if not p or not p.get("A0_deg"):
            continue
        trials.append((grp, np.asarray(p["phi"], float),
                       np.asarray(p["t_r"], float), float(p["A0_deg"])))
    print(f"{len(trials)} trials with usable params\n")

    def evaluate(fn, win_s, p):
        rows = []
        for grp, phi, t_r, a0 in trials:
            try:
                om = np.abs(fn(phi, t_r, win_s, p))
                rows.append({"grp": grp, "v": float(np.nanmax(om)) / a0})
            except Exception:
                continue
        d = pd.DataFrame(rows)
        g = d.groupby("grp").size()
        d = d[d.grp.isin(g[g >= 3].index)]
        cvs = []
        for _, gg in d.groupby("grp"):
            v = gg.v.dropna()
            if len(v) >= 3 and abs(v.mean()) > 1e-9:
                cvs.append(v.std(ddof=1) / abs(v.mean()))
        return (float(np.median(cvs)) if cvs else np.nan,
                float(d.v.median()) if len(d) else np.nan)

    print(f"{'estimator':22s} {'win_s':>6s} {'p':>2s} {'CV':>8s} {'level':>8s} "
          f"{'beats tick CV':>14s} {'unbiased':>9s}")
    best = []
    for name, fn in (("gradient+smooth", omega_grad_then_smooth),
                     ("SG derivative", omega_sg_derivative)):
        for p in (2, 3):
            for win_s in (0.10, 0.15, 0.20, 0.25, 0.30):
                cv, lvl = evaluate(fn, win_s, p)
                if not np.isfinite(cv):
                    continue
                ok_cv = cv <= TICK_CV
                ok_lvl = lvl >= TICK_LEVEL
                best.append((cv, lvl, name, win_s, p, ok_cv and ok_lvl))
                print(f"{name:22s} {win_s:6.2f} {p:2d} {cv:8.4f} {lvl:8.3f} "
                      f"{'YES' if ok_cv else 'no':>14s} {'YES' if ok_lvl else 'no':>9s}")
        print()

    print(f"reference: tick CV {TICK_CV:.4f} level {TICK_LEVEL:.3f} | "
          f"native-current CV {CUR_CV:.4f} level {CUR_LEVEL:.3f}\n")
    wins = [b for b in best if b[5]]
    if wins:
        wins.sort()
        cv, lvl, name, win_s, p, _ = wins[0]
        print(f"BEST BOTH-WAYS: {name} win_s={win_s} p={p} -> "
              f"CV {cv:.4f} (tick {TICK_CV:.4f}), level {lvl:.3f} (tick {TICK_LEVEL:.3f})")
    else:
        best.sort()
        cv, lvl, name, win_s, p, _ = best[0]
        print(f"none beat tick on BOTH. Lowest CV: {name} win_s={win_s} p={p} "
              f"-> CV {cv:.4f}, level {lvl:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
