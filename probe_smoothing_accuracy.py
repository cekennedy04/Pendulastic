"""THROWAWAY PROBE -- not production code.

Why do the phone and the desktop count N differently, and which is right?

Mechanism (already established): the reference smooths over a window of 0.10
SECONDS converted through each trial's own dt, while the Rust port uses fixed
SAMPLE counts (15/15/9/7). Those agree only near 100 Hz. The scoring path
resamples to a 50 ms grid first, and at 20 Hz the reference's window is 5
samples (0.25 s) against the port's 9 (0.45 s) -- against a ~1 Hz swing the
port is averaging over nearly half a period.

This isolates that single variable inside one language: `compute_pt_params` is
run twice on identical synthetic signals whose true cycle count is known by
construction, once with the duration-based window (desktop) and once with the
port's fixed sample counts (phone).

Run:  ~/miniconda3/python.exe probe_smoothing_accuracy.py
"""
from __future__ import annotations

import warnings

import numpy as np

warnings.filterwarnings("ignore")

import pendulastic_pt_score as pt

_ORIG_SG = pt._sg
# Call order inside compute_pt_params: ang_s_raw(p=3), ang_s(p=3),
# phi_s(p=2), omega_s(p=2). Keying on polyorder plus an occurrence counter
# disambiguates the two p=2 sites (9 then 7) without touching the source.
_RUST_WIDTHS = {3: [15, 15], 2: [9, 7]}


class PhoneSmoothing:
    """Context manager: make `_sg` behave like the Rust port's fixed windows."""

    def __enter__(self):
        self.counts = {2: 0, 3: 0}

        def fake_sg(sig, dt, win_s=pt._SG_WINDOW_S, p=3):
            widths = _RUST_WIDTHS.get(p)
            if widths is None:
                return _ORIG_SG(sig, dt, win_s, p)
            i = min(self.counts[p], len(widths) - 1)
            self.counts[p] += 1
            w = widths[i]
            n = len(sig)
            w = min(w, n - 1 if n % 2 == 0 else n)
            if w % 2 == 0:
                w -= 1
            if w < p + 2:
                return np.asarray(sig, dtype=float).copy()
            from scipy.signal import savgol_filter
            return savgol_filter(sig, w, p)

        pt._sg = fake_sg
        return self

    def __exit__(self, *a):
        pt._sg = _ORIG_SG


def make_swing(n_cycles, fs, A0=47.0, freq=0.9, tail_s=6.0, noise=0.25, seed=3):
    rng = np.random.default_rng(seed)
    hold = np.full(int(1.0 * fs), A0)
    dur = n_cycles / freq
    ti = np.arange(0, dur, 1 / fs)
    min_amp = max(1.0, 0.05 * A0)
    zeta = -np.log(min_amp * 1.15 / A0) / (2 * np.pi * n_cycles)
    motion = A0 * np.exp(-zeta * 2 * np.pi * freq * ti) * np.cos(2 * np.pi * freq * ti)
    tail = np.zeros(int(tail_s * fs))
    ang = np.concatenate([hold, motion, tail]).astype(float)
    ang += rng.normal(0, noise, size=ang.shape)
    return np.arange(len(ang)) / fs, ang


def score(t, a, phone: bool):
    if phone:
        with PhoneSmoothing():
            p = pt.compute_pt_params(t, a)
    else:
        p = pt.compute_pt_params(t, a)
    return (p or {}).get("N"), (p or {}).get("R2n")


def main() -> int:
    print("Window widths actually used, by rate (polyorder 2, the N path):")
    for fs in (20, 30, 100, 120):
        dt = 1 / fs
        w = int(round(pt._SG_WINDOW_S / dt))
        if w % 2 == 0:
            w += 1
        if w < 4:
            w = 5
        print(f"  {fs:4d} Hz -> desktop {w:3d} samples ({w*dt:.3f} s)   "
              f"phone   9 samples ({9*dt:.3f} s)")
    print()

    print("=== Recovered N against known truth ===")
    print(f"{'rate':>6s} {'truth':>6s} {'desktop':>8s} {'phone':>7s} "
          f"{'d.err':>7s} {'p.err':>7s}")
    tot = {"desktop": [], "phone": []}
    for fs in (20, 30, 100, 120):
        for truth in (2.0, 4.0, 6.0, 8.0):
            t, a = make_swing(truth, fs)
            nd, _ = score(t, a, phone=False)
            np_, _ = score(t, a, phone=True)
            if nd is None or np_ is None:
                print(f"{fs:6d} {truth:6.1f}   (a trial failed to score)")
                continue
            ed, ep = nd - truth, np_ - truth
            tot["desktop"].append(abs(ed))
            tot["phone"].append(abs(ep))
            print(f"{fs:6d} {truth:6.1f} {nd:8.2f} {np_:7.2f} "
                  f"{ed:+7.2f} {ep:+7.2f}")
        print()

    print("=== mean |error| in cycles ===")
    for k, v in tot.items():
        print(f"  {k:8s} {np.mean(v):.3f}   (n={len(v)})")

    print("\n=== the rate the phone actually scores at (20 Hz), in detail ===")
    print(f"{'truth':>6s} {'desktop':>8s} {'phone':>7s}  verdict")
    for truth in (2.0, 3.0, 4.0, 5.0, 6.0, 8.0):
        t, a = make_swing(truth, 20)
        nd, _ = score(t, a, phone=False)
        np_, _ = score(t, a, phone=True)
        if nd is None or np_ is None:
            continue
        better = "desktop" if abs(nd - truth) < abs(np_ - truth) else (
            "phone" if abs(np_ - truth) < abs(nd - truth) else "tie")
        print(f"{truth:6.1f} {nd:8.2f} {np_:7.2f}  {better}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
