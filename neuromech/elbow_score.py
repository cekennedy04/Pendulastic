"""Score an elbow pendulum trace the way the elbow literature does. Never a grade.

Plan: docs/plans/2026-11-10-001-plan-neuromech-elbow-emulator.md, unit U4.

Measures (raw values, each with the estimator that produced it):

  swings     reversals after release whose excursion from the rest exceeds
             `swing_thr_deg`, found with hysteresis (a reversal counts only
             after the trace has moved `swing_thr_deg` back from the extreme),
             so sensor noise cannot add swings. OUR definition: the Lin-group
             counting rule is in the paywalled papers and replaces this once read.
  RI         relaxation index, de Lima et al. 2023's form: first-swing
             excursion over the release-to-rest distance (no 1.6, unlike the
             knee's R2). Withheld when the trace has not settled.
  zeta, wn   by LOG DECREMENT over successive same-sign peaks about the rest
             (Valle et al. 2025; de Lima), and by LEAST-SQUARES FIT of
             J*q'' + B*q' + K*(q - q_rest) = 0  (Lin 2003) after Savitzky-Golay
             smoothing: in LINEAR mode on |q - rest| <= fit_max_amp_deg (sin x ~ x);
             in GRAVITY mode (J and k_gravity given) on |q - rest| <=
             fit_max_amp_gravity_deg with gravity removed as k_gravity*sin(x),
             fitting B and the tissue K directly. Known bias: a linear-mode fit of
             a zeta ~ 2 trace reads K ~ +13%, zeta ~ +5%.
  B, K       from zeta and wn when J is given: K = J*wn**2 (correcting the
             review's printed K = J*wn), B = 2*zeta*J*wn. K includes gravity's
             stiffness unless `k_gravity` is given, in which case `K_tissue`
             is reported as well -- the two published K scales differ on exactly
             this.

Release is the last sample of any initial hold: samples before it are dropped.

A result carries `refusals`: a dict of measure -> reason for every measure it
declined to give, including when the noise floor is too high for the swing
threshold or the trace is growing rather than decaying. There is no PT score and
no MAS band anywhere in this module (plan E5).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import savgol_filter


@dataclass
class ElbowScore:
    rest_deg: float | None = None
    settled: bool = False
    release_index: int = 0
    noise_deg: float | None = None
    swings: int | None = None
    ri: float | None = None
    zeta_logdec: float | None = None
    wn_logdec: float | None = None
    zeta_fit: float | None = None
    wn_fit: float | None = None
    fit_r2: float | None = None
    fit_mode: str | None = None
    B_logdec: float | None = None
    K_logdec: float | None = None
    B_fit: float | None = None
    K_fit: float | None = None
    K_tissue_logdec: float | None = None
    K_tissue_fit: float | None = None
    refusals: dict = field(default_factory=dict)

    def as_dict(self):
        return dict(self.__dict__)


def _reversals(q: np.ndarray, thr: float) -> list[int]:
    """Indices of turning points, with hysteresis `thr` (deg).

    Tracks the running extreme in the current direction; a turn is confirmed
    only once the trace moves `thr` back from it. Returns the extreme's index.
    """
    out: list[int] = []
    i_ext, direction = 0, 0
    for i in range(1, len(q)):
        if direction == 0:
            if abs(q[i] - q[0]) >= thr:
                direction = 1 if q[i] > q[0] else -1
                i_ext = i
            continue
        if (q[i] - q[i_ext]) * direction > 0:
            i_ext = i
        elif (q[i_ext] - q[i]) * direction >= thr:
            out.append(i_ext)
            direction, i_ext = -direction, i
    return out


def _release_index(q: np.ndarray, tol: float) -> int:
    """Last sample of the initial hold: the first sample that moved > tol, minus one."""
    moved = np.where(np.abs(q - q[0]) > tol)[0]
    return max(int(moved[0]) - 1, 0) if len(moved) else 0


def score_elbow(t: np.ndarray, q_deg: np.ndarray, *, J: float | None = None,
                k_gravity: float | None = None, swing_thr_deg: float = 1.0,
                settle_window_s: float = 1.0, settle_tol_deg: float = 0.5,
                fit_max_amp_deg: float = 15.0, fit_min_amp_deg: float = 0.5,
                fit_min_r2: float = 0.9, fit_max_amp_gravity_deg: float = 60.0) -> ElbowScore:
    """Score one elbow drop. `q_deg` is anatomical flexion, starting at or before release.

    `J` (kg.m^2, about the elbow) turns zeta and wn into B and K; without it
    those are refused. With both `J` and `k_gravity` the fit runs in GRAVITY mode
    (see the fit block): it assumes the rest is where the forearm hangs
    vertically, so gravity's torque about it is k_gravity*sin(x). `k_gravity` (N.m/rad) is gravity's linearised stiffness
    about the rest, m*g*l_c*cos(forearm angle from vertical at rest) -- for a
    forearm hanging vertically, m*g*l_c.
    """
    t = np.asarray(t, float); q = np.asarray(q_deg, float)
    if t.shape != q.shape or t.ndim != 1 or len(t) < 10:
        raise ValueError("t and q_deg must be equal-length 1-D arrays of at least 10 samples")
    if not np.all(np.isfinite(q)) or not np.all(np.isfinite(t)):
        raise ValueError("t or q_deg contains non-finite values")
    dt = float(np.median(np.diff(t)))
    sc = ElbowScore()
    n_win = max(5, int(round(settle_window_s / dt)))

    # ── noise floor, from the tail's second difference ────────────────────
    # A smooth swing has a tiny second difference at 1 kHz; white noise of
    # std s has a second difference of std s*sqrt(6). So this reads noise, not
    # motion, even on a tail that has not settled.
    tail = q[-n_win:]
    sc.noise_deg = float(np.std(np.diff(tail, 2)) / math.sqrt(6))
    noisy = sc.noise_deg * 4 > swing_thr_deg
    if noisy:
        sc.refusals["noise"] = (f"noise floor {sc.noise_deg:.3g} deg is too close to the swing threshold "
                                f"{swing_thr_deg} deg; every measure is withheld")
        return sc

    # ── release: drop any initial hold ────────────────────────────────────
    i0 = _release_index(q, tol=max(4 * sc.noise_deg, 1e-6))
    sc.release_index = i0
    t = t[i0:]; q = q[i0:]
    if len(q) < 20:
        sc.refusals["length"] = "too little trace after release"
        return sc

    # ── rest and settling: two consecutive windows, flat and agreeing ─────
    w2 = q[-n_win:]
    sc.rest_deg = float(np.median(w2))
    if len(q) < 2 * n_win:
        sc.settled = False                  # too short to show two quiet windows
    else:
        w1 = q[-2 * n_win:-n_win]
        sc.settled = bool(np.ptp(w2) <= 2 * settle_tol_deg
                          and abs(np.median(w1) - np.median(w2)) <= settle_tol_deg / 2)
    x = q - sc.rest_deg
    release_dist = q[0] - sc.rest_deg

    # ── swings ────────────────────────────────────────────────────────────
    sm_win = max(7, int(round(0.04 / dt)) | 1)
    qs = savgol_filter(q, sm_win, 3) if len(q) > sm_win else q
    rev = _reversals(qs, swing_thr_deg)
    big = [i for i in rev if abs(qs[i] - sc.rest_deg) > swing_thr_deg]
    sc.swings = len(big)
    if not sc.settled:
        sc.refusals["swings_final"] = "trace ends before it settles; count is a lower bound"

    # ── relaxation index (de Lima form) ───────────────────────────────────
    if not sc.settled:
        sc.refusals["ri"] = "unsettled: the rest, and so the release-to-rest distance, is unknown"
    elif abs(release_dist) < swing_thr_deg:
        sc.refusals["ri"] = "release is at the rest; nothing fell"
    elif not big or np.sign(qs[big[0]] - sc.rest_deg) == np.sign(release_dist):
        sc.refusals["ri"] = "no first swing past the rest"
    else:
        sc.ri = float((qs[0] - qs[big[0]]) / (qs[0] - sc.rest_deg))

    # ── log decrement over same-sign peaks ────────────────────────────────
    # same-sign peaks, each at least 2x the swing threshold: smaller ones are a
    # few quanta or noise floors tall and bias the decrement (0.5 deg quantisation: +4% K)
    same = [i for i in big if np.sign(qs[i] - sc.rest_deg) == np.sign(qs[big[0]] - sc.rest_deg)
            and abs(qs[i] - sc.rest_deg) >= 2 * swing_thr_deg] if big else []
    if not sc.settled:
        sc.refusals["logdec"] = "unsettled: peaks cannot be referred to the rest"
    elif len(same) < 2:
        sc.refusals["logdec"] = "fewer than two same-sign peaks (overdamped or one swing)"
    else:
        amps = np.abs(qs[same] - sc.rest_deg)
        if np.any(np.diff(amps) >= 0):
            sc.refusals["logdec"] = "peaks do not decay at every cycle; not a free damped swing"
        else:
            n = len(amps) - 1
            # slope of log amplitude on peak number: uses every peak, not two
            delta = -float(np.polyfit(np.arange(n + 1), np.log(amps), 1)[0])
            zeta = delta / math.sqrt(4 * math.pi ** 2 + delta ** 2)
            wd = 2 * math.pi * n / float(t[same[-1]] - t[same[0]])
            sc.zeta_logdec = zeta
            sc.wn_logdec = wd / math.sqrt(1 - zeta ** 2)

    # ── least-squares fit of the linear model, small amplitudes only ──────
    if not sc.settled:
        sc.refusals["fit"] = "unsettled: the rest is unknown"
    else:
        # Smoothing window ~1/10 of the swing period (from the reversal spacing:
        # successive reversals are half a period apart), clamped to 20-250 ms.
        # With too few reversals to measure a period the motion is heavily
        # damped, and its fast mode decays in tens of ms: a 100 ms window smears
        # it (B +15-45%, measured), so use 21 ms, or 41 ms on a noisy trace.
        if len(rev) >= 3:
            period = 2.0 * float(np.median(np.diff(t[rev])))
            w_s = min(max(period / 10, 0.02), 0.25)
        else:
            w_s = 0.041 if sc.noise_deg > 0.01 else 0.021
        win = max(7, int(round(w_s / dt)) | 1)
        if win >= len(x):
            win = max(7, (len(x) // 2) | 1)
        xs = np.radians(savgol_filter(x, win, 3))
        v = savgol_filter(x, win, 3, deriv=1, delta=dt) * math.pi / 180
        a = savgol_filter(x, win, 3, deriv=2, delta=dt) * math.pi / 180
        ax = np.abs(np.degrees(xs))
        # Two modes. With J and k_gravity known (they are, from the forearm model),
        # gravity's torque is subtracted in its true form, k_g*sin(x), so the fit
        # may use the large-amplitude start of the swing, where a heavily damped
        # limb's fast mode lives; only damping and the TISSUE stiffness are fitted.
        # Without them, only |x| <= fit_max_amp_deg is used (sin x ~ x), and a
        # heavily damped swing released from far out can be refused, because only
        # its slow mode is left by the time it is that close to the rest.
        gravity_mode = J is not None and k_gravity is not None
        sc.fit_mode = "gravity" if gravity_mode else "linear"
        amp_cap = fit_max_amp_gravity_deg if gravity_mode else fit_max_amp_deg
        mask = (ax <= amp_cap) & (ax > fit_min_amp_deg)
        mask[:win] = False; mask[len(x) - win:] = False
        if mask.sum() < max(3 * win, 50):
            sc.refusals["fit"] = "too little motion inside the fit range"
        else:
            A = np.column_stack([v[mask], xs[mask]])
            y = -a[mask]
            if gravity_mode:
                y = y - (k_gravity / J) * np.sin(xs[mask])   # remove gravity; fit B and K_tissue
            # Column-scaled condition number: a first-order (exponential) decay makes
            # velocity proportional to position, and lstsq would return an arbitrary
            # minimum-norm answer instead of failing.
            cond = float(np.linalg.cond(A / np.linalg.norm(A, axis=0)))
            coef, *_ = np.linalg.lstsq(A, y, rcond=None)
            b_j, k_j = float(coef[0]), float(coef[1])
            resid = y - A @ coef
            yy = -a[mask]
            sc.fit_r2 = float(1 - np.sum(resid ** 2) / np.sum((yy - yy.mean()) ** 2))
            if gravity_mode:
                kt_j = k_j                       # tissue stiffness / J; may be ~0
                k_j = kt_j + k_gravity / J       # total linearised stiffness about the rest
            if cond > 1e3:
                sc.refusals["fit"] = (f"velocity and position are collinear (cond {cond:.3g}): first-order "
                                      "motion, stiffness and damping cannot be separated")
            elif k_j <= 0 or b_j <= 0:
                sc.refusals["fit"] = f"fitted stiffness or damping not positive (K/J {k_j:.3g}, B/J {b_j:.3g})"
            elif sc.fit_r2 < fit_min_r2:
                sc.refusals["fit"] = f"linear model fits poorly (R^2 {sc.fit_r2:.3f} < {fit_min_r2})"
            elif b_j / (2 * math.sqrt(k_j)) > 5.0:
                sc.refusals["fit"] = (f"fitted damping ratio {b_j / (2 * math.sqrt(k_j)):.3g} > 5: "
                                      "too far from a swing to trust the split")
            else:
                sc.wn_fit = math.sqrt(k_j)
                sc.zeta_fit = b_j / (2 * sc.wn_fit)
                if gravity_mode:
                    sc.K_tissue_fit = kt_j * J   # fitted directly, not by subtraction

    # ── B and K from zeta, wn and J ───────────────────────────────────────
    if J is None:
        sc.refusals["B_K"] = "J not given"
    else:
        if sc.wn_logdec is not None:
            sc.K_logdec = J * sc.wn_logdec ** 2
            sc.B_logdec = 2 * sc.zeta_logdec * J * sc.wn_logdec
        if sc.wn_fit is not None:
            sc.K_fit = J * sc.wn_fit ** 2
            sc.B_fit = 2 * sc.zeta_fit * J * sc.wn_fit
        if k_gravity is not None:
            if sc.K_logdec is not None:
                sc.K_tissue_logdec = sc.K_logdec - k_gravity
            if sc.K_fit is not None and sc.K_tissue_fit is None:
                sc.K_tissue_fit = sc.K_fit - k_gravity
    return sc


__all__ = ["ElbowScore", "score_elbow"]
