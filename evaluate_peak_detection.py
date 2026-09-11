"""
evaluate_peak_detection.py
============================
One-off research diagnostic (NOT pipeline-wired, same category as
evaluate_flex_axis_methods.py / evaluate_ockendon_methodology.py): asks two
questions about how compute_pt_params counts oscillation extrema.

PART 1 -- does the tail-noise pathology that _ACTIVE_WINDOW_CAP_SEC exists to
prevent still exist?

  _active_oscillation_window_end's docstring justifies the 4.0 s cap with a
  measurement: a synthetic single-drop trial (180deg hold -> 106deg -> 60deg,
  no rebound) read "N = 0.5 with a 3 s tail, 28.5 with a 30 s tail, purely
  from tail noise".  That number was measured against the code as it stood
  BEFORE commit a1ca2b5, which shipped the window cap and the
  prominence=min_amp gate together in a single commit.  Part 1 reproduces the
  pathology under both the pre-a1ca2b5 detector (height=min_amp only) and the
  current one (height AND prominence), to establish which of the two fixes
  actually does the work.

PART 2 -- bake-off of extremum-detection methods for counting cycles on a
decaying pendulum swing, scored on (a) recovering the true cycle count across
damping / amplitude / frequency / sample rate, (b) not reinstating whatever
Part 1 establishes as the tail-noise failure, and (c) correctly reporting no
oscillation on a genuine single-drop (severe spasticity) trial.

Nothing in pendulastic_pt_score.py is edited: every variant is applied by
monkeypatching module-level names that compute_pt_params reads at call time
(P.find_peaks, P._active_oscillation_window_end, P._ACTIVE_WINDOW_CAP_SEC)
and restoring them afterwards.

Usage:
    ~/miniconda3/python.exe evaluate_peak_detection.py [part1|part2|all]
"""
from __future__ import annotations

import sys
from contextlib import contextmanager

import numpy as np
from scipy.signal import find_peaks as _scipy_find_peaks
from scipy.signal import savgol_filter

import pendulastic_pt_score as P

_ORIG_FIND_PEAKS = P.find_peaks
_ORIG_WINDOW_FN = P._active_oscillation_window_end
_ORIG_CAP = P._ACTIVE_WINDOW_CAP_SEC


# ══════════════════════════════════════════════════════════════════════════════
# Synthetic trial generators
# ══════════════════════════════════════════════════════════════════════════════

def add_noise(t, ang, kind, amp, freq=1.0, seed=0):
    rng = np.random.default_rng(seed)
    n = len(ang)
    if kind == "none" or amp == 0:
        return ang
    if kind == "white":
        return ang + rng.normal(0.0, amp, n)
    if kind == "walk":          # gyro-integration style drift
        return ang + np.cumsum(rng.normal(0.0, amp, n))
    if kind == "tremor":
        return ang + amp * np.sin(2 * np.pi * freq * t + rng.uniform(0, 2 * np.pi))
    if kind == "tremor+white":
        return (ang + amp * np.sin(2 * np.pi * freq * t)
                + rng.normal(0.0, 0.3, n))
    raise ValueError(kind)


def single_drop(fs=20.0, hold_s=2.0, tail_s=30.0,
                start=180.0, mid=106.0, end=60.0,
                drop1_s=0.35, plateau_s=0.30, drop2_s=0.35,
                noise="white", noise_amp=0.3, noise_f=1.0, seed=0):
    """The docstring's synthetic: a two-stage monotonic descent with NO
    rebound.  180 hold -> 106 -> 60, then a resting tail of tail_s seconds.
    Each stage is a raised-cosine ease so the signal is C1-smooth."""
    def hold(v, dur):
        return np.full(max(1, int(round(dur * fs))), float(v))

    def ramp(a, b, dur):
        n = max(2, int(round(dur * fs)))
        u = np.linspace(0.0, 1.0, n)
        return a + (b - a) * (0.5 - 0.5 * np.cos(np.pi * u))

    segs = [hold(start, hold_s), ramp(start, mid, drop1_s)]
    if plateau_s > 0:
        segs.append(hold(mid, plateau_s))
    segs += [ramp(mid, end, drop2_s), hold(end, tail_s)]
    ang = np.concatenate(segs)
    t = np.arange(len(ang)) / fs
    return t, add_noise(t, ang, noise, noise_amp, noise_f, seed)


