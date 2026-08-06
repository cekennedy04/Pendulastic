#!/usr/bin/env python3
"""
calibrate.py
============
Quadratic polynomial calibration of aligned MediaPipe 2D knee angles against
3D OptiTrack ground truth.

Pipeline
--------
1. Load  aligned_training_data.json  (produced by align_and_calibrate.py)
2. LOTO cross-validation (Leave-One-Trial-Out) to produce unbiased MAE/RMSE
3. Fit final degree-2 polynomial on all kept data via Ridge regression
4. Save coefficients to  calibration_coefficients.json
5. Export two-panel scatter figure to  figures/calibration_scatter.png

Formula saved:
    ot_angle = c0 + c1 * mp + c2 * mp^2

Usage
-----
  .venv\\Scripts\\python.exe calibrate.py
  .venv\\Scripts\\python.exe calibrate.py --degree 3 --alpha 0.1
  .venv\\Scripts\\python.exe calibrate.py --input custom_aligned.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")          # write to file — no GUI window needed

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.preprocessing import PolynomialFeatures

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).parent
INPUT_JSON = ROOT / "training_data" / "aligned_training_data.json"
COEFF_JSON = ROOT / "calibration_coefficients.json"
FIG_PATH   = ROOT / "figures" / "calibration_scatter.png"

# ── Defaults ──────────────────────────────────────────────────────────────────
# Optimal settings found by LOTO sweep (investigate_calibration.py):
#   min_r=0.70 drops 4 low-quality trials → LOTO RMSE 24° → 20°
#   degree=1 (linear) matches degree-2 LOTO RMSE and gives clean positive slope
#   alpha is irrelevant (5k+ samples, 1-2 features)
DEFAULT_DEGREE          = 1
DEFAULT_ALPHA           = 1.0
DEFAULT_MIN_R           = 0.70
DEFAULT_EXCLUDE_FLIPPED = True


# ═══════════════════════════════════════════════════════════════════════════════
# Data
# ═══════════════════════════════════════════════════════════════════════════════

def load_data(
    path: Path,
    min_r: float | None = None,
    exclude_flipped: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Parse aligned_training_data.json.

    Parameters
    ----------
    min_r            : if set, exclude trials whose r_best < min_r.
                       Use 0.0 to drop all negative-correlation trials.
    exclude_flipped  : if True, exclude trials where the aligner chose the
                       180-deg-flipped mp angle (sign-inverted cross-correlation).

    Returns
    -------
    X      : (N, 1) float64 — mp_angle_aligned
    y      : (N,)   float64 — ot_angle
    groups : (N,)   str     — trial name, used as LOTO group key
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # Per-trial quality metadata from the alignments table
    trial_meta: dict[str, dict] = {
        a["trial"]: a
        for a in data.get("trial_alignments", [])
    }

    def trial_ok(name: str) -> bool:
        meta  = trial_meta.get(name, {})
        r_raw = meta.get("r_best")
        r     = 1.0 if r_raw is None else float(r_raw)  # None r_best → pass
        flip  = meta.get("flipped", False)
        if min_r is not None and r < min_r:
            return False
        if exclude_flipped and flip:
            return False
        return True

    all_samples   = data["samples"]
    valid_samples = [
        s for s in all_samples
        if s.get("mp_angle_aligned") is not None and trial_ok(s["trial"])
    ]

    n_dropped = len(all_samples) - len(valid_samples)
    if n_dropped:
        excluded_trials = sorted({
            s["trial"] for s in all_samples
            if not trial_ok(s["trial"]) or s.get("mp_angle_aligned") is None
        })
        print(f"  Quality filter: dropped {n_dropped:,} samples "
              f"({len(excluded_trials)} trials excluded)")
        for t in excluded_trials[:12]:
            meta = trial_meta.get(t, {})
            print(f"    - {t}  r={meta.get('r_best', '?'):.3f}  "
                  f"flipped={meta.get('flipped', '?')}")
        if len(excluded_trials) > 12:
            print(f"    ... and {len(excluded_trials) - 12} more")

    X      = np.array([[s["mp_angle_aligned"]] for s in valid_samples], dtype=np.float64)
    y      = np.array([s["ot_angle"]            for s in valid_samples], dtype=np.float64)
    groups = np.array([s["trial"]               for s in valid_samples])
    return X, y, groups


# ═══════════════════════════════════════════════════════════════════════════════
# Model helpers
# ═══════════════════════════════════════════════════════════════════════════════

def build_model(degree: int, alpha: float):
    """
    Scikit-learn pipeline:
      PolynomialFeatures(degree, include_bias=False)  →  features [mp, mp^2, ...]
      Ridge(alpha, fit_intercept=True)                →  intercept c0, coef [c1, c2, ...]
    """
    return _Poly2Ridge(degree=degree, alpha=alpha)


class _Poly2Ridge:
    """Thin wrapper so fit/predict work identically to any sklearn estimator."""

    def __init__(self, degree: int, alpha: float):
        self._poly  = PolynomialFeatures(degree=degree, include_bias=False)
        self._ridge = Ridge(alpha=alpha, fit_intercept=True)

    def fit(self, X: np.ndarray, y: np.ndarray) -> "_Poly2Ridge":
        self._ridge.fit(self._poly.fit_transform(X), y)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self._ridge.predict(self._poly.transform(X))

    @property
    def coefficients(self) -> list[float]:
        """[c0, c1, ..., cN] where ot = c0 + c1*mp + c2*mp^2 + ..."""
        return [float(self._ridge.intercept_)] + [float(c) for c in self._ridge.coef_]


def _rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


# ═══════════════════════════════════════════════════════════════════════════════
# LOTO cross-validation
# ═══════════════════════════════════════════════════════════════════════════════

def loto_cv(
    X: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray,
    degree: int,
    alpha: float,
) -> tuple[pd.DataFrame, np.ndarray]:
    """
    Leave-One-Trial-Out cross-validation.

    Returns
    -------
    results   : DataFrame with columns [trial, n, mae, rmse]
    y_loto    : (N,) array of out-of-fold predictions (same order as input)
    """
    logo   = LeaveOneGroupOut()
    y_loto = np.empty_like(y)
    rows: list[dict] = []

    for train_idx, test_idx in logo.split(X, y, groups):
        model  = build_model(degree, alpha)
        model.fit(X[train_idx], y[train_idx])
        y_hat  = model.predict(X[test_idx])
        y_loto[test_idx] = y_hat
        rows.append({
            "trial": groups[test_idx[0]],
            "n":     len(test_idx),
            "mae":   float(mean_absolute_error(y[test_idx], y_hat)),
            "rmse":  _rmse(y[test_idx], y_hat),
        })

    df = pd.DataFrame(rows).sort_values("trial").reset_index(drop=True)
    return df, y_loto


# ═══════════════════════════════════════════════════════════════════════════════
# Console output
# ═══════════════════════════════════════════════════════════════════════════════

def print_loto_table(df: pd.DataFrame) -> None:
    W   = 44
    hdr = f"  {'Trial':<{W}} {'N':>5}  {'MAE (deg)':>9}  {'RMSE (deg)':>10}"
    sep = "  " + "-" * (len(hdr) - 2)

    print(sep)
    print(hdr)
    print(sep)
    for _, row in df.iterrows():
        print(f"  {row['trial']:<{W}} {int(row['n']):>5}  "
              f"{row['mae']:>9.2f}  {row['rmse']:>10.2f}")
    print(sep)

    mae_mean,  mae_sd  = df["mae"].mean(),  df["mae"].std()
    rmse_mean, rmse_sd = df["rmse"].mean(), df["rmse"].std()
    print(f"  {'Mean':>{W+5}}  {mae_mean:>9.2f}  {rmse_mean:>10.2f}")
    print(f"  {'SD':>{W+5}}  {mae_sd:>9.2f}  {rmse_sd:>10.2f}")
    print(sep)


# ═══════════════════════════════════════════════════════════════════════════════
# Figure
# ═══════════════════════════════════════════════════════════════════════════════

def _trial_colors(unique_trials: list[str]) -> dict[str, tuple]:
    """
    Assign a unique RGBA colour to each trial.
    Combines tab20 + tab20b for up to 40 distinct colours.
    """
    n   = len(unique_trials)
    c20 = plt.cm.tab20
    c20b = plt.cm.tab20b
    palette = [c20(i / 20)  for i in range(min(n, 20))] + \
              [c20b((i - 20) / 20) for i in range(20, n)]
    return {t: palette[i] for i, t in enumerate(unique_trials)}


def _short_label(trial: str) -> str:
    """Abbreviated trial label for the legend."""
    t = trial
    for rep in [("Participant_", "P"), ("_control", ""), ("_left", "L"),
                ("_right", "R"), ("_solo", "s"), ("_duo", "d"), ("_side", "S")]:
        t = t.replace(*rep)
    return t


def plot_calibration(
    X:           np.ndarray,
    y:           np.ndarray,
    groups:      np.ndarray,
    y_loto:      np.ndarray,
    final_model: _Poly2Ridge,
    fig_path:    Path,
    degree:      int,
) -> None:
    """
    Two-panel scatter figure.

    Left  — raw mp_angle_aligned vs ot_angle  +  calibration curve (red)
    Right — LOTO calibrated angle vs ot_angle  +  identity line

    Using LOTO predictions in the right panel gives an unbiased picture of
    calibration accuracy (the right panel is NOT fitted to its own data).
    """
    mp_raw  = X[:, 0]
    ang_min = min(mp_raw.min(), y.min(), y_loto.min()) - 2
    ang_max = max(mp_raw.max(), y.max(), y_loto.max()) + 2
    id_line = [ang_min, ang_max]

    # Smooth calibration curve over the mp range
    x_curve = np.linspace(mp_raw.min(), mp_raw.max(), 400).reshape(-1, 1)
    y_curve = final_model.predict(x_curve)

    # Per-trial colours
    unique_trials = sorted(set(groups))
    color_map = _trial_colors(unique_trials)

    # Metrics
    raw_rmse  = _rmse(y, final_model.predict(X))
    raw_mae   = float(mean_absolute_error(y, final_model.predict(X)))
    loto_rmse = _rmse(y, y_loto)
    loto_mae  = float(mean_absolute_error(y, y_loto))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6.5))
    fig.suptitle(
        f"Pendulastic Knee Angle Calibration  (degree-{degree} polynomial, n={len(y):,})",
        fontsize=13, fontweight="bold", y=1.01,
    )

    # ── Left: raw scatter + calibration curve ─────────────────────────────────
    for trial in unique_trials:
        mask = groups == trial
        ax1.scatter(mp_raw[mask], y[mask],
                    color=color_map[trial], alpha=0.30, s=7, linewidths=0)
    ax1.plot(x_curve[:, 0], y_curve, color="crimson", lw=2.2,
             label="Calibration curve", zorder=5)
    ax1.plot(id_line, id_line, "--", color="#888888", lw=1.3, alpha=0.7,
             label="Identity (y = x)")
    ax1.set_xlim(ang_min, ang_max)
    ax1.set_ylim(ang_min, ang_max)
    ax1.set_aspect("equal", "box")
    ax1.set_xlabel("MediaPipe Aligned Angle (deg)", fontsize=11)
    ax1.set_ylabel("OptiTrack Angle (deg)", fontsize=11)
    ax1.set_title(
        f"Raw (aligned, sign-corrected) vs OptiTrack\n"
        f"Training fit:  RMSE = {raw_rmse:.1f} deg    MAE = {raw_mae:.1f} deg",
        fontsize=10,
    )
    ax1.legend(fontsize=9, loc="upper left")
    ax1.grid(True, alpha=0.25, linewidth=0.6)

    # ── Right: LOTO calibrated vs OptiTrack ───────────────────────────────────
    legend_handles = []
    for trial in unique_trials:
        mask = groups == trial
        sc = ax2.scatter(y_loto[mask], y[mask],
                         color=color_map[trial], alpha=0.35, s=7, linewidths=0,
                         label=_short_label(trial))
        legend_handles.append(sc)
    ax2.plot(id_line, id_line, "--", color="#888888", lw=1.3, alpha=0.7)
    ax2.set_xlim(ang_min, ang_max)
    ax2.set_ylim(ang_min, ang_max)
    ax2.set_aspect("equal", "box")
    ax2.set_xlabel("LOTO Calibrated Angle (deg)", fontsize=11)
    ax2.set_ylabel("OptiTrack Angle (deg)", fontsize=11)
    ax2.set_title(
        f"LOTO Calibrated vs OptiTrack  (unbiased estimate)\n"
        f"LOTO:  RMSE = {loto_rmse:.1f} deg    MAE = {loto_mae:.1f} deg",
        fontsize=10,
    )
    ax2.grid(True, alpha=0.25, linewidth=0.6)

    # Compact legend — two columns, small font, outside right edge of ax2
    leg = ax2.legend(
        handles=legend_handles,
        labels=[_short_label(t) for t in unique_trials],
        fontsize=6.5,
        ncol=2,
        loc="upper left",
        bbox_to_anchor=(1.01, 1.0),
        borderaxespad=0,
        title="Trial",
        title_fontsize=7.5,
        framealpha=0.85,
    )

    plt.tight_layout()
    fig_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(fig_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"\n  Figure -> {fig_path}")


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Quadratic calibration of aligned MediaPipe knee angles.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--input",  type=Path,  default=INPUT_JSON,    metavar="PATH")
    ap.add_argument("--output", type=Path,  default=COEFF_JSON,    metavar="PATH")
    ap.add_argument("--figure", type=Path,  default=FIG_PATH,      metavar="PATH")
    ap.add_argument("--degree", type=int,   default=DEFAULT_DEGREE, metavar="N",
                    help=f"Polynomial degree (default {DEFAULT_DEGREE}; "
                         "LOTO sweep shows deg=1 matches deg=2 and gives clean positive slope)")
    ap.add_argument("--alpha",  type=float, default=DEFAULT_ALPHA,  metavar="A",
                    help="Ridge regularisation strength (alpha has negligible effect "
                         "with 4k+ samples and 1-2 features; kept for completeness)")
    ap.add_argument("--min-r", type=float, default=DEFAULT_MIN_R, metavar="R", dest="min_r",
                    help=f"Exclude trials with r_best < R (default {DEFAULT_MIN_R}). "
                         "Trials below 0.70 were found to inflate LOTO RMSE by ~4 deg.")
    ap.add_argument("--exclude-flipped", action="store_true", default=DEFAULT_EXCLUDE_FLIPPED,
                    dest="exclude_flipped",
                    help="Exclude trials where the aligner used the 180-deg-flipped mp angle "
                         f"(default {DEFAULT_EXCLUDE_FLIPPED}). Use --no-exclude-flipped to disable.")
    ap.add_argument("--no-exclude-flipped", action="store_false", dest="exclude_flipped",
                    help="Include flipped-alignment trials (not recommended).")
    ap.add_argument("--all-trials", action="store_true", default=False,
                    help="Shortcut: disable all quality filters (equivalent to "
                         "--min-r -999 --no-exclude-flipped). Reproduces legacy behaviour.")
    args = ap.parse_args()

    # ── Load ──────────────────────────────────────────────────────────────────
    print(f"\nLoading {args.input} ...")
    if not args.input.exists():
        print(f"ERROR: {args.input} not found.")
        print("Run align_and_calibrate.py first.")
        sys.exit(1)

    if args.all_trials:
        args.min_r          = None
        args.exclude_flipped = False
        print("  --all-trials: quality filters disabled (legacy behaviour)")

    filter_desc = []
    if args.min_r is not None:
        filter_desc.append(f"r_best >= {args.min_r}")
    if args.exclude_flipped:
        filter_desc.append("non-flipped only")
    if filter_desc:
        print(f"  Quality filter: {' + '.join(filter_desc)}")
    else:
        print("  Quality filter: none (all trials included)")

    X, y, groups = load_data(args.input, min_r=args.min_r, exclude_flipped=args.exclude_flipped)
    unique_trials = sorted(set(groups))
    n_trials = len(unique_trials)
    print(f"  {len(y):,} samples  /  {n_trials} trials")

    # ── Raw baseline (no polynomial, just the aligned angle) ──────────────────
    raw_baseline_rmse = _rmse(y, X[:, 0])
    raw_baseline_mae  = float(mean_absolute_error(y, X[:, 0]))
    print(f"\n  Baseline (aligned only, no calibration):"
          f"  RMSE = {raw_baseline_rmse:.2f} deg    MAE = {raw_baseline_mae:.2f} deg")

    # ── LOTO CV ───────────────────────────────────────────────────────────────
    print(f"\n=== Leave-One-Trial-Out Cross-Validation"
          f"  (degree={args.degree}, ridge alpha={args.alpha}) ===\n")
    loto_df, y_loto = loto_cv(X, y, groups, args.degree, args.alpha)
    print_loto_table(loto_df)

    loto_mae_mean  = float(loto_df["mae"].mean())
    loto_mae_sd    = float(loto_df["mae"].std())
    loto_rmse_mean = float(loto_df["rmse"].mean())
    loto_rmse_sd   = float(loto_df["rmse"].std())

    loto_overall_rmse = _rmse(y, y_loto)
    loto_overall_mae  = float(mean_absolute_error(y, y_loto))

    print(f"\n  LOTO overall RMSE : {loto_overall_rmse:.2f} deg")
    print(f"  LOTO overall MAE  : {loto_overall_mae:.2f} deg")
    print(f"  Improvement over baseline RMSE: "
          f"{raw_baseline_rmse:.2f} -> {loto_overall_rmse:.2f} deg "
          f"({raw_baseline_rmse - loto_overall_rmse:+.2f} deg)")

    # ── Final model on all data ───────────────────────────────────────────────
    print("\nFitting final model on all data ...")
    final_model = build_model(args.degree, args.alpha)
    final_model.fit(X, y)
    coeffs = final_model.coefficients

    train_rmse = _rmse(y, final_model.predict(X))
    train_mae  = float(mean_absolute_error(y, final_model.predict(X)))
    print(f"  Training RMSE : {train_rmse:.2f} deg    MAE : {train_mae:.2f} deg")

    # Print formula
    terms   = ["1"] + [f"mp^{i+1}" for i in range(args.degree)]
    formula = "  ot = " + " + ".join(f"({c:+.6f})*{t}" for c, t in zip(coeffs, terms))
    print(f"\n  Calibration formula:\n{formula}\n")

    # ── Save coefficients ─────────────────────────────────────────────────────
    output_data = {
        "degree":              args.degree,
        "ridge_alpha":         args.alpha,
        "filter_min_r":        args.min_r,
        "filter_exclude_flipped": args.exclude_flipped,
        "feature":             "mp_angle_aligned",
        "formula":             "ot_angle = c[0] + c[1]*mp + c[2]*mp^2 (+ higher if degree>2)",
        "coefficients":        [round(c, 9) for c in coeffs],
        "coefficient_labels":  ["intercept"] + [f"mp^{i+1}" for i in range(args.degree)],
        "n_trials":            n_trials,
        "n_samples":           int(len(y)),
        "train_rmse_deg":      round(train_rmse, 4),
        "train_mae_deg":       round(train_mae,  4),
        "loto_overall_rmse_deg": round(loto_overall_rmse, 4),
        "loto_overall_mae_deg":  round(loto_overall_mae,  4),
        "loto_per_trial_mae_mean_deg": round(loto_mae_mean,  4),
        "loto_per_trial_mae_sd_deg":   round(loto_mae_sd,   4),
        "loto_per_trial_rmse_mean_deg": round(loto_rmse_mean, 4),
        "loto_per_trial_rmse_sd_deg":   round(loto_rmse_sd,  4),
        "per_trial_loto": [
            {
                "trial": row["trial"],
                "n":     int(row["n"]),
                "mae":   round(row["mae"],  4),
                "rmse":  round(row["rmse"], 4),
            }
            for _, row in loto_df.iterrows()
        ],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)
    print(f"  Coefficients -> {args.output}")

    # ── Figure ────────────────────────────────────────────────────────────────
    print("\nGenerating scatter plot ...")
    plot_calibration(X, y, groups, y_loto, final_model, args.figure, args.degree)

    print("\nDone.\n")


if __name__ == "__main__":
    main()
