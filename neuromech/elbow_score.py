"""Score an elbow pendulum trace the way the elbow literature does. Never a grade.

Plan: docs/plans/2026-11-10-001-plan-neuromech-elbow-emulator.md, unit U4.

Measures (raw values, each with the estimator that produced it):

  swings     extrema after release whose excursion from the rest exceeds
             `swing_thr_deg`. OUR definition: the Lin-group counting rule is in
             the paywalled papers and replaces this once read.
  RI         relaxation index, de Lima et al. 2023's form: first-swing
             excursion over the release-to-rest distance (no 1.6, unlike the
             knee's R2). Withheld when the trace has not settled.
  zeta, wn   by LOG DECREMENT over successive same-sign peaks about the rest
             (Valle et al. 2025; de Lima), and by LEAST-SQUARES FIT of the linear
             model  J*q'' + B*q' + K*(q - q_rest) = 0  on the free swing (Lin 2003).
  B, K       from zeta and wn when J is given: K = J*wn**2 (correcting the
             review's printed K = J*wn), B = 2*zeta*J*wn. K includes gravity's
             stiffness unless `k_gravity` is given, in which case `K_tissue`
             is reported as well -- the two published K scales differ on exactly
             this.

A result carries `refusals`: a dict of measure -> reason for every measure it
declined to give. There is no PT score and no MAS band anywhere in this module
(plan E5: the elbow literature offers no bands to calibrate to).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class ElbowScore:
    rest_deg: float | None = None
    settled: bool = False
    swings: int | None = None
    ri: float | None = None
    zeta_logdec: float | None = None
    wn_logdec: float | None = None
    zeta_fit: float | None = None
    wn_fit: float | None = None
    B_logdec: float | None = None
    K_logdec: float | None = None
    B_fit: float | None = None
    K_fit: float | None = None
    K_tissue_logdec: float | None = None
    K_tissue_fit: float | None = None
    refusals: dict = field(default_factory=dict)

    def as_dict(self):
        return dict(self.__dict__)


def _extrema(x: np.ndarray, i0: int = 1):
    """Indices of strict local maxima and minima of x (plateaus take the first sample)."""
    d = np.diff(x)
    s = np.sign(d)
    # carry the last nonzero slope through flats so a flat top counts once
    for i in range(1, len(s)):
        if s[i] == 0:
            s[i] = s[i - 1]
    turn = np.where(s[1:] != s[:-1])[0] + 1
    return turn[turn >= i0]


def score_elbow(t: np.ndarray, q_deg: np.ndarray, *, J: float | None = None,
                k_gravity: float | None = None, swing_thr_deg: float = 1.0,
                settle_window_s: float = 1.0, settle_tol_deg: float = 0.5,
                fit_until_amp_deg: float = 0.5) -> ElbowScore:
    """Score one elbow drop. `q_deg` is anatomical flexion from release (index 0) onward.

    `J` (kg.m^2, about the elbow) turns zeta and wn into B and K; without it
    those are refused. `k_gravity` (N.m/rad) is gravity's linearised stiffness
    about the rest, m*g*l_c*cos(forearm angle from vertical at rest) -- for a
    forearm hanging vertically, m*g*l_c.
    """
    t = np.asarray(t, float); q = np.asarray(q_deg, float)
    if t.shape != q.shape or t.ndim != 1 or len(t) < 10:
        raise ValueError("t and q_deg must be equal-length 1-D arrays of at least 10 samples")
    if not np.all(np.isfinite(q)):
        raise ValueError("q_deg contains non-finite values")
    dt = float(np.median(np.diff(t)))
    sc = ElbowScore()

    # ── rest and settling ─────────────────────────────────────────────────
    n_win = max(2, int(round(settle_window_s / dt)))
    tail = q[-n_win:]
    sc.rest_deg = float(np.median(tail))
    sc.settled = bool(np.ptp(tail) <= 2 * settle_tol_deg)
    x = q - sc.rest_deg
    release_dist = q[0] - sc.rest_deg

    # ── swings ────────────────────────────────────────────────────────────
    ext = _extrema(q)
    big = [i for i in ext if abs(x[i]) > swing_thr_deg]
    sc.swings = len(big)
    if not sc.settled:
        sc.refusals["swings_final"] = "trace ends before it settles; count is a lower bound"

    # ── relaxation index (de Lima form) ───────────────────────────────────
    if not sc.settled:
        sc.refusals["ri"] = "unsettled: the rest, and so the release-to-rest distance, is unknown"
    elif abs(release_dist) < swing_thr_deg:
        sc.refusals["ri"] = "release is at the rest; nothing fell"
    elif not big:
        sc.refusals["ri"] = "no first swing"
    else:
        sc.ri = float((q[0] - q[big[0]]) / release_dist)

    # ── log decrement over same-sign peaks ────────────────────────────────
    same = [i for i in big if np.sign(x[i]) == np.sign(x[big[0]])] if big else []
    if len(same) >= 2 and sc.settled:
        amps = np.abs(x[same])
        n = len(amps) - 1
        delta = math.log(amps[0] / amps[-1]) / n
        zeta = delta / math.sqrt(4 * math.pi ** 2 + delta ** 2)
        Td = float(t[same[-1]] - t[same[0]]) / n
        wd = 2 * math.pi / Td
        sc.zeta_logdec = zeta
        sc.wn_logdec = wd / math.sqrt(1 - zeta ** 2)
    else:
        sc.refusals["logdec"] = ("unsettled: peaks cannot be referred to the rest" if not sc.settled
                                 else "fewer than two same-sign peaks (overdamped or one swing)")

    # ── least-squares fit of the linear model ─────────────────────────────
    # q'' = -(B/J) q' - (K/J) (q - rest), on the free swing: from release until
    # the excursion stays under fit_until_amp_deg, so the soft range walls and
    # the dead tail do not dominate.
    if sc.settled:
        above = np.where(np.abs(x) > fit_until_amp_deg)[0]
        i_end = int(above[-1]) + 1 if len(above) else 0
        if i_end < 20:
            sc.refusals["fit"] = "too little motion to fit"
        else:
            xr = np.radians(x[: i_end + 2])
            v = np.gradient(xr, dt)
            a = np.gradient(v, dt)
            sl = slice(2, i_end)      # gradient's one-sided ends are least accurate
            A = np.column_stack([v[sl], xr[sl]])
            coef, *_ = np.linalg.lstsq(A, -a[sl], rcond=None)
            b_j, k_j = float(coef[0]), float(coef[1])
            if k_j <= 0:
                sc.refusals["fit"] = f"fitted stiffness is not positive (K/J = {k_j:.3g})"
            else:
                sc.wn_fit = math.sqrt(k_j)
                sc.zeta_fit = b_j / (2 * sc.wn_fit)
    else:
        sc.refusals["fit"] = "unsettled: the rest is unknown"

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
            if sc.K_fit is not None:
                sc.K_tissue_fit = sc.K_fit - k_gravity
    return sc


__all__ = ["ElbowScore", "score_elbow"]
