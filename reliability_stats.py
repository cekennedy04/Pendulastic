"""
reliability_stats.py
=====================
Concurrent-validity and reliability statistics (Bland-Altman, ICC), extracted
from validate_controls.py so they can be reused without importing that file
-- which cannot currently run: three of its own module-level imports
(gen_figures.py, gen_fig_best_trials.py, gen_fig_A_all_participants.py) do
not exist anywhere in the repo. These three functions have no dependency on
those missing files; this module exists purely to make them importable
again. See docs/superpowers/specs/2026-08-04-imu-stillness-gyro-bias-design.md
Section 5.
"""
from __future__ import annotations

import numpy as np
from scipy import stats


def icc_one_way(groups: list) -> dict:
    """
    ICC(1,1): one-way random effects model.
    groups: list of per-subject arrays, each with ≥ 2 observations.
    Returns dict: icc, ci_lo, ci_hi, sem, mdc95, n_subjects, n_obs
    """
    groups = [np.asarray(g, float) for g in groups if len(g) >= 2]
    n_s = len(groups)
    if n_s < 2:
        return dict(icc=np.nan, ci_lo=np.nan, ci_hi=np.nan,
                    sem=np.nan, mdc95=np.nan, n_subjects=n_s, n_obs=0)

    all_v  = np.concatenate(groups)
    N      = len(all_v)
    grand  = np.mean(all_v)

    SS_b = float(sum(len(g) * (np.mean(g) - grand) ** 2 for g in groups))
    SS_w = float(sum(np.sum((g - np.mean(g)) ** 2) for g in groups))
    df_b, df_w = n_s - 1, N - n_s

    if df_b <= 0 or df_w <= 0:
        return dict(icc=np.nan, ci_lo=np.nan, ci_hi=np.nan,
                    sem=np.nan, mdc95=np.nan, n_subjects=n_s, n_obs=N)

    MS_b = SS_b / df_b
    MS_w = SS_w / df_w

    # k₀: effective number of replications (harmonic correction, Shrout & Fleiss 1979)
    k0 = (N - sum(len(g) ** 2 for g in groups) / N) / (n_s - 1)

    denom   = MS_b + (k0 - 1) * MS_w
    icc_val = max(0.0, (MS_b - MS_w) / denom) if denom > 0 else np.nan

    # 95 % CI via F-distribution
    F0  = MS_b / (MS_w + 1e-12)
    F_L = F0 / stats.f.ppf(0.975, df_b, df_w)
    F_U = F0 * stats.f.ppf(0.975, df_b, df_w)
    ci_l = max(0.0, (F_L - 1) / (F_L + k0 - 1))
    ci_u = min(1.0, (F_U - 1) / (F_U + k0 - 1))

    sem   = float(np.sqrt(MS_w))
    mdc95 = float(sem * 1.96 * np.sqrt(2))

    return dict(icc=float(icc_val), ci_lo=float(ci_l), ci_hi=float(ci_u),
                sem=sem, mdc95=mdc95, n_subjects=n_s, n_obs=N)


def icc_two_way(x: np.ndarray, y: np.ndarray) -> dict:
    """
    ICC(2,1): two-way mixed, absolute agreement (concurrent validity).
    x = reference (OptiTrack), y = test (Pendulastic annotation), paired.
    Returns dict: icc, ci_lo, ci_hi, sem, sdc95, n
    """
    x, y = np.asarray(x, float), np.asarray(y, float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    n = len(x)
    if n < 3:
        return dict(icc=np.nan, ci_lo=np.nan, ci_hi=np.nan,
                    sem=np.nan, sdc95=np.nan, n=n)

    k = 2
    data      = np.column_stack([x, y])
    grand     = data.mean()
    row_means = data.mean(axis=1)
    col_means = data.mean(axis=0)

    SS_rows  = k * np.sum((row_means - grand) ** 2)
    SS_cols  = n * np.sum((col_means - grand) ** 2)
    SS_total = np.sum((data - grand) ** 2)
    SS_err   = SS_total - SS_rows - SS_cols

    df_r, df_c, df_e = n - 1, k - 1, (n - 1) * (k - 1)
    MS_r = SS_rows / max(df_r, 1)
    MS_c = SS_cols / max(df_c, 1)
    MS_e = SS_err  / max(df_e, 1)

    denom   = MS_r + (k - 1) * MS_e + (k / n) * (MS_c - MS_e)
    icc_val = max(0.0, (MS_r - MS_e) / denom) if denom > 0 else np.nan

    # 95 % CI
    F0  = MS_r / (MS_e + 1e-12)
    F_L = F0 / stats.f.ppf(0.975, df_r, df_e)
    F_U = F0 * stats.f.ppf(0.975, df_r, df_e)
    ci_l = max(0.0, (F_L - 1) / (F_L + k - 1))
    ci_u = min(1.0, (F_U - 1) / (F_U + k - 1))

    sem   = float(np.sqrt(MS_e))
    sdc95 = float(sem * 1.96 * np.sqrt(2))

    return dict(icc=float(icc_val), ci_lo=float(ci_l), ci_hi=float(ci_u),
                sem=sem, sdc95=sdc95, n=n)


def bland_altman(x: np.ndarray, y: np.ndarray) -> dict:
    x, y = np.asarray(x, float), np.asarray(y, float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y  = x[mask], y[mask]
    diffs = y - x
    means = (x + y) / 2.0
    bias  = float(np.mean(diffs))
    sd    = float(np.std(diffs, ddof=1))
    return dict(bias=bias, sd=sd,
                loa_lo=bias - 1.96 * sd, loa_hi=bias + 1.96 * sd,
                means=means, diffs=diffs)