def decaying_swing(fs=20.0, hold_s=2.0, post_s=20.0, A0=45.0, f=1.0,
                   damping=0.5, neutral=60.0,
                   noise="white", noise_amp=0.3, noise_f=1.0, seed=0):
    """phi(t) = A0 * exp(-damping*t) * cos(2*pi*f*t), t measured from release,
    riding on `neutral`, preceded by a flat hold at neutral+A0.

    `damping` is a per-second envelope rate (1/s), NOT a damping ratio.  This
    parameterisation is the one the task's own reference numbers use: at
    A0=45, f=1 Hz, min_amp = max(1, 0.05*A0) = 2.25 the analytic cycle counts
    are 12 / 9 / 6 for damping 0.25 / 0.35 / 0.50, matching the figures quoted
    in the brief."""
    n_hold = max(1, int(round(hold_s * fs)))
    n_post = max(2, int(round(post_s * fs)))
    t = np.arange(n_hold + n_post) / fs
    tr = np.arange(n_post) / fs
    phi = A0 * np.exp(-damping * tr) * np.cos(2 * np.pi * f * tr)
    ang = np.concatenate([np.full(n_hold, neutral + A0), neutral + phi])
    return t, add_noise(t, ang, noise, noise_amp, noise_f, seed)


def true_cycle_count(A0, f, damping, post_s):
    """Ground truth for N under compute_pt_params' own definition:
    N = (#maxima with phi > min_amp + #minima with phi < -min_amp) / 2.

    Extrema of A0*exp(-d t)*cos(2 pi f t) sit (to first order in d/f) at
    t_k = k/(2f), k = 0,1,2,...  with |phi(t_k)| = A0*exp(-d*k/(2f)).
    k = 0 is the release sample itself: it is the first point of the array,
    so find_peaks can never see it as a local maximum (nothing precedes it).
    Ground truth therefore counts k >= 1 only."""
    min_amp = max(1.0, 0.05 * A0)
    k = 1
    n = 0
    while True:
        t_k = k / (2.0 * f)
        if t_k > post_s:
            break
        if A0 * np.exp(-damping * t_k) <= min_amp:
            break
        n += 1
        k += 1
    return n / 2.0


# ══════════════════════════════════════════════════════════════════════════════
# Extremum-detection methods (installed as P.find_peaks / window overrides)
# ══════════════════════════════════════════════════════════════════════════════

def _fp_current(x, **kw):
    """M1/M2 detector: height=min_amp AND prominence=min_amp (current code)."""
    return _scipy_find_peaks(x, **kw)


def _fp_height_only(x, **kw):
    """The pre-a1ca2b5 detector: height + distance, NO prominence."""
    kw.pop("prominence", None)
    return _scipy_find_peaks(x, **kw)


def _fp_prominence_only(x, **kw):
    """M3: prominence + distance, NO height gate."""
    kw.pop("height", None)
    return _scipy_find_peaks(x, **kw)


_SCHMITT_FRAC = 0.05


