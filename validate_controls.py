"""
validate_controls.py
====================
Reliability and validity of the Pendulastic annotation for the Wartenberg
Pendulum Test.  Healthy controls only (P1, P6–P12).

Metrics computed
----------------
  Concurrent validity  (Pendulastic annotation ↔ OptiTrack, paired per trial)
    ICC(2,1)   two-way mixed, single measure, absolute agreement
    Pearson r
    Bland-Altman: bias, SD of differences, 95% limits of agreement (LoA)
    SEM        Standard Error of Measurement
    SDC₉₅      Smallest Detectable Change at 95 % CI  =  SEM × 1.96 × √2

  Intra-session reliability  (Pendulastic only, across trials within each
                              participant-leg group that has ≥ 2 annotated trials)
    ICC(1,1)   one-way random effects
    SEM
    MDC₉₅      Minimum Detectable Change at 95 % CI  =  SEM × 1.96 × √2
                → answers "how much change is needed to exceed measurement noise?"

Applied to  PT score (7-param Popovic 2018)  and (separately) RMSE vs OptiTrack.

Usage
-----
  .venv\\Scripts\\python.exe validate_controls.py
"""
from __future__ import annotations
import csv
import os
import sys
from collections import defaultdict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy import stats

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from pendulastic_pt_score import (
    compute_pt_params, compute_pt_score, _detect_release,
)
from gen_figures import (
    load_annotation_csv, load_optitrack_safe, compute_rmse_xcorr, OUT_DIR,
)
from gen_fig_A_all_participants import _compute_pt_forced
from gen_fig_best_trials import (
    discover, opti_path as _opti_path, sibling_folder,
    CTRL_PARTICIPANTS,
)

STATS_OUT = os.path.join(BASE_DIR, "Model_Analysis_Outputs")
os.makedirs(STATS_OUT, exist_ok=True)


# ── ICC helpers ────────────────────────────────────────────────────────────────

def _icc_one_way(groups: list) -> dict:
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


def _icc_two_way(x: np.ndarray, y: np.ndarray) -> dict:
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


def _bland_altman(x: np.ndarray, y: np.ndarray) -> dict:
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


def _icc_label(v: float) -> str:
    if np.isnan(v): return "n/a"
    if v >= 0.90:   return "Excellent"
    if v >= 0.75:   return "Good"
    if v >= 0.50:   return "Moderate"
    return "Poor"


# ── Data collection ────────────────────────────────────────────────────────────

def collect() -> tuple[list, dict, dict]:
    """
    Returns
    -------
    paired_recs : list of dicts — one per trial with valid annotation AND OptiTrack
                  keys: pnum, side, trial_n, pt_opti, pt_ann, rmse
    ann_groups  : {(pnum, side): [pt_ann, ...]} — all annotated trials (reliability)
    rmse_groups : {(pnum, side): [rmse, ...]}   — for RMSE reliability
    """
    candidates  = discover()
    paired_recs = []
    ann_groups  = defaultdict(list)
    rmse_groups = defaultdict(list)
    seen        = set()   # deduplicate (pnum, side, trial_n)

    for pnum, folder, trial_n, ann_path, side in candidates:
        if pnum not in CTRL_PARTICIPANTS:
            continue
        uid = (pnum, side, trial_n)
        if uid in seen:
            continue          # skip duplicate annotation files for same trial

        # ── Annotation ──────────────────────────────────────────────────────
        t_ann, ang_ann = load_annotation_csv(ann_path)
        if t_ann is None or len(t_ann) < 20:
            continue
        rel_ann    = _detect_release(t_ann, ang_ann)
        params_ann = _compute_pt_forced(t_ann, ang_ann, rel_ann)
        if params_ann is None:
            continue
        pt_ann = compute_pt_score(params_ann)
        if not np.isfinite(pt_ann):
            continue

        # ── OptiTrack ───────────────────────────────────────────────────────
        model_fld  = sibling_folder(folder, side)
        op         = _opti_path(model_fld, trial_n) or _opti_path(folder, trial_n)
        has_opti   = op is not None

        t_ref = ang_ref = None
        if has_opti:
            t_ref, ang_ref = load_optitrack_safe(op)
            if t_ref is None or not np.isfinite(ang_ref).any():
                has_opti = False

        if has_opti:
            rel_ref    = _detect_release(t_ref, ang_ref)
            params_ref = compute_pt_params(t_ref, ang_ref)
            pt_opti    = compute_pt_score(params_ref) if params_ref else np.nan
            rmse, _    = compute_rmse_xcorr(
                t_ref, ang_ref, t_ann, ang_ann, rel_ref, rel_ann)

            if np.isfinite(pt_opti):
                seen.add(uid)
                paired_recs.append(dict(
                    pnum=pnum, side=side, trial_n=trial_n,
                    pt_opti=pt_opti, pt_ann=pt_ann, rmse=rmse,
                ))
                ann_groups[(pnum, side)].append(pt_ann)
                if np.isfinite(rmse):
                    rmse_groups[(pnum, side)].append(rmse)
                print(f"  P{pnum:2d} {side:<5} T{trial_n}  "
                      f"PT_opti={pt_opti:.3f}  PT_ann={pt_ann:.3f}  "
                      f"RMSE={rmse:.2f}°")
                continue

        # No valid OptiTrack — still count for reliability
        seen.add(uid)
        ann_groups[(pnum, side)].append(pt_ann)
        print(f"  P{pnum:2d} {side:<5} T{trial_n}  "
              f"PT_ann={pt_ann:.3f}  (no OptiTrack — reliability only)")

    return paired_recs, dict(ann_groups), dict(rmse_groups)


