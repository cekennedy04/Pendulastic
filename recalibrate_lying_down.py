#!/usr/bin/env python3
"""
recalibrate_lying_down.py
=========================
Position-stratified calibration for the MediaPipe -> OptiTrack knee-angle mapping.

Background
----------
Participants 4 and 8 were tested SUPINE (lying down on a table with legs hanging
off the edge), unlike the other participants who sat upright.  The 2D image-plane
angle computed by MediaPipe sees a different perspective when the body is horizontal:
the hip-knee-ankle geometry appears foreshortened differently, the MediaPipe model
(trained predominantly on upright poses) may miscalibrate joint positions, and the
resulting mp_angle_aligned -> ot_angle mapping has a different systematic offset and
scale from the upright group.

Strategy
--------
1. EVALUATE:  Run the current global model on lying-down vs upright trials
              separately to measure how large the residual bias is per group.
2. RE-FIT:    Fit group-specific linear polynomials:
                  upright     — all trials EXCEPT Participant_4 and Participant_8
                  lying_down  — Participant_4 and Participant_8 only
              Run LOTO within each group to estimate per-trial error.
3. SAVE:      Write two separate coefficient JSON files:
                  calibration_coefficients_upright.json
                  calibration_coefficients_lying_down.json
              so the viewer can select the correct one at runtime.

Usage
-----
  .venv\\Scripts\\python.exe recalibrate_lying_down.py
  .venv\\Scripts\\python.exe recalibrate_lying_down.py --degree 2
  .venv\\Scripts\\python.exe recalibrate_lying_down.py --no-save   # evaluate only

Output files (next to this script)
-----------------------------------
  calibration_coefficients_upright.json
  calibration_coefficients_lying_down.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

try:
    import pandas as pd
    from sklearn.linear_model import Ridge
    from sklearn.metrics import mean_absolute_error
    from sklearn.model_selection import LeaveOneGroupOut
    from sklearn.preprocessing import PolynomialFeatures
except ImportError:
    print("ERROR: Required packages missing.  Run:")
    print("  .venv\\Scripts\\pip install scikit-learn pandas")
    sys.exit(1)

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT         = Path(__file__).parent
ALIGNED_JSON = ROOT / "training_data" / "aligned_training_data.json"
GLOBAL_JSON  = ROOT / "calibration_coefficients.json"

# Participants whose data was collected SUPINE (lying down)
LYING_DOWN_PARTICIPANTS = {"Participant_4", "Participant_8"}

# Same quality filters as calibrate.py
DEFAULT_DEGREE = 1
DEFAULT_ALPHA  = 1.0
DEFAULT_MIN_R  = 0.70


# ── Data loading ──────────────────────────────────────────────────────────────

def load_data(min_r: float = DEFAULT_MIN_R):
    """
    Load aligned_training_data.json and split into lying-down and upright groups.

    Returns (X, y, groups, positions) where position is 'lying_down' or 'upright'.
    """
    if not ALIGNED_JSON.exists():
        print(f"ERROR: {ALIGNED_JSON} not found.")
        print("  Run:  .venv\\Scripts\\python.exe align_and_calibrate.py")
        sys.exit(1)

    with open(ALIGNED_JSON, encoding="utf-8") as f:
        data = json.load(f)

    trial_meta: dict[str, dict] = {
        a["trial"]: a for a in data.get("trial_alignments", [])
    }

    def trial_ok(name: str) -> bool:
        meta  = trial_meta.get(name, {})
        r_raw = meta.get("r_best")
        r     = 1.0 if r_raw is None else float(r_raw)
        flip  = meta.get("flipped", False)
        if r < min_r:
            return False
        if flip:
            return False
        return True

    def participant_of(trial: str) -> str:
        """'Participant_4_left_T2' -> 'Participant_4'"""
        parts = trial.split("_")
        if len(parts) >= 2:
            return "_".join(parts[:2])
        return parts[0]

    def position_of(trial: str) -> str:
        pid = participant_of(trial)
        return "lying_down" if pid in LYING_DOWN_PARTICIPANTS else "upright"

    all_samples   = data["samples"]
    valid_samples = [
        s for s in all_samples
        if s.get("mp_angle_aligned") is not None and trial_ok(s["trial"])
    ]

    n_dropped = len(all_samples) - len(valid_samples)
    excl = sorted({s["trial"] for s in all_samples if not trial_ok(s["trial"])
                   or s.get("mp_angle_aligned") is None})
    print(f"  Loaded {len(all_samples):,} samples -> kept {len(valid_samples):,} "
          f"(dropped {n_dropped:,} from {len(excl)} trials: r<{min_r} or flipped)")

    X         = np.array([[s["mp_angle_aligned"]] for s in valid_samples], dtype=np.float64)
    y         = np.array([s["ot_angle"]            for s in valid_samples], dtype=np.float64)
    groups    = np.array([s["trial"]               for s in valid_samples])
    positions = np.array([position_of(s["trial"])  for s in valid_samples])

    return X, y, groups, positions


# ── Model ─────────────────────────────────────────────────────────────────────

class PolyRidge:
    def __init__(self, degree: int = 1, alpha: float = 1.0):
        self._poly  = PolynomialFeatures(degree=degree, include_bias=False)
        self._ridge = Ridge(alpha=alpha, fit_intercept=True)

    def fit(self, X, y):
        self._ridge.fit(self._poly.fit_transform(X), y)
        return self

    def predict(self, X):
        return self._ridge.predict(self._poly.transform(X))

    @property
    def coefficients(self) -> list[float]:
        return [float(self._ridge.intercept_)] + [float(c) for c in self._ridge.coef_]


def _rmse(y_true, y_pred) -> float:
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def _mae(y_true, y_pred) -> float:
    return float(np.mean(np.abs(y_true - y_pred)))


# ── LOTO cross-validation ─────────────────────────────────────────────────────

def loto_cv(X, y, groups, degree: int, alpha: float):
    logo   = LeaveOneGroupOut()
    y_loto = np.empty_like(y)
    rows   = []

    for train_idx, test_idx in logo.split(X, y, groups):
        m = PolyRidge(degree, alpha)
        m.fit(X[train_idx], y[train_idx])
        y_hat = m.predict(X[test_idx])
        y_loto[test_idx] = y_hat
        rows.append({
            "trial": groups[test_idx[0]],
            "n":     len(test_idx),
            "mae":   _mae(y[test_idx], y_hat),
            "rmse":  _rmse(y[test_idx], y_hat),
        })

    df = pd.DataFrame(rows).sort_values("trial").reset_index(drop=True)
    return df, y_loto


# ── Console output helpers ────────────────────────────────────────────────────

def print_loto_table(df: pd.DataFrame, label: str = "") -> None:
    W   = 44
    if label:
        print(f"\n  {label}")
    hdr = f"  {'Trial':<{W}} {'N':>5}  {'MAE':>9}  {'RMSE':>9}"
    sep = "  " + "-" * (len(hdr) - 2)
    print(sep)
    print(hdr)
    print(sep)
    for _, row in df.iterrows():
        print(f"  {row['trial']:<{W}} {int(row['n']):>5}  "
              f"{row['mae']:>9.2f}  {row['rmse']:>9.2f}")
    print(sep)
    mae_mean  = df["mae"].mean()
    rmse_mean = df["rmse"].mean()
    mae_sd    = df["mae"].std()
    rmse_sd   = df["rmse"].std()
    print(f"  {'Mean':>{W+5}}  {mae_mean:>9.2f}  {rmse_mean:>9.2f}")
    print(f"  {'SD':>{W+5}}  {mae_sd:>9.2f}  {rmse_sd:>9.2f}")
    print(sep)


def _coeff_labels(degree: int) -> list[str]:
    return ["intercept"] + [f"mp^{i}" for i in range(1, degree + 1)]


def save_coefficients(
    path: Path,
    group: str,
    model: PolyRidge,
    df_loto: pd.DataFrame,
    n_trials: int,
    n_samples: int,
    y_all: np.ndarray,
    y_loto_all: np.ndarray,
    degree: int,
    alpha: float,
    min_r: float,
) -> None:
    per_trial_loto = [
        {"trial": row["trial"], "n": int(row["n"]),
         "mae": round(row["mae"], 4), "rmse": round(row["rmse"], 4)}
        for _, row in df_loto.iterrows()
    ]

    # Compute pooled LOTO metrics over this group only
    loto_rmse = _rmse(y_all, y_loto_all)
    loto_mae  = _mae(y_all, y_loto_all)

    obj = {
        "position_group":        group,
        "lying_down_participants": sorted(LYING_DOWN_PARTICIPANTS),
        "degree":                degree,
        "ridge_alpha":           alpha,
        "filter_min_r":          min_r,
        "filter_exclude_flipped": True,
        "feature":               "mp_angle_aligned",
        "formula":               "ot_angle = c[0] + c[1]*mp (+ higher terms if degree>1)",
        "coefficients":          [round(c, 9) for c in model.coefficients],
        "coefficient_labels":    _coeff_labels(degree),
        "n_trials":              n_trials,
        "n_samples":             n_samples,
        "loto_overall_rmse_deg": round(loto_rmse, 4),
        "loto_overall_mae_deg":  round(loto_mae, 4),
        "per_trial_loto":        per_trial_loto,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    print(f"  Saved -> {path.name}")


# ── Evaluation against global model ──────────────────────────────────────────

def evaluate_global_model(X, y, groups, positions, global_json: Path):
    """Apply the saved global polynomial to upright/lying-down subsets separately."""
    if not global_json.exists():
        print("  (Global coefficients file not found — skipping global evaluation)")
        return

    with open(global_json, encoding="utf-8") as f:
        g = json.load(f)

    coeffs = g["coefficients"]
    degree = g.get("degree", 1)

    def poly_predict(X_in):
        result = np.full(len(X_in), coeffs[0])
        for power, c in enumerate(coeffs[1:], start=1):
            result += c * (X_in[:, 0] ** power)
        return result

    print("\n" + "=" * 64)
    print("  CURRENT GLOBAL MODEL EVALUATION BY POSITION GROUP")
    print("=" * 64)
    print(f"  Formula: ot = {coeffs[0]:.3f} + {coeffs[1]:.3f} * mp"
          + (f" + ... (degree {degree})" if degree > 1 else ""))

    for pos_label in ("upright", "lying_down"):
        mask = positions == pos_label
        if not mask.any():
            print(f"\n  {pos_label}: no trials found")
            continue
        X_g  = X[mask]
        y_g  = y[mask]
        g_g  = groups[mask]
        pred = poly_predict(X_g)

        trials = sorted(set(g_g))
        print(f"\n  {pos_label.upper()}  ({len(trials)} trials, {mask.sum():,} samples):")
        print(f"    Overall RMSE = {_rmse(y_g, pred):.2f} deg,  MAE = {_mae(y_g, pred):.2f} deg")

        # Per-trial breakdown
        W = 44
        print(f"\n    {'Trial':<{W}} {'N':>5}  {'MAE':>9}  {'RMSE':>9}  {'Bias':>9}")
        print("    " + "-" * (W + 38))
        for t in trials:
            tm   = g_g == t
            pred_t = poly_predict(X_g[tm])
            bias_t = float(np.mean(y_g[tm] - pred_t))
            print(f"    {t:<{W}} {tm.sum():>5}  "
                  f"{_mae(y_g[tm], pred_t):>9.2f}  "
                  f"{_rmse(y_g[tm], pred_t):>9.2f}  "
                  f"{bias_t:>+9.2f}")

        # Systematic offset: mean of (ot - mp) per group
        raw_bias = float(np.mean(y_g - X_g[:, 0]))
        cal_bias = float(np.mean(y_g - pred))
        print(f"\n    Systematic raw bias  (ot - mp)          : {raw_bias:+.2f} deg")
        print(f"    Residual calibrated bias (ot - model)  : {cal_bias:+.2f} deg")


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Position-stratified calibration for lying-down vs upright participants")
    p.add_argument("--degree", type=int,   default=DEFAULT_DEGREE,
                   help=f"Polynomial degree (default {DEFAULT_DEGREE})")
    p.add_argument("--alpha",  type=float, default=DEFAULT_ALPHA,
                   help=f"Ridge alpha (default {DEFAULT_ALPHA})")
    p.add_argument("--min-r",  type=float, default=DEFAULT_MIN_R,
                   help=f"Min r_best threshold (default {DEFAULT_MIN_R})")
    p.add_argument("--no-save", action="store_true",
                   help="Evaluate only — do not write coefficient files")
    return p.parse_args()


def main():
    args = parse_args()

    print("=" * 64)
    print("  Lying-Down vs Upright Position-Stratified Calibration")
    print("=" * 64)
    print(f"\n  Lying-down participants : {sorted(LYING_DOWN_PARTICIPANTS)}")
    print(f"  Polynomial degree       : {args.degree}")
    print(f"  Min alignment r_best    : {args.min_r}")
    print()

    print("Loading aligned training data ...")
    X, y, groups, positions = load_data(min_r=args.min_r)

    n_lying   = int((positions == "lying_down").sum())
    n_upright = int((positions == "upright").sum())
    t_lying   = len(set(groups[positions == "lying_down"]))
    t_upright = len(set(groups[positions == "upright"]))

    print(f"\n  Upright    : {t_upright} trials,  {n_upright:,} samples")
    print(f"  Lying-down : {t_lying} trials,  {n_lying:,} samples")

    # ── Step 1: evaluate global model as baseline ──────────────────────────────
    evaluate_global_model(X, y, groups, positions, GLOBAL_JSON)

    # ── Step 2: fit group-specific models ─────────────────────────────────────
    print("\n" + "=" * 64)
    print("  POSITION-STRATIFIED RE-FIT")
    print("=" * 64)

    for pos_label, coeff_fname in [
        ("upright",    "calibration_coefficients_upright.json"),
        ("lying_down", "calibration_coefficients_lying_down.json"),
    ]:
        mask = positions == pos_label
        if not mask.any():
            print(f"\n  [{pos_label}]  No data — skipping.")
            continue

        X_g     = X[mask]
        y_g     = y[mask]
        grp_g   = groups[mask]
        n_t     = len(set(grp_g))
        n_s     = int(mask.sum())

        print(f"\n  [{pos_label.upper()}]  {n_t} trials, {n_s:,} samples")

        # LOTO within this group
        if n_t < 2:
            print("  WARNING: Only 1 trial — LOTO not possible. Fitting on all data.")
            model = PolyRidge(args.degree, args.alpha).fit(X_g, y_g)
            print(f"  Coefficients: {model.coefficients}")
            df_loto = pd.DataFrame([{
                "trial": grp_g[0], "n": n_s,
                "mae": _mae(y_g, model.predict(X_g)),
                "rmse": _rmse(y_g, model.predict(X_g)),
            }])
            y_loto_g = model.predict(X_g)
        else:
            df_loto, y_loto_g = loto_cv(X_g, y_g, grp_g, args.degree, args.alpha)

        # Final model on all data in this group
        model = PolyRidge(args.degree, args.alpha).fit(X_g, y_g)

        coeffs = model.coefficients
        formula_str = f"ot = {coeffs[0]:.3f} + {coeffs[1]:.3f}*mp"
        if len(coeffs) > 2:
            formula_str += " + " + " + ".join(
                f"{coeffs[i]:.4f}*mp^{i}" for i in range(2, len(coeffs)))
        print(f"  Final coefficients: {formula_str}")

        print_loto_table(df_loto, label=f"LOTO results — {pos_label}")

        if not args.no_save:
            out_path = ROOT / coeff_fname
            save_coefficients(
                out_path, pos_label, model, df_loto,
                n_trials=n_t, n_samples=n_s,
                y_all=y_g, y_loto_all=y_loto_g,
                degree=args.degree, alpha=args.alpha, min_r=args.min_r,
            )

    # ── Step 3: comparison summary ────────────────────────────────────────────
    print("\n" + "=" * 64)
    print("  SUMMARY")
    print("=" * 64)

    with open(GLOBAL_JSON, encoding="utf-8") as f:
        g = json.load(f)
    global_rmse = g.get("loto_overall_rmse_deg", "?")
    global_mae  = g.get("loto_overall_mae_deg",  "?")

    print(f"\n  Global model (all participants, LOTO):")
    print(f"    RMSE = {global_rmse} deg,  MAE = {global_mae} deg")
    print(f"    Formula: ot = {g['coefficients'][0]:.3f} + {g['coefficients'][1]:.3f}*mp")

    for pos_label, coeff_fname in [
        ("upright",    "calibration_coefficients_upright.json"),
        ("lying_down", "calibration_coefficients_lying_down.json"),
    ]:
        out_path = ROOT / coeff_fname
        if not out_path.exists():
            continue
        with open(out_path, encoding="utf-8") as f:
            d = json.load(f)
        coeffs = d["coefficients"]
        print(f"\n  {pos_label.upper()} model (LOTO within group):")
        print(f"    RMSE = {d['loto_overall_rmse_deg']} deg,  "
              f"MAE = {d['loto_overall_mae_deg']} deg")
        print(f"    Formula: ot = {coeffs[0]:.3f} + {coeffs[1]:.3f}*mp")
        print(f"    Trials: {d['n_trials']},  Samples: {d['n_samples']:,}")

    print()
    print("  To apply in the viewer, pass the appropriate calibration file:")
    print("    Upright participants:")
    print("      .venv\\Scripts\\python.exe pendulastic_viewer.py "
          "--coefficients calibration_coefficients_upright.json")
    print("    Lying-down participants (P4, P8):")
    print("      .venv\\Scripts\\python.exe pendulastic_viewer.py "
          "--coefficients calibration_coefficients_lying_down.json")
    print("=" * 64)


if __name__ == "__main__":
    main()