def _fp_schmitt(x, **kw):
    """M4: maxima of x located as sign reversals of d(x)/dn, accepted only if
    |omega| exceeded _SCHMITT_FRAC * max|omega| since the previous accepted
    reversal (a Schmitt trigger on angular velocity).

    omega here is DIFFERENTIATED angle, exactly as compute_pt_params builds
    its own omega_s (savgol of np.gradient(phi, t_r)), so it inherits the
    angle's noise; the sample spacing cancels because the threshold is a
    fraction of the signal's own maximum.  height/distance are still applied
    afterwards so every method faces the same downstream amplitude gate."""
    height = kw.get("height", None)
    distance = kw.get("distance", 1)
    n = len(x)
    if n < 9:
        return np.array([], dtype=int), {}
    om = np.gradient(np.asarray(x, dtype=float))
    w = min(7, n - 1 if n % 2 == 0 else n)
    w = w if w % 2 == 1 else w - 1
    if w >= 4:
        om = savgol_filter(om, w, 2)
    thr = _SCHMITT_FRAC * float(np.nanmax(np.abs(om))) if n else 0.0
    if thr <= 0:
        return np.array([], dtype=int), {}

    idx = []
    armed = False          # has |omega| exceeded thr while rising?
    for i in range(1, n):
        if om[i] > thr:
            armed = True
        if armed and om[i - 1] > 0 >= om[i]:
            # local maximum of x between i-1 and i
            j = i - 1 if x[i - 1] >= x[i] else i
            idx.append(j)
            armed = False
    idx = np.asarray(idx, dtype=int)
    if height is not None and len(idx):
        idx = idx[x[idx] >= height]
    if distance and len(idx) > 1:
        keep = [int(idx[0])]
        for j in idx[1:]:
            if j - keep[-1] >= distance:
                keep.append(int(j))
        idx = np.asarray(keep, dtype=int)
    return idx, {}


