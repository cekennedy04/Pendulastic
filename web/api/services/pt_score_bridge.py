"""
services/pt_score_bridge.py
============================
Calls compute_pt_params() and related functions from the project-root
pendulastic_pt_score.py without subprocess overhead.

The bridge imports the module dynamically so that the API layer never hard-
codes paths: the project root is prepended to sys.path at import time.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_hpe_csv(csv_path: str) -> tuple[np.ndarray, np.ndarray]:
    """
    Parse an HPE worker output CSV (columns: frame, time_sec, …, knee_angle_deg)
    and return (t_sec, knee_angle_deg) arrays with NaN rows dropped.
    """
    df = pd.read_csv(csv_path)
    t  = df["time_sec"].values.astype(float)
    a  = df["knee_angle_deg"].values.astype(float)
    t -= t[0]
    return t, a


class PTScoreBridge:
    """
    Wraps the existing compute_pt_params / pt_to_mas pipeline.
    Returns a dict that maps 1:1 onto AnalysisResponse.
    """

    def compute(self, csv_path: str, trial_meta: dict) -> dict[str, Any]:
        from pendulastic_pt_score import (
            compute_pt_params,
            compute_pt_score_simple,
            pt_to_mas,
        )

        t, angle = _load_hpe_csv(csv_path)
        params   = compute_pt_params(t, angle)

        if params is None:
            raise ValueError("Insufficient oscillation data — trial cannot be scored.")

        pt_simple = compute_pt_score_simple(params)
        mas       = pt_to_mas(pt_simple)

        def _safe(v: float) -> float:
            return float(v) if math.isfinite(v) else 0.0

        return {
            "trial_id":    trial_meta["id"],
            "mas_estimate": mas,
            "pt_score":    round(pt_simple, 4),
            "parameters": {
                "R2n":              _safe(params["R2n"]),
                "N":                _safe(params["N"]),
                "phi_max_ratio":    _safe(params["phi_max_ratio"]),
                "omega_max_n":      _safe(params["omega_max_n"]),
                "omega_peak_deg_s": _safe(params.get("omega_peak_deg_s", float("nan"))),
                "f":                _safe(params["f"]),
                "area_ratio":       _safe(params["area_ratio"]),
            },
            "fidelity": {
                "passed":          not params.get("quality_warn", False),
                "area_ratio_warn": bool(params.get("quality_warn", False)),
                "spasticity_type": params.get("spasticity_type", "unknown"),
                "A0_deg":          round(_safe(params["A0_deg"]), 2),
                "neutral_deg":     round(_safe(params["neutral_deg"]), 2),
            },
            # Truncate time-series to 4000 points to keep response size bounded
            "time_series": _subsample({
                "t":     t.tolist(),
                "angle": angle.tolist(),
                "t_r":   params["t_r"].tolist(),
                "phi":   params["phi"].tolist(),
            }, max_points=4000),
        }


def _subsample(series: dict, max_points: int) -> dict:
    """Down-sample all series to at most max_points for transport efficiency."""
    out: dict = {}
    for key, arr in series.items():
        if len(arr) > max_points:
            step = len(arr) // max_points
            out[key] = arr[::step]
        else:
            out[key] = arr
    return out
