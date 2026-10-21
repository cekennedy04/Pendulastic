"""THROWAWAY PROBE -- not production code.

Which angular-velocity estimator is ACCURATE, not merely consistent?

Real trials cannot answer this: omega_max_n is a `nanmax(|omega|)`, and a
maximum is biased UPWARD by noise, so the estimator that reports the highest
peak may simply be the one that smooths least. Repeatability cannot separate
those either -- a consistently attenuated estimate is consistently wrong.

So this uses a signal whose true peak angular velocity is known in closed
form. For phi(t) = A0 * exp(-z*w*t) * cos(w*t), with w = 2*pi*f:

    dphi/dt = -A0 * exp(-z*w*t) * w * (z*cos(w*t) + sin(w*t))

evaluated on a dense grid to give the true peak |dphi/dt|. The same signal is
then sampled at 100 Hz with realistic sensor noise and passed through each
candidate estimator, including the 20 Hz tick path.

Run:  ~/miniconda3/python.exe probe_omega_truth.py
"""
from __future__ import annotations

import warnings

import numpy as np
from scipy.signal import savgol_filter

warnings.filterwarnings("ignore")

A0, FREQ, ZETA = 47.0, 0.9, 0.06
TICK_S = 0.05


def phi_of(t):
    w = 2 * np.pi * FREQ
    return A0 * np.exp(-ZETA * w * t) * np.cos(w * t)


def true_peak_omega():
    w = 2 * np.pi * FREQ
    t = np.arange(0, 12, 1e-5)
    d = -A0 * np.exp(-ZETA * w * t) * w * (ZETA * np.cos(w * t) + np.sin(w * t))
    return float(np.max(np.abs(d)))


def _win(dt, win_s, p, n):
    w = int(round(win_s / dt))
    if w % 2 == 0:
        w += 1
    if w < p + 2:
        w = p + 2 if (p + 2) % 2 == 1 else p + 3
    w = min(w, n - 1 if n % 2 == 0 else n)
    return w if w % 2 == 1 else w - 1


def est_grad(phi, t, win_s, p):
    dt = float(np.median(np.diff(t)))
    g = np.gradient(phi, t)
    w = _win(dt, win_s, p, len(g))
    return savgol_filter(g, w, p) if w >= p + 2 else g


def est_sgderiv(phi, t, win_s, p):
    dt = float(np.median(np.diff(t)))
    w = _win(dt, win_s, p, len(phi))
    if w < p + 2:
        return np.gradient(phi, t)
    return savgol_filter(phi, w, p, deriv=1, delta=dt)


def tick_resample(t, v):
    n = max(1, int((t[-1] - t[0]) / TICK_S) + 1)
    ticks = t[0] + np.arange(n) * TICK_S
    idx = np.searchsorted(t, ticks, side="left")
    held = np.full(n, np.nan)
    for i in range(1, n):
        if idx[i] > 0:
            held[i] = v[idx[i] - 1]
    return ticks - t[0], held


def main() -> int:
    truth = true_peak_omega()
    print(f"true peak |omega| = {truth:.3f} deg/s  "
          f"(A0={A0}, f={FREQ} Hz, zeta={ZETA})\n")

    rng = np.random.default_rng(0)
    N_REP = 40
    fs = 100.0
    t = np.arange(0, 10, 1 / fs)
    clean = phi_of(t)

    configs = [("gradient+smooth", est_grad, 0.10, 2),
               ("gradient+smooth", est_grad, 0.15, 2),
               ("gradient+smooth", est_grad, 0.15, 3),
               ("SG derivative", est_sgderiv, 0.10, 2),
               ("SG derivative", est_sgderiv, 0.10, 3),
               ("SG derivative", est_sgderiv, 0.15, 3),
               ("SG derivative", est_sgderiv, 0.20, 3)]

    for noise in (0.25, 1.0):
        print(f"===== sensor noise sd = {noise} deg =====")
        print(f"{'estimator':18s} {'win':>5s} {'p':>2s} {'mean est':>9s} "
              f"{'bias %':>8s} {'CV %':>7s} {'|err| %':>8s}")
        results = []
        for name, fn, win_s, p in configs:
            vals = []
            for _ in range(N_REP):
                y = clean + rng.normal(0, noise, size=clean.shape)
                vals.append(float(np.max(np.abs(fn(y, t, win_s, p)))))
            v = np.array(vals)
            bias = (v.mean() - truth) / truth * 100
            cv = v.std(ddof=1) / v.mean() * 100
            err = np.mean(np.abs(v - truth)) / truth * 100
            results.append((err, name, win_s, p, v.mean(), bias, cv))
            print(f"{name:18s} {win_s:5.2f} {p:2d} {v.mean():9.3f} "
                  f"{bias:+8.1f} {cv:7.2f} {err:8.1f}")

        # the 20 Hz tick path, for comparison
        vals = []
        for _ in range(N_REP):
            y = clean + rng.normal(0, noise, size=clean.shape)
            tt, yy = tick_resample(t, y)
            fin = np.isfinite(yy)
            vals.append(float(np.max(np.abs(est_grad(yy[fin], tt[fin], 0.10, 2)))))
        v = np.array(vals)
        bias = (v.mean() - truth) / truth * 100
        cv = v.std(ddof=1) / v.mean() * 100
        err = np.mean(np.abs(v - truth)) / truth * 100
        print(f"{'20 Hz tick path':18s} {0.10:5.2f} {2:2d} {v.mean():9.3f} "
              f"{bias:+8.1f} {cv:7.2f} {err:8.1f}")

        results.sort()
        e, name, win_s, p, m, b, c = results[0]
        print(f"\n  most accurate: {name} win_s={win_s} p={p} "
              f"-> |err| {e:.1f}%, bias {b:+.1f}%, CV {c:.2f}%\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