def _fp_ampd(x, **kw):
    """M5: AMPD (Scholkmann 2012 automatic multiscale peak detection).

    Builds the local-maxima scalogram over scales k=1..L, picks the scale
    lambda with the most maxima, and keeps only indices that are a local
    maximum at EVERY scale <= lambda.  L is capped at 1.5*fs-equivalent
    samples: the pipeline already refuses inter-peak gaps shorter than
    fps/3.5, so scales far beyond one pendulum period carry no information
    and the O(L*N) cost is wasted.  height/distance are applied afterwards,
    same as every other method; AMPD's multiscale criterion replaces the
    prominence gate rather than supplementing it."""
    height = kw.get("height", None)
    distance = int(kw.get("distance", 1) or 1)
    x = np.asarray(x, dtype=float)
    n = len(x)
    L = min(n // 2 - 1, max(4, distance * 6))
    if L < 2:
        return np.array([], dtype=int), {}
    xd = x - np.linspace(x[0], x[-1], n)      # remove the gross linear trend
    lms = np.zeros((L, n), dtype=bool)
    for k in range(1, L + 1):
        m = np.zeros(n, dtype=bool)
        m[k:n - k] = (xd[k:n - k] > xd[0:n - 2 * k]) & (xd[k:n - k] > xd[2 * k:n])
        lms[k - 1] = m
    gamma = lms.sum(axis=1)
    lam = int(np.argmax(gamma)) + 1
    idx = np.where(lms[:lam].all(axis=0))[0]
    if height is not None and len(idx):
        idx = idx[x[idx] >= height]
    if distance and len(idx) > 1:
        keep = [int(idx[0])]
        for j in idx[1:]:
            if j - keep[-1] >= distance:
                keep.append(int(j))
        idx = np.asarray(keep, dtype=int)
    return idx, {}


def _window_unbounded(t_r, ang_r, pk_i, tr_i, neutral, A0):
    """No window at all -- count over the whole post-release record."""
    return float("inf")


def _window_settle(t_r, ang_r, pk_i, tr_i, neutral, A0):
    """M2: the SECOND branch of _active_oscillation_window_end, applied
    unconditionally and with the 4 s cap removed.  Window ends at the first
    time after which the signal is PERMANENTLY within tol of neutral."""
    tol = max(2.0, 0.05 * A0)
    near = np.abs(ang_r - neutral) <= tol
    settle_idx = len(ang_r) - 1
    # np.all(near[i:]) for every i, computed once: the last index at which
    # near is False is the settle boundary.
    bad = np.where(~near)[0]
    if len(bad):
        settle_idx = min(int(bad[-1]) + 1, len(ang_r) - 1)
    else:
        settle_idx = 0
    return float(t_r[settle_idx])


@contextmanager
def variant(fp=None, window=None, cap=None):
    P.find_peaks = fp or _ORIG_FIND_PEAKS
    P._active_oscillation_window_end = window or _ORIG_WINDOW_FN
    P._ACTIVE_WINDOW_CAP_SEC = _ORIG_CAP if cap is None else cap
    try:
        yield
    finally:
        P.find_peaks = _ORIG_FIND_PEAKS
        P._active_oscillation_window_end = _ORIG_WINDOW_FN
        P._ACTIVE_WINDOW_CAP_SEC = _ORIG_CAP


# name -> (find_peaks impl, window impl, cap)
METHODS = {
    "M1 current (h+prom, 4s cap)":  (_fp_current,         None,              4.0),
    "M1b current, NO window":       (_fp_current,         _window_unbounded, None),
    "M2 h+prom, settle window":     (_fp_current,         _window_settle,    None),
    "M3 prominence only, settle":   (_fp_prominence_only, _window_settle,    None),
    "M4 omega Schmitt, settle":     (_fp_schmitt,         _window_settle,    None),
    "M5 AMPD, settle":              (_fp_ampd,            _window_settle,    None),
}

# The pre-a1ca2b5 detector, used only in Part 1's archaeology.
PREFIX_METHODS = {
    "PRE-FIX height-only, 4s cap": (_fp_height_only, None,              4.0),
    "PRE-FIX height-only, NO win": (_fp_height_only, _window_unbounded, None),
    "CURRENT h+prom, 4s cap":      (_fp_current,     None,              4.0),
    "CURRENT h+prom, NO win":      (_fp_current,     _window_unbounded, None),
}


def score(t, ang, method_spec, detrend=True):
    fp, win, cap = method_spec
    with variant(fp, win, cap):
        try:
            return P.compute_pt_params(t, ang, detrend=detrend)
        except Exception as exc:                       # never abort a sweep
            return {"_error": repr(exc)}


def N_of(r):
    if r is None:
        return float("nan")
    if "_error" in r:
        return float("nan")
    return float(r["N"])


def A1_of(r):
    if r is None or "_error" in r:
        return float("nan")
    return float(r["A1_deg"])


# ══════════════════════════════════════════════════════════════════════════════
# PART 1
# ══════════════════════════════════════════════════════════════════════════════

def part1():
    print("=" * 100)
    print("PART 1 -- does the tail-noise pathology reproduce?")
    print("=" * 100)

    print("\n[1.1] The brief's failed attempt, re-run: single drop 180->106->60,")
    print("      white noise 0.3-1.0 deg, tails 3-30 s, CURRENT detector.")
    print(f"{'tail_s':>7} {'noise':>7} {'A0':>8} {'min_amp':>8} {'neutral':>8}"
          f" {'N (4s cap)':>11} {'N (no win)':>11}")
    for tail in (3, 5, 10, 20, 30):
        for na in (0.3, 0.5, 1.0):
            t, a = single_drop(tail_s=tail, noise="white", noise_amp=na, seed=1)
            rc = score(t, a, PREFIX_METHODS["CURRENT h+prom, 4s cap"])
            ru = score(t, a, PREFIX_METHODS["CURRENT h+prom, NO win"])
            print(f"{tail:7} {na:7.2f} {ru['A0_deg']:8.2f} "
                  f"{max(1.0, 0.05*ru['A0_deg']):8.2f} {ru['neutral_deg']:8.2f} "
                  f"{N_of(rc):11.1f} {N_of(ru):11.1f}")
    print("      -> confirms the brief's negative result.")

    print("\n[1.2] Same signals, PRE-a1ca2b5 detector (height=min_amp, NO prominence).")
    print(f"{'tail_s':>7} {'noise':>7} {'N (4s cap)':>11} {'N (no win)':>11}")
    for tail in (3, 5, 10, 20, 30):
        for na in (0.3, 1.0):
            t, a = single_drop(tail_s=tail, noise="white", noise_amp=na, seed=1)
            rc = score(t, a, PREFIX_METHODS["PRE-FIX height-only, 4s cap"])
            ru = score(t, a, PREFIX_METHODS["PRE-FIX height-only, NO win"])
            print(f"{tail:7} {na:7.2f} {N_of(rc):11.1f} {N_of(ru):11.1f}")
    print("      -> the N=0.5 floor the docstring quotes for a 3 s tail appears here,")
    print("         and it is NOT noise: it is the descent's own 106 deg shoulder read")
    print("         as one height-only 'peak'. Verify with a noiseless signal:")
    t, a = single_drop(tail_s=3, noise="none")
    print(f"         noiseless, 3 s tail, height-only : N = {N_of(score(t, a, PREFIX_METHODS['PRE-FIX height-only, NO win'])):.1f}")
    print(f"         noiseless, 3 s tail, h+prominence: N = {N_of(score(t, a, PREFIX_METHODS['CURRENT h+prom, NO win'])):.1f}")

    print("\n[1.3] Third ingredient: a SHORT pre-release hold + detrend=True.")
    print("      compute_pt_params fits its drift slope on the pre-release baseline")
    print("      only, then extrapolates it across the WHOLE trial. A short, noisy")
    print("      baseline gives a badly-estimated slope; extrapolating it over a 30 s")
    print("      tail injects tens of degrees of ramp, dragging the tail far off")
    print("      `neutral` so the height gate is satisfied everywhere in the tail.")
    print(f"{'fs':>5} {'hold_s':>7} {'noise':>7} {'detrend':>8} | "
          f"{'N@3s tail':>10} {'N@30s tail':>11} {'neutral@30s':>12}")
    for fs in (20.0, 120.0):
        for hold in (0.6, 2.0):
            for na in (0.3, 2.0):
                for det in (True, False):
                    t3, a3 = single_drop(fs=fs, hold_s=hold, tail_s=3,
                                         noise="white", noise_amp=na, seed=7)
                    t30, a30 = single_drop(fs=fs, hold_s=hold, tail_s=30,
                                           noise="white", noise_amp=na, seed=7)
                    m = PREFIX_METHODS["PRE-FIX height-only, NO win"]
                    r3 = score(t3, a3, m, detrend=det)
                    r30 = score(t30, a30, m, detrend=det)
                    print(f"{fs:5.0f} {hold:7.2f} {na:7.2f} {str(det):>8} | "
                          f"{N_of(r3):10.1f} {N_of(r30):11.1f} "
                          f"{r30['neutral_deg'] if r30 and '_error' not in r30 else float('nan'):12.2f}")

    print("\n[1.4] MINIMAL SYNTHETIC that reproduces the docstring's exact numbers.")
    print("      fs=120 Hz, pre-release hold 0.6 s, two-stage descent 180->106->60,")
    print("      white noise sd 2.0 deg, seed 7, detrend=True.")
    print(f"{'tail_s':>7} | {'PRE-FIX no win':>15} {'PRE-FIX 4s cap':>15} "
          f"{'CURRENT no win':>15} {'CURRENT 4s cap':>15}")
    for tail in (3, 30):
        t, a = single_drop(fs=120.0, hold_s=0.6, tail_s=tail,
                           noise="white", noise_amp=2.0, seed=7)
        vals = [N_of(score(t, a, PREFIX_METHODS[k])) for k in
                ("PRE-FIX height-only, NO win", "PRE-FIX height-only, 4s cap",
                 "CURRENT h+prom, NO win", "CURRENT h+prom, 4s cap")]
        print(f"{tail:7} | {vals[0]:15.1f} {vals[1]:15.1f} {vals[2]:15.1f} {vals[3]:15.1f}")
    print("      Docstring claims N = 0.5 (3 s tail) and N = 28.5 (30 s tail).")

    print("\n[1.5] The A1 half of the claim: 'a single spurious tail trough could")
    print("      flip A1 from 0 to ~A0'.")
    print(f"{'tail_s':>7} | {'A1 PRE-FIX no win':>18} {'A1 CURRENT no win':>18} {'A0':>8}")
    for tail in (3, 30):
        t, a = single_drop(fs=120.0, hold_s=0.6, tail_s=tail,
                           noise="white", noise_amp=2.0, seed=7)
        rp = score(t, a, PREFIX_METHODS["PRE-FIX height-only, NO win"])
        rc = score(t, a, PREFIX_METHODS["CURRENT h+prom, NO win"])
        print(f"{tail:7} | {A1_of(rp):18.2f} {A1_of(rc):18.2f} {rp['A0_deg']:8.2f}")

    print("\n[1.6] Does the CURRENT detector EVER over-count a resting tail?")
    print("      Sweep noise character and amplitude against min_amp (~6 deg here),")
    print("      2 s hold, 30 s tail, unbounded window, prominence ON.")
    print(f"{'noise kind':>14} {'amp':>7} {'freq':>6} {'min_amp':>8} "
          f"{'N (no win)':>11} {'N (4s cap)':>11} {'N (settle win)':>15}")
    cases = ([("white", a, 0.0) for a in (0.3, 1, 2, 3, 5, 8, 12, 20)]
             + [("walk", a, 0.0) for a in (0.05, 0.1, 0.3, 0.6)]
             + [("tremor", a, f) for a in (1, 3, 8, 15) for f in (0.5, 2.0)])
    for kind, amp, fr in cases:
        t, a = single_drop(tail_s=30, noise=kind, noise_amp=amp, noise_f=fr, seed=3)
        ru = score(t, a, PREFIX_METHODS["CURRENT h+prom, NO win"])
        rc = score(t, a, PREFIX_METHODS["CURRENT h+prom, 4s cap"])
        rs = score(t, a, METHODS["M2 h+prom, settle window"])
        ma = (max(1.0, 0.05 * ru["A0_deg"]) if ru and "_error" not in ru else float("nan"))
        print(f"{kind:>14} {amp:7.2f} {fr:6.1f} {ma:8.2f} "
              f"{N_of(ru):11.1f} {N_of(rc):11.1f} {N_of(rs):15.1f}")


# ══════════════════════════════════════════════════════════════════════════════
# PART 2
# ══════════════════════════════════════════════════════════════════════════════

DAMPINGS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5]
AMPLITUDES = [10.0, 25.0, 45.0, 70.0]
FREQS = [0.4, 0.8, 1.2, 1.6]
RATES = [20.0, 60.0, 120.0]
SEEDS = [1, 2]
POST_S = 20.0


