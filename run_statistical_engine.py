"""
run_statistical_engine.py
==========================
Validation analysis for the Pendulastic case study (P5).
Goal: show Pendulastic detects the same parameter changes as OptiTrack.

Two-part analysis:
  Part 1 — Concurrent validity (pre-training):
      ICC(2,1) + Bland-Altman between OptiTrack and Pendulastic Viewer
      for each parameter (uses viewer_vs_optitrack.csv).

  Part 2 — Sensitivity to change (pre vs post):
      Wilcoxon on OptiTrack pre/post (both legs).
      Pendulastic post-training sessions are flagged as PENDING.

Run:  .venv\Scripts\python.exe run_statistical_engine.py
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
from scipy import stats

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

VIEWER_CSV = os.path.join(BASE_DIR, "Model_Analysis_Outputs",
                          "Viewer_Comparison", "viewer_vs_optitrack.csv")
OPTI_CSV   = os.path.join(BASE_DIR, "Model_Analysis_Outputs",
                          "PT_Scores", "pendulum_test_scores.csv")
OUT_DIR    = os.path.join(BASE_DIR, "Model_Analysis_Outputs")
os.makedirs(OUT_DIR, exist_ok=True)

# Column mapping: viewer_vs_optitrack.csv name -> display label
CONCURRENT_PARAMS = {
    "phi":   ("phi_max_ratio",  "phi_max_ratio",   "First-swing ratio"),
    "omega": ("omega_max_n",    "omega_max_n",      "Norm. peak velocity"),
    "N":     ("N",              "N",                "Oscillation cycles"),
    "R2n":   ("R2n",            "R2n",              "Amplitude decay R2n"),
    "pt4":   ("pt_score_4param","pt_score_4param",  "PT score (4-param)"),
}

CHANGE_PARAMS = [
    ("phi_max_ratio",  "First-swing ratio",          0.787),
    ("omega_max_n",    "Norm. peak velocity",         5.692),
    ("area_ratio",     "Extension area ratio",        0.09),
    ("pt_score_4param","PT score (4-param)",          0.09),
    ("N",              "Oscillation cycles",          7.0),
]

HEALTHY_REF = {p[0]: p[2] for p in CHANGE_PARAMS}


# ─── ICC(2,1) — two-way mixed, single measures ───────────────────────────────

def icc_2_1(y1: np.ndarray, y2: np.ndarray) -> tuple[float, float, float]:
    """
    ICC(2,1): two-way random effects, single measures, absolute agreement.
    Returns (icc, lower_95ci, upper_95ci).
    """
    n  = len(y1)
    if n < 3:
        return float("nan"), float("nan"), float("nan")
    grand = np.mean([y1, y2])
    SS_r  = n * np.sum((np.array([np.mean(y1), np.mean(y2)]) - grand) ** 2)
    SS_w  = np.sum((y1 - np.mean(y1)) ** 2) + np.sum((y2 - np.mean(y2)) ** 2)
    SS_e  = SS_w - SS_r
    MS_b  = np.var(np.mean([y1, y2], axis=0), ddof=1) * 2 * (2 * n - 1) / (n - 1)
    diff  = y1 - y2
    SS_e2 = np.sum((diff - np.mean(diff)) ** 2) / 2
    # Standard ICC(2,1) via one-way ANOVA approach (simplified for k=2 raters)
    xmat      = np.column_stack([y1, y2])
    row_means = xmat.mean(axis=1)
    col_means = xmat.mean(axis=0)
    grand_m   = xmat.mean()
    ss_rows   = 2 * np.sum((row_means - grand_m) ** 2)
    ss_cols   = n * np.sum((col_means - grand_m) ** 2)
    ss_total  = np.sum((xmat - grand_m) ** 2)
    ss_err    = ss_total - ss_rows - ss_cols
    ms_rows   = ss_rows / (n - 1)
    ms_err    = ss_err / ((n - 1) * 1)   # (n-1)(k-1), k=2
    ms_cols   = ss_cols / 1
    icc_val   = (ms_rows - ms_err) / (ms_rows + ms_err + (2 / n) * (ms_cols - ms_err))
    # 95% CI via F-distribution
    F_L = (ms_rows / ms_err) / stats.f.ppf(0.975, n - 1, n - 1)
    F_U = (ms_rows / ms_err) * stats.f.ppf(0.975, n - 1, n - 1)
    ci_l = (F_L - 1) / (F_L + 1)
    ci_u = (F_U - 1) / (F_U + 1)
    return float(icc_val), float(ci_l), float(ci_u)


def icc_interp(icc: float) -> str:
    if np.isnan(icc):
        return "n/a"
    if icc >= 0.90: return "Excellent"
    if icc >= 0.75: return "Good"
    if icc >= 0.50: return "Moderate"
    return "Poor"


# ─── Part 1: Concurrent validity ─────────────────────────────────────────────

def part1_concurrent(viewer_df: pd.DataFrame) -> pd.DataFrame:
    # Filter to P5 only
    p5 = viewer_df[viewer_df["participant"] == 5].copy()
    print(f"\n{'='*65}")
    print("  PART 1 — CONCURRENT VALIDITY (Pre-training, n trials = %d)" % len(p5))
    print(f"  OptiTrack vs Pendulastic Viewer, P5 pre-training sessions")
    print(f"{'='*65}")

    rows = []
    for key, (opti_col, view_col, label) in CONCURRENT_PARAMS.items():
        oc = f"opti_{key}"
        vc = f"viewer_{key}"
        if oc not in p5.columns or vc not in p5.columns:
            continue
        paired = p5[[oc, vc]].dropna()
        if len(paired) < 3:
            continue
        y_o = paired[oc].values.astype(float)
        y_v = paired[vc].values.astype(float)

        icc, ci_l, ci_u = icc_2_1(y_o, y_v)
        r, p_r = stats.pearsonr(y_o, y_v)
        mae    = float(np.mean(np.abs(y_o - y_v)))
        bias   = float(np.mean(y_v - y_o))          # Bland-Altman mean bias
        loa    = 1.96 * float(np.std(y_v - y_o))    # limits of agreement (±)

        rows.append({
            "Parameter":           label,
            "n":                   len(paired),
            "OptiTrack med":       round(float(np.median(y_o)), 3),
            "Pendulastic med":     round(float(np.median(y_v)), 3),
            "ICC(2,1)":            round(icc,  3) if not np.isnan(icc) else "n/a",
            "ICC 95% CI":          f"[{ci_l:.2f}, {ci_u:.2f}]" if not np.isnan(ci_l) else "n/a",
            "ICC interp":          icc_interp(icc),
            "Pearson r":           round(r,    3),
            "p (Pearson)":         round(p_r,  4),
            "MAE":                 round(mae,  4),
            "Bias (mean)":         round(bias, 4),
            "LoA (+/-)":           round(loa,  4),
        })
        print(f"  {label:<35}  ICC={icc:.3f} [{ci_l:.2f},{ci_u:.2f}]  "
              f"{icc_interp(icc):<10}  r={r:.3f}  MAE={mae:.4f}")

    print("\n  Note: area_ratio not yet in sessions.json viewer output —"
          " add to Pendulastic app export to complete validation.")
    return pd.DataFrame(rows)


# ─── Part 2: Sensitivity to change (OptiTrack pre vs post) ───────────────────

def _effect_r(w: float, n: int) -> float:
    max_w = n * (n + 1) / 2
    return float((w - max_w / 2) / (max_w / 2)) if max_w else float("nan")


def part2_change(opti_df: pd.DataFrame) -> pd.DataFrame:
    print(f"\n{'='*65}")
    print("  PART 2 — SENSITIVITY TO CHANGE  (OptiTrack, P5)")
    print(f"{'='*65}")

    results = []
    for leg in ["left", "right"]:
        pre_df  = opti_df[opti_df["pid"] == f"5_{leg}_side"].copy()
        post_df = opti_df[opti_df["pid"] == f"5_{leg}_post"].copy()

        merged = pd.merge(pre_df, post_df, on="trial", suffixes=("_Pre", "_Post"))
        n = len(merged)
        print(f"\n  {leg.upper()} LEG  —  {n} matched trials"
              + ("  [note: n<6, limited power]" if n < 6 else ""))

        for col, label, ref in CHANGE_PARAMS:
            pc, qc = f"{col}_Pre", f"{col}_Post"
            if pc not in merged or qc not in merged:
                continue
            pre_v  = merged[pc].dropna().values.astype(float)
            post_v = merged[qc].dropna().values.astype(float)
            mask   = ~np.isnan(pre_v) & ~np.isnan(post_v)
            pv, qv = pre_v[mask], post_v[mask]
            if len(pv) < 3:
                continue

            med_pre  = float(np.median(pv))
            med_post = float(np.median(qv))
            iqr_pre  = float(stats.iqr(pv))
            iqr_post = float(stats.iqr(qv))
            pct      = 100 * (med_post - med_pre) / abs(med_pre) if med_pre else float("nan")

            try:
                w_stat, p_val = stats.wilcoxon(pv, qv, zero_method="pratt")
                r_eff = _effect_r(w_stat, len(pv))
            except ValueError:
                w_stat, p_val, r_eff = float("nan"), float("nan"), float("nan")

            results.append({
                "Leg":                 leg.capitalize(),
                "Parameter":           label,
                "n_pairs":             len(pv),
                "Pre med [IQR]":       f"{med_pre:.3f} [{iqr_pre:.3f}]",
                "Post med [IQR]":      f"{med_post:.3f} [{iqr_post:.3f}]",
                "Change%":             f"{pct:+.1f}%",
                "Healthy ref":         ref,
                "W":                   round(w_stat, 1) if not np.isnan(w_stat) else "n/a",
                "p":                   round(p_val,  4) if not np.isnan(p_val)  else "n/a",
                "r_effect":            round(r_eff,  3) if not np.isnan(r_eff)  else "n/a",
            })
            print(f"  {label:<35}  Pre={med_pre:.3f} -> Post={med_post:.3f}"
                  f"  ({pct:+.1f}%)  r={r_eff:.2f}")

    print("\n  [PENDING] Pendulastic post-training sessions not yet in sessions.json.")
    print("  -> Capture post-training sessions in the Pendulastic app,")
    print("     then re-run to complete the OptiTrack vs Pendulastic change-detection comparison.")
    return pd.DataFrame(results)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    viewer_df = pd.read_csv(VIEWER_CSV)
    opti_df   = pd.read_csv(OPTI_CSV)

    part1_df = part1_concurrent(viewer_df)
    part2_df = part2_change(opti_df)

    part1_df.to_csv(os.path.join(OUT_DIR, "P5_concurrent_validity.csv"), index=False)
    part2_df.to_csv(os.path.join(OUT_DIR, "P5_wilcoxon_optitrack.csv"), index=False)

    print("\n\n=== CONCURRENT VALIDITY TABLE ===")
    pd.set_option("display.max_colwidth", 20)
    pd.set_option("display.width", 140)
    print(part1_df.to_string(index=False))

    print("\n=== OPTITRACK PRE->POST CHANGE TABLE ===")
    print(part2_df.to_string(index=False))

    print("\nSaved:")
    print(f"  Model_Analysis_Outputs/P5_concurrent_validity.csv")
    print(f"  Model_Analysis_Outputs/P5_wilcoxon_optitrack.csv")


if __name__ == "__main__":
    main()
