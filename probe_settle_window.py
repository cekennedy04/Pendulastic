"""THROWAWAY PROBE -- not production code.

Condition 1 from the POV: is a settle-based oscillation window safe where a
raised fixed cap is not?

The corpus cannot answer this. Its post-settle tails run median 3.3 s / max
10.2 s, while the bug the 4 s cap was added for was demonstrated on a 30 s
tail (N read 0.5 at 3 s vs 28.5 at 30 s, from tail noise alone). So this
builds the bug's own repro synthetically, with known ground-truth N, and
compares three windowing rules:

  A. current      -- fixed 4 s cap
  B. raised cap   -- fixed 10 s cap (the naive fix)
  C. settle-based -- window ends where the signal is permanently within
                     tolerance of neutral, with a generous absolute backstop

`compute_pt_params` already contains rule C -- but only as the branch taken
when NO extrema are detected. This asks whether promoting it to the primary
rule keeps N honest on long tails.

Run:  ~/miniconda3/python.exe probe_settle_window.py
"""
from __future__ import annotations

import warnings

import numpy as np

warnings.filterwarnings("ignore")

import pendulastic_pt_score as pt

FS = 100.0
NEUTRAL = 0.0
RNG = np.random.default_rng(7)

_ORIG_WINDOW_FN = pt._active_oscillation_window_end


def settle_based_window_end(t_r, ang_r, pk_i, tr_i, neutral, A0,
                            backstop=15.0):
    """Rule C. Settle time is the primary bound for BOTH branches.

    The existing code uses this only when find_peaks returns nothing. Here it
    always applies: the window closes once the signal is permanently within
    tolerance of neutral, so a long quiet tail is excluded by construction
    rather than by a clock.
    """
    t_r = np.asarray(t_r, dtype=float)
    ang_r = np.asarray(ang_r, dtype=float)
    tol = max(2.0, 0.05 * A0)
    near = np.abs(ang_r - neutral) <= tol
    settle_idx = len(ang_r) - 1
    # walk backwards: the last index where the signal was still OUTSIDE tol
    outside = np.flatnonzero(~near)
    if len(outside):
        settle_idx = min(int(outside[-1]) + 1, len(ang_r) - 1)
    else:
        settle_idx = 0
    return min(t_r[0] + backstop, float(t_r[settle_idx]))


def make_trial(kind: str, tail_s: float, noise_deg: float, n_cycles=4.0,
               A0=47.0, f=0.9):
    """Hold, then motion, then a quiet tail. Ground-truth N is known."""
    hold = np.full(int(1.0 * FS), A0)
    if kind == "decay":
        # decaying cosine with `n_cycles` visible cycles above min_amp
        dur = n_cycles / f
        t = np.arange(0, dur, 1 / FS)
        # pick zeta so the last cycle still clears min_amp = max(1, .05*A0)
        min_amp = max(1.0, 0.05 * A0)
        zeta = -np.log(min_amp * 1.15 / A0) / (2 * np.pi * n_cycles)
        motion = A0 * np.exp(-zeta * 2 * np.pi * f * t) * np.cos(2 * np.pi * f * t)
        truth = n_cycles
    else:  # "drop" -- the original bug case: single fall, no rebound
        dur = 2.0
        t = np.arange(0, dur, 1 / FS)
        motion = A0 * (1 - t / dur) ** 2
        truth = 0.0
    tail = np.zeros(int(tail_s * FS))
    ang = np.concatenate([hold, motion, tail]).astype(float)
    ang += RNG.normal(0, noise_deg, size=ang.shape)
    t_all = np.arange(len(ang)) / FS
    return t_all, ang, truth


def score(t, a, rule, cap=4.0):
    if rule == "settle":
        pt._active_oscillation_window_end = settle_based_window_end
    else:
        pt._active_oscillation_window_end = _ORIG_WINDOW_FN
        pt._ACTIVE_WINDOW_CAP_SEC = cap
    try:
        p = pt.compute_pt_params(t, a)
    except Exception:
        p = None
    finally:
        pt._active_oscillation_window_end = _ORIG_WINDOW_FN
        pt._ACTIVE_WINDOW_CAP_SEC = 4.0
    return (p or {}).get("N")


def main() -> int:
    print("Ground-truth N is known by construction. 'nan' = trial did not score.\n")
    for noise in (0.3, 1.0, 2.5):
        print(f"===== sensor noise sd = {noise} deg =====")
        print(f"{'case':>18s} {'tail':>6s} {'truth':>6s} "
              f"{'A: cap 4s':>10s} {'B: cap 10s':>11s} {'C: settle':>10s}")
        for kind in ("decay", "drop"):
            for tail in (3.0, 10.0, 20.0, 30.0):
                t, a, truth = make_trial(kind, tail, noise)
                a4 = score(t, a, "cap", 4.0)
                a10 = score(t, a, "cap", 10.0)
                cs = score(t, a, "settle")
                fmt = lambda v: "  nan" if v is None else f"{v:5.1f}"
                print(f"{kind:>18s} {tail:5.0f}s {truth:6.1f} "
                      f"{fmt(a4):>10s} {fmt(a10):>11s} {fmt(cs):>10s}")
        print()

    print("===== does rule C track truth as the real cycle count varies? =====")
    print(f"{'truth N':>8s} {'A: cap 4s':>10s} {'B: cap 10s':>11s} {'C: settle':>10s}   (20s tail, 0.3 deg noise)")
    for n_cyc in (2.0, 4.0, 6.0, 8.0):
        t, a, truth = make_trial("decay", 20.0, 0.3, n_cycles=n_cyc)
        fmt = lambda v: "  nan" if v is None else f"{v:5.1f}"
        print(f"{truth:8.1f} {fmt(score(t,a,'cap',4.0)):>10s} "
              f"{fmt(score(t,a,'cap',10.0)):>11s} {fmt(score(t,a,'settle')):>10s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