# ── Figure ─────────────────────────────────────────────────────────────────────

_PCOLORS = {6: "#2563EB", 7: "#059669", 8: "#D97706", 9: "#7C3AED",
            10: "#DC2626", 11: "#0891B2", 12: "#65A30D", 1: "#6B7280"}


def _make_figure(paired: list, ba: dict, icc2: dict, icc1: dict,
                 pearson_r: float, pearson_p: float,
                 ann_groups: dict):

    pt_opti = np.array([r["pt_opti"] for r in paired])
    pt_ann  = np.array([r["pt_ann"]  for r in paired])
    pnums   = [r["pnum"] for r in paired]

    fig = plt.figure(figsize=(15, 11))
    gs  = gridspec.GridSpec(2, 3, figure=fig,
                            height_ratios=[1.5, 1.0],
                            hspace=0.48, wspace=0.38)

    # ── Panel A: Scatter ──────────────────────────────────────────────────────
    ax_s = fig.add_subplot(gs[0, :2])
    ax_s.set_facecolor("#F8FAFC")

    for pv, av, pn in zip(pt_opti, pt_ann, pnums):
        ax_s.scatter(pv, av, color=_PCOLORS.get(pn, "#94A3B8"),
                     s=62, zorder=4, alpha=0.88, edgecolors="white", lw=0.5)

    lo = max(0.0, min(pt_opti.min(), pt_ann.min()) - 0.05)
    hi = max(pt_opti.max(), pt_ann.max()) + 0.10
    ax_s.plot([lo, hi], [lo, hi], "k--", lw=1.0, alpha=0.45,
              label="Identity  (x = y)", zorder=1)

    valid = np.isfinite(pt_opti) & np.isfinite(pt_ann)
    if valid.sum() >= 3:
        m, b = np.polyfit(pt_opti[valid], pt_ann[valid], 1)
        xr   = np.linspace(lo, hi, 120)
        ax_s.plot(xr, m * xr + b, color="#D97706", lw=1.6, zorder=2,
                  label=f"Fit:  y = {m:.2f}x + {b:.2f}")

    for pn in sorted(set(pnums)):
        ax_s.scatter([], [], color=_PCOLORS.get(pn, "#94A3B8"), s=55,
                     label=f"P{pn}", edgecolors="white", lw=0.5)

    r_star = ("***" if pearson_p < 0.001 else "**" if pearson_p < 0.01
              else "*" if pearson_p < 0.05 else "")
    icc_str = (f"ICC(2,1) = {icc2['icc']:.3f}  "
               f"[{icc2['ci_lo']:.2f}, {icc2['ci_hi']:.2f}]  "
               f"({_icc_label(icc2['icc'])})   "
               f"r = {pearson_r:.3f}{r_star}   n = {icc2['n']}")
    ax_s.set_title(
        "Concurrent Validity — Pendulastic Annotation vs OptiTrack  (PT score)\n" + icc_str,
        fontsize=10.5, pad=6)
    ax_s.set_xlabel("PT score — OptiTrack  (gold standard)", fontsize=10)
    ax_s.set_ylabel("PT score — Pendulastic Annotation", fontsize=10)
    ax_s.set_xlim(lo, hi)
    ax_s.set_ylim(lo, hi)
    ax_s.legend(loc="upper left", fontsize=8.0, ncol=2, framealpha=0.92)
    ax_s.grid(True, alpha=0.20, lw=0.6)

    # ── Panel B: Bland-Altman ─────────────────────────────────────────────────
    ax_b = fig.add_subplot(gs[0, 2])
    ax_b.set_facecolor("#F8FAFC")

    for mv, dv, pn in zip(ba["means"], ba["diffs"], pnums):
        ax_b.scatter(mv, dv, color=_PCOLORS.get(pn, "#94A3B8"),
                     s=55, zorder=4, alpha=0.88, edgecolors="white", lw=0.5)

    x_range = [float(ba["means"].min()) - 0.08, float(ba["means"].max()) + 0.08]
    ax_b.fill_between(x_range, ba["loa_lo"], ba["loa_hi"],
                      color="#BFDBFE", alpha=0.22, zorder=0)
    ax_b.axhline(ba["bias"],   color="#1D4ED8", lw=1.6, ls="-", zorder=3,
                 label=f"Bias {ba['bias']:+.3f}")
    ax_b.axhline(ba["loa_hi"], color="#DC2626", lw=1.2, ls="--", zorder=3,
                 label=f"95% LoA  [{ba['loa_lo']:+.3f}, {ba['loa_hi']:+.3f}]")
    ax_b.axhline(ba["loa_lo"], color="#DC2626", lw=1.2, ls="--", zorder=3)
    ax_b.axhline(0, color="#64748B", lw=0.7, ls=":", alpha=0.5, zorder=1)
    ax_b.set_xlim(x_range)
    ax_b.set_title("Bland-Altman Agreement\n(Annotation − OptiTrack)", fontsize=10)
    ax_b.set_xlabel("Mean  (Annotation + OptiTrack) / 2", fontsize=9)
    ax_b.set_ylabel("Difference  (Annotation − OptiTrack)", fontsize=9)
    ax_b.legend(loc="upper right", fontsize=8.0, framealpha=0.92)
    ax_b.grid(True, alpha=0.20, lw=0.6)

    # ── Panel C: Per-participant strip chart (reliability) ────────────────────
    ax_r = fig.add_subplot(gs[1, :2])
    ax_r.set_facecolor("#F8FAFC")

    keys = sorted(ann_groups.keys())
    x_tick_pos, x_tick_lbl = [], []
    for xi, key in enumerate(keys):
        pn, side = key
        vals = np.array(ann_groups[key])
        jitter = (np.random.default_rng(xi).random(len(vals)) - 0.5) * 0.18
        for v, j in zip(vals, jitter):
            ax_r.scatter(xi + j, v, color=_PCOLORS.get(pn, "#94A3B8"),
                         s=52, zorder=4, alpha=0.85, edgecolors="white", lw=0.4)
        ax_r.plot([xi] * len(vals), vals,
                  color=_PCOLORS.get(pn, "#94A3B8"), lw=0.8, alpha=0.35, zorder=2)
        x_tick_pos.append(xi)
        x_tick_lbl.append(f"P{pn}\n{side[0]}")

    ax_r.set_xticks(x_tick_pos)
    ax_r.set_xticklabels(x_tick_lbl, fontsize=8.5)
    ax_r.axhline(0.09, color="#64748B", lw=1.0, ls="--", alpha=0.60,
                 label="Healthy reference (0.09)")
    ax_r.set_title(
        f"Intra-session Trial-to-Trial Variability — Pendulastic Annotation\n"
        f"ICC(1,1) = {icc1['icc']:.3f}  [{icc1['ci_lo']:.2f}, {icc1['ci_hi']:.2f}]  "
        f"({_icc_label(icc1['icc'])})   "
        f"SEM = {icc1['sem']:.4f}   MDC₉₅ = {icc1['mdc95']:.4f} PT units",
        fontsize=10)
    ax_r.set_ylabel("PT score (7-param)", fontsize=9)
    ax_r.legend(loc="upper right", fontsize=8.0, framealpha=0.92)
    ax_r.grid(True, alpha=0.18, lw=0.6, axis="y")

    # ── Panel D: Summary text ─────────────────────────────────────────────────
    ax_t = fig.add_subplot(gs[1, 2])
    ax_t.axis("off")

    txt = (
        "SUMMARY STATISTICS\n"
        "─────────────────────────────────────\n"
        "Concurrent Validity\n"
        f"  Trials n         {icc2['n']}\n"
        f"  ICC(2,1)         {icc2['icc']:.3f}\n"
        f"  95% CI           [{icc2['ci_lo']:.2f}, {icc2['ci_hi']:.2f}]\n"
        f"  Interpretation   {_icc_label(icc2['icc'])}\n"
        f"  Pearson r        {pearson_r:.3f}  (p={pearson_p:.4f})\n"
        f"  SEM              {icc2['sem']:.4f} PT units\n"
        f"  SDC₉₅            {icc2['sdc95']:.4f} PT units\n"
        f"  Bias             {ba['bias']:+.4f} PT units\n"
        f"  95% LoA          [{ba['loa_lo']:+.3f}, {ba['loa_hi']:+.3f}]\n"
        "─────────────────────────────────────\n"
        "Intra-session Reliability\n"
        f"  Groups (n)       {icc1['n_subjects']}\n"
        f"  Observations     {icc1['n_obs']}\n"
        f"  ICC(1,1)         {icc1['icc']:.3f}\n"
        f"  95% CI           [{icc1['ci_lo']:.2f}, {icc1['ci_hi']:.2f}]\n"
        f"  Interpretation   {_icc_label(icc1['icc'])}\n"
        f"  SEM              {icc1['sem']:.4f} PT units\n"
        f"  MDC₉₅            {icc1['mdc95']:.4f} PT units\n"
        "─────────────────────────────────────\n"
        "MDC₉₅ = SEM × 1.96 × √2\n"
        "SDC₉₅ = SEM × 1.96 × √2\n"
        "(Shrout & Fleiss 1979;\n"
        " Koo & Mae 2016)"
    )
    ax_t.text(0.04, 0.97, txt, transform=ax_t.transAxes,
              fontsize=8.5, va="top", ha="left", family="monospace",
              bbox=dict(boxstyle="round,pad=0.55", facecolor="#F0F9FF",
                        edgecolor="#0284C7", alpha=0.96, lw=1.0))

    fig.suptitle(
        "Pendulastic Annotation — Reliability & Validity\n"
        "Healthy Controls  (P1, P6–P12)  |  7-param Popovic 2018 PT Score",
        fontsize=12.5, fontweight="bold", y=1.005)

    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUT_DIR, f"Fig_control_validation.{ext}"),
                    dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: Fig_control_validation.png / .pdf")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Collecting control annotation data…\n")
    paired, ann_grps, rmse_grps = collect()

    n_paired = len(paired)
    print(f"\n  {n_paired} paired trials  |  "
          f"{sum(len(v) for v in ann_grps.values())} total annotation observations  |  "
          f"{len(ann_grps)} participant-side groups")

    if n_paired < 3:
        print("[ERROR] Not enough paired data (need ≥ 3 trials with both sources).")
        return

    pt_opti = np.array([r["pt_opti"] for r in paired])
    pt_ann  = np.array([r["pt_ann"]  for r in paired])
    rmses   = np.array([r["rmse"]    for r in paired])

    # ── Concurrent validity ───────────────────────────────────────────────────
    icc2 = _icc_two_way(pt_opti, pt_ann)
    ba   = _bland_altman(pt_opti, pt_ann)
    valid = np.isfinite(pt_opti) & np.isfinite(pt_ann)
    pr, pp = (stats.pearsonr(pt_opti[valid], pt_ann[valid])
              if valid.sum() >= 3 else (np.nan, np.nan))

    # Also compute for RMSE (secondary outcome)
    rmse_opti_med = np.nanmedian(rmses)

    # ── Intra-session reliability (PT score) ──────────────────────────────────
    rel_groups = [v for v in ann_grps.values() if len(v) >= 2]
    icc1 = _icc_one_way(rel_groups)

    # ── Intra-session reliability (RMSE) ─────────────────────────────────────
    rmse_rel_groups = [v for v in rmse_grps.values() if len(v) >= 2]
    icc1_rmse = _icc_one_way(rmse_rel_groups)

    # ── Print ─────────────────────────────────────────────────────────────────
    W = 68
    print(f"\n{'='*W}")
    print("  CONCURRENT VALIDITY  —  Pendulastic Annotation vs OptiTrack PT Score")
    print(f"{'='*W}")
    print(f"  n paired trials               {icc2['n']}")
    print(f"  ICC(2,1)                      {icc2['icc']:.3f}  "
          f"[{icc2['ci_lo']:.2f}, {icc2['ci_hi']:.2f}]  "
          f"({_icc_label(icc2['icc'])})")
    print(f"  Pearson r                     {pr:.3f}  (p = {pp:.4f})")
    print(f"  SEM (concurrent)              {icc2['sem']:.4f} PT units")
    print(f"  SDC₉₅ (concurrent)            {icc2['sdc95']:.4f} PT units")
    print(f"  Bland-Altman bias             {ba['bias']:+.4f} PT units")
    print(f"  95% LoA                       [{ba['loa_lo']:+.4f}, {ba['loa_hi']:+.4f}] PT units")
    print(f"  Angle RMSE  median / range    {rmse_opti_med:.1f}° / "
          f"{np.nanmin(rmses):.1f}°–{np.nanmax(rmses):.1f}°")

    print(f"\n{'='*W}")
    print("  INTRA-SESSION RELIABILITY  —  Pendulastic Annotation, PT Score")
    print(f"{'='*W}")
    print(f"  Participant-side groups (≥2 trials)  {icc1['n_subjects']}")
    print(f"  Total observations                   {icc1['n_obs']}")
    print(f"  ICC(1,1)                      {icc1['icc']:.3f}  "
          f"[{icc1['ci_lo']:.2f}, {icc1['ci_hi']:.2f}]  "
          f"({_icc_label(icc1['icc'])})")
    print(f"  SEM                           {icc1['sem']:.4f} PT units")
    print(f"  MDC₉₅  (min. real change)     {icc1['mdc95']:.4f} PT units")

    print(f"\n{'='*W}")
    print("  INTRA-SESSION RELIABILITY  —  Pendulastic Annotation, RMSE vs OptiTrack")
    print(f"{'='*W}")
    print(f"  Participant-side groups (≥2 trials)  {icc1_rmse['n_subjects']}")
    print(f"  Total observations                   {icc1_rmse['n_obs']}")
    print(f"  ICC(1,1)                      {icc1_rmse['icc']:.3f}  "
          f"[{icc1_rmse['ci_lo']:.2f}, {icc1_rmse['ci_hi']:.2f}]  "
          f"({_icc_label(icc1_rmse['icc'])})")
    print(f"  SEM (RMSE)                    {icc1_rmse['sem']:.3f}°")
    print(f"  MDC₉₅ (RMSE)                  {icc1_rmse['mdc95']:.3f}°")

    print(f"\n{'='*W}")
    print("  PER-TRIAL BREAKDOWN")
    print(f"{'='*W}")
    print(f"  {'P':>3}  {'Side':<6} {'T':>2}  "
          f"{'PT_opti':>8}  {'PT_ann':>8}  {'Diff':>7}  {'RMSE°':>6}")
    print(f"  {'─'*50}")
    for r in sorted(paired, key=lambda x: (x["pnum"], x["side"], x["trial_n"])):
        d = r["pt_ann"] - r["pt_opti"]
        print(f"  {r['pnum']:>3}  {r['side']:<6} {r['trial_n']:>2}  "
              f"{r['pt_opti']:>8.3f}  {r['pt_ann']:>8.3f}  {d:>+7.3f}  "
              f"{r['rmse']:>6.2f}")

    print(f"\n  → MDC₉₅ = SEM × 1.96 × √2  =  {icc1['mdc95']:.4f} PT units")
    print( "    A change in PT score larger than this threshold can be considered")
    print( "    a real change with 95% confidence (not attributable to measurement error).")

    # ── Figure ────────────────────────────────────────────────────────────────
    print("\nGenerating figure…")
    _make_figure(paired, ba, icc2, icc1, float(pr), float(pp), ann_grps)

    # ── Save CSVs ─────────────────────────────────────────────────────────────
    out_trials = os.path.join(STATS_OUT, "control_validation_trials.csv")
    with open(out_trials, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pnum", "side", "trial_n",
                    "pt_opti", "pt_ann", "diff", "rmse_deg"])
        for r in sorted(paired, key=lambda x: (x["pnum"], x["side"], x["trial_n"])):
            w.writerow([r["pnum"], r["side"], r["trial_n"],
                        round(r["pt_opti"], 4), round(r["pt_ann"], 4),
                        round(r["pt_ann"] - r["pt_opti"], 4),
                        round(r["rmse"], 3)])

    out_stats = os.path.join(STATS_OUT, "control_validation_stats.csv")
    with open(out_stats, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value", "unit", "note"])
        def row(m, v="", u="", n=""):
            w.writerow([m, v, u, n])
        row("analysis_variable", "PT score (7-param Popovic 2018)")
        row("controls", "P1, P6-P12")
        row("", "")
        row("=== CONCURRENT VALIDITY (Pendulastic vs OptiTrack) ===")
        row("n_paired_trials",  icc2["n"])
        row("ICC_2_1",          round(icc2["icc"],  4), "",   _icc_label(icc2["icc"]))
        row("ICC_2_1_CI_lo",    round(icc2["ci_lo"],4))
        row("ICC_2_1_CI_hi",    round(icc2["ci_hi"],4))
        row("Pearson_r",        round(float(pr), 4))
        row("Pearson_p",        round(float(pp), 4))
        row("SEM_concurrent",   round(icc2["sem"],  4), "PT units")
        row("SDC95_concurrent", round(icc2["sdc95"],4), "PT units",
            "min detectable change at 95% CI (concurrent validity)")
        row("BA_bias",          round(ba["bias"],   4), "PT units")
        row("BA_LoA_lo",        round(ba["loa_lo"], 4), "PT units")
        row("BA_LoA_hi",        round(ba["loa_hi"], 4), "PT units")
        row("RMSE_median_deg",  round(float(np.nanmedian(rmses)), 2), "degrees")
        row("", "")
        row("=== INTRA-SESSION RELIABILITY (Pendulastic, PT score) ===")
        row("n_participant_side_groups", icc1["n_subjects"])
        row("n_observations",   icc1["n_obs"])
        row("ICC_1_1",          round(icc1["icc"],  4), "",   _icc_label(icc1["icc"]))
        row("ICC_1_1_CI_lo",    round(icc1["ci_lo"],4))
        row("ICC_1_1_CI_hi",    round(icc1["ci_hi"],4))
        row("SEM_reliability",  round(icc1["sem"],  4), "PT units")
        row("MDC95",            round(icc1["mdc95"],4), "PT units",
            "smallest real change at 95% CI (= SEM x 1.96 x sqrt(2))")
        row("", "")
        row("=== INTRA-SESSION RELIABILITY (Pendulastic, RMSE vs OptiTrack) ===")
        row("n_participant_side_groups_rmse", icc1_rmse["n_subjects"])
        row("n_observations_rmse",   icc1_rmse["n_obs"])
        row("ICC_1_1_rmse",    round(icc1_rmse["icc"],  4), "", _icc_label(icc1_rmse["icc"]))
        row("SEM_rmse",        round(icc1_rmse["sem"],  4), "degrees")
        row("MDC95_rmse",      round(icc1_rmse["mdc95"],4), "degrees",
            "smallest real change in RMSE at 95% CI")

    print(f"  Saved: control_validation_trials.csv")
    print(f"  Saved: control_validation_stats.csv")


if __name__ == "__main__":
    main()
