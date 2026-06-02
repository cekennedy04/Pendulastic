"""Reliability and validity statistical analyses for Aim 2 and Aim 3."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


# ---------------------------------------------------------------------------
# Aim 2 — Reliability and agreement with IMUs / motion capture
# ---------------------------------------------------------------------------

def intraclass_correlation(
    ratings: pd.DataFrame,
    icc_type: str = "ICC(2,1)",
) -> dict:
    """Compute Intraclass Correlation Coefficient (ICC) for reliability analysis.

    Args:
        ratings: DataFrame where rows are subjects and columns are raters/sessions.
        icc_type: ICC model to compute. Supported: "ICC(1,1)", "ICC(2,1)", "ICC(3,1)".

    Returns:
        Dict with keys: icc, f_value, df1, df2, p_value, ci_lower, ci_upper.
    """
    # Placeholder — full two-way mixed ANOVA implementation required
    raise NotImplementedError(f"ICC computation ({icc_type}) not yet implemented.")


def bland_altman(
    method_a: np.ndarray,
    method_b: np.ndarray,
    confidence: float = 0.95,
) -> dict:
    """Bland–Altman limits of agreement between two measurement methods.

    Args:
        method_a: Measurements from method A (e.g., video-based system).
        method_b: Measurements from method B (e.g., IMU or motion capture).
        confidence: Confidence level for limits of agreement (default 95%).

    Returns:
        Dict with keys: mean_diff, std_diff, loa_lower, loa_upper, mean_values.
    """
    method_a = np.asarray(method_a, dtype=float)
    method_b = np.asarray(method_b, dtype=float)

    diff = method_a - method_b
    mean_diff = float(np.mean(diff))
    std_diff = float(np.std(diff, ddof=1))

    z = float(stats.norm.ppf(1 - (1 - confidence) / 2))
    loa_lower = mean_diff - z * std_diff
    loa_upper = mean_diff + z * std_diff

    return {
        "mean_diff": mean_diff,
        "std_diff": std_diff,
        "loa_lower": round(loa_lower, 4),
        "loa_upper": round(loa_upper, 4),
        "mean_values": (method_a + method_b) / 2,
    }


def standard_error_of_measurement(icc: float, sd_total: float) -> float:
    """Compute Standard Error of Measurement (SEM) from ICC and total SD.

    SEM = SD_total * sqrt(1 - ICC)
    """
    return float(sd_total * np.sqrt(1 - icc))


def minimal_detectable_change(sem: float, z: float = 1.96) -> float:
    """Compute Minimal Detectable Change (MDC95) from SEM.

    MDC95 = SEM * z * sqrt(2)
    """
    return float(sem * z * np.sqrt(2))


# ---------------------------------------------------------------------------
# Aim 3 — Clinical validity against MAS and Tardieu Scale
# ---------------------------------------------------------------------------

def spearman_correlation(x: np.ndarray, y: np.ndarray) -> dict:
    """Compute Spearman rank correlation with 95% confidence interval.

    Args:
        x: Continuous pendulum metric values.
        y: Ordinal clinical scale scores (MAS or Tardieu).

    Returns:
        Dict with keys: rho, p_value, ci_lower, ci_upper.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    rho, p_value = stats.spearmanr(x, y)

    # Fisher z-transformation for CI
    n = len(x)
    z_r = np.arctanh(rho)
    se = 1 / np.sqrt(n - 3)
    ci_lower = float(np.tanh(z_r - 1.96 * se))
    ci_upper = float(np.tanh(z_r + 1.96 * se))

    return {
        "rho": round(float(rho), 4),
        "p_value": round(float(p_value), 6),
        "ci_lower": round(ci_lower, 4),
        "ci_upper": round(ci_upper, 4),
    }


def receiver_operating_characteristic(
    y_true: np.ndarray,
    y_score: np.ndarray,
) -> dict:
    """Compute ROC-AUC for discriminating clinically defined spasticity categories.

    Args:
        y_true: Binary labels (0 = no/mild spasticity, 1 = moderate/severe).
        y_score: Continuous metric scores from the pendulum framework.

    Returns:
        Dict with keys: auc, fpr, tpr, thresholds.
    """
    from sklearn.metrics import roc_auc_score, roc_curve

    fpr, tpr, thresholds = roc_curve(y_true, y_score)
    auc = float(roc_auc_score(y_true, y_score))

    return {
        "auc": round(auc, 4),
        "fpr": fpr,
        "tpr": tpr,
        "thresholds": thresholds,
    }