def part2():
    print("\n" + "=" * 100)
    print("PART 2 -- bake-off: recovering the true cycle count")
    print("=" * 100)
    print(f"grid: damping {DAMPINGS}")
    print(f"      A0 {AMPLITUDES} deg, f {FREQS} Hz, fs {RATES} Hz, seeds {SEEDS}")
    print(f"      {POST_S:.0f} s post-release record, 2 s hold, white noise sd 0.3 deg,")
    print( "      neutral = 60 deg, detrend=True")

    names = list(METHODS)
    err = {k: [] for k in names}
    signed = {k: [] for k in names}
    per_damp = {k: {d: [] for d in DAMPINGS} for k in names}
    per_rate = {k: {r: [] for r in RATES} for k in names}
    n_cfg = 0
    for d in DAMPINGS:
        for A0 in AMPLITUDES:
            for f in FREQS:
                truth = true_cycle_count(A0, f, d, POST_S)
                for fs in RATES:
                    for sd in SEEDS:
                        t, a = decaying_swing(fs=fs, A0=A0, f=f, damping=d,
                                              post_s=POST_S, noise="white",
                                              noise_amp=0.3, seed=sd)
                        n_cfg += 1
                        for k in names:
                            v = N_of(score(t, a, METHODS[k]))
                            if not np.isfinite(v):
                                continue
                            err[k].append(abs(v - truth))
                            signed[k].append(v - truth)
                            per_damp[k][d].append(abs(v - truth))
                            per_rate[k][fs].append(abs(v - truth))
    print(f"\n{n_cfg} synthetic trials per method.\n")
    print(f"{'method':<30} {'MAE':>7} {'median AE':>10} {'bias':>8} {'|err|<=0.5':>11} {'|err|<=1':>9}")
    for k in names:
        e = np.array(err[k]); s = np.array(signed[k])
        print(f"{k:<30} {e.mean():7.2f} {np.median(e):10.2f} {s.mean():8.2f} "
              f"{100*np.mean(e <= 0.5):10.0f}% {100*np.mean(e <= 1.0):8.0f}%")

    print("\nMAE by damping (1/s):")
    hdr = "  ".join(f"{d:>6.2f}" for d in DAMPINGS)
    print(f"{'method':<30} {hdr}")
    for k in names:
        print(f"{k:<30} " + "  ".join(f"{np.mean(per_damp[k][d]):6.2f}" for d in DAMPINGS))

    print("\nMAE by sample rate (Hz):")
    hdr = "  ".join(f"{r:>7.0f}" for r in RATES)
    print(f"{'method':<30} {hdr}")
    for k in names:
        print(f"{k:<30} " + "  ".join(f"{np.mean(per_rate[k][r]):7.2f}" for r in RATES))

    print("\nWorked example -- A0=45, f=1.0 Hz, fs=20 Hz, varying damping only")
    print("(the brief's own table):")
    print(f"{'damping':>8} {'true N':>7} " + " ".join(f"{k.split()[0]:>6}" for k in names))
    for d in (0.25, 0.35, 0.5):
        t, a = decaying_swing(fs=20.0, A0=45.0, f=1.0, damping=d, post_s=POST_S,
                              noise="white", noise_amp=0.3, seed=1)
        vals = [N_of(score(t, a, METHODS[k])) for k in names]
        print(f"{d:8.2f} {true_cycle_count(45.0, 1.0, d, POST_S):7.1f} "
              + " ".join(f"{v:6.1f}" for v in vals))

    # ── (b) tail-noise stress ────────────────────────────────────────────────
    print("\n" + "-" * 100)
    print("(b) tail-noise stress: does the method reinstate the Part 1 failure?")
    print("    All are single-drop trials (TRUE N = 0) with a 30 s resting tail.")
    print("-" * 100)
    stress = [
        ("docstring repro (fs120,hold0.6,wn2.0)",
         dict(fs=120.0, hold_s=0.6, tail_s=30, noise="white", noise_amp=2.0, seed=7)),
        ("realistic wn 0.3",
         dict(fs=20.0, hold_s=2.0, tail_s=30, noise="white", noise_amp=0.3, seed=3)),
        ("wn 5 deg (~min_amp)",
         dict(fs=20.0, hold_s=2.0, tail_s=30, noise="white", noise_amp=5.0, seed=3)),
        ("wn 12 deg (2x min_amp)",
         dict(fs=20.0, hold_s=2.0, tail_s=30, noise="white", noise_amp=12.0, seed=3)),
        ("drift walk step 0.3",
         dict(fs=20.0, hold_s=2.0, tail_s=30, noise="walk", noise_amp=0.3, seed=2)),
        ("tremor 8 deg @ 0.5 Hz",
         dict(fs=20.0, hold_s=2.0, tail_s=30, noise="tremor", noise_amp=8.0,
              noise_f=0.5, seed=3)),
        ("tremor 15 deg @ 2 Hz",
         dict(fs=20.0, hold_s=2.0, tail_s=30, noise="tremor", noise_amp=15.0,
              noise_f=2.0, seed=3)),
    ]
    print(f"{'case':<40} " + " ".join(f"{k.split()[0]:>6}" for k in names))
    for label, kw in stress:
        t, a = single_drop(**kw)
        print(f"{label:<40} " + " ".join(f"{N_of(score(t, a, METHODS[k])):6.1f}" for k in names))

    # ── (c) genuine single drop, no rebound ──────────────────────────────────
    print("\n" + "-" * 100)
    print("(c) genuine severe-spasticity single drop, no rebound (TRUE N = 0),")
    print("    realistic 0.3 deg white noise, short 4 s tail and long 30 s tail")
    print("-" * 100)
    print(f"{'case':<40} " + " ".join(f"{k.split()[0]:>6}" for k in names))
    for label, kw in [
        ("1-stage 180->60, 4 s tail",
         dict(tail_s=4, mid=120.0, plateau_s=0.0, noise="white", noise_amp=0.3, seed=5)),
        ("1-stage 180->60, 30 s tail",
         dict(tail_s=30, mid=120.0, plateau_s=0.0, noise="white", noise_amp=0.3, seed=5)),
        ("2-stage 180->106->60, 4 s tail",
         dict(tail_s=4, noise="white", noise_amp=0.3, seed=5)),
        ("2-stage 180->106->60, 30 s tail",
         dict(tail_s=30, noise="white", noise_amp=0.3, seed=5)),
        ("slow controlled lowering, 30 s tail",
         dict(tail_s=30, mid=140.0, drop1_s=2.5, plateau_s=0.0, drop2_s=2.5,
              noise="white", noise_amp=0.3, seed=5)),
    ]:
        t, a = single_drop(**kw)
        print(f"{label:<40} " + " ".join(f"{N_of(score(t, a, METHODS[k])):6.1f}" for k in names))

    # ── Schmitt hysteresis sensitivity ───────────────────────────────────────
    global _SCHMITT_FRAC
    print("\n" + "-" * 100)
    print("M4 sensitivity to the Schmitt hysteresis fraction (MAE on the main grid,")
    print("subsampled to fs=20/60 Hz, seed 1)")
    print("-" * 100)
    saved = _SCHMITT_FRAC
    print(f"{'frac':>6} {'MAE':>7} {'bias':>8}   single-drop N (2-stage, 30 s tail)")
    for frac in (0.02, 0.05, 0.10, 0.20, 0.35):
        _SCHMITT_FRAC = frac
        e, s = [], []
        for d in DAMPINGS:
            for A0 in AMPLITUDES:
                for f in FREQS:
                    truth = true_cycle_count(A0, f, d, POST_S)
                    for fs in (20.0, 60.0):
                        t, a = decaying_swing(fs=fs, A0=A0, f=f, damping=d,
                                              post_s=POST_S, noise="white",
                                              noise_amp=0.3, seed=1)
                        v = N_of(score(t, a, METHODS["M4 omega Schmitt, settle"]))
                        if np.isfinite(v):
                            e.append(abs(v - truth)); s.append(v - truth)
        t, a = single_drop(tail_s=30, noise="white", noise_amp=0.3, seed=5)
        sd_n = N_of(score(t, a, METHODS["M4 omega Schmitt, settle"]))
        print(f"{frac:6.2f} {np.mean(e):7.2f} {np.mean(s):8.2f}   {sd_n:6.1f}")
    _SCHMITT_FRAC = saved


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("part1", "all"):
        part1()
    if which in ("part2", "all"):
        part2()
