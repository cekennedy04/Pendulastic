"""
p13_vs_p5_comparison.py
========================
Direct P13-vs-P5 comparison: highlights (1) differences in their baseline
spasticity "signature" (the 7 Popovic parameters at the first timepoint) and
(2) differences in how each participant changed over their 3 recorded
timepoints (PT-score trajectory + per-parameter delta from first to last
timepoint). Same 7-parameter scoring, release-alignment, and representative-
trial selection as p13_full_report.py / p5_full_report.py (pt_report_common.py)
so all three figures are numerically consistent.

Timepoints are aligned by ORDINAL position, not label text -- P13 uses
Pre/Post/+1wk Post, P5 uses Initial/Post/+1wk Post. Both are baseline / immediate
post-intervention / one-week-later, so index 0/1/2 line up conceptually even
though the underlying interventions differ (this is a shape-of-trajectory
comparison, not a claim that the two participants received the same protocol).

Run:
    .venv\\Scripts\\python.exe p13_vs_p5_comparison.py
"""
from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import p13_full_report as p13
import p5_full_report as p5
import pt_report_common as common

OUT_DIR = common.OUT_DIR

P13_COLOR = '#1f77b4'   # blue
P5_COLOR = '#ff7f0e'    # orange
STAGE_LABELS = ["Baseline", "Post", "+1wk Post"]


def _stage_series(by_leg_tp, timepoints, leg):
    """Per-stage (mean pt7, all vals) list, aligned by ordinal position."""
    out = []
    for tp_key, _, _ in timepoints:
        trials = by_leg_tp.get((leg, tp_key), [])
        vals = [r["pt7"] for r in trials]
        out.append(vals)
    return out


def _param_means(by_leg_tp, timepoints, leg, stage_idx):
    tp_key = timepoints[stage_idx][0]
    trials = by_leg_tp.get((leg, tp_key), [])
    if not trials:
        return None
    return {k: float(np.mean([r[k] for r in trials])) for k in common._PARAM_KEYS}


def make_comparison_figure(p13_data, p5_data):
    fig, axes = plt.subplots(3, 2, figsize=(15, 14), facecolor='white')

    for col_idx, (leg, leg_label) in enumerate((("left", "Left"), ("right", "Right"))):

        # ── Row 1: PT-score trajectory overlay ──────────────────────────────
        ax = axes[0, col_idx]
        ax.set_facecolor('white')
        y_all = ([v for vals in _stage_series(p13_data, p13.TIMEPOINTS, leg) for v in vals] +
                [v for vals in _stage_series(p5_data, p5.TIMEPOINTS, leg) for v in vals])
        y_max = (max(y_all) if y_all else 1.6) * 1.15
        for (lo, hi), zcolor in zip(zip(common.ZONE_EDGES[:-1], common.ZONE_EDGES[1:]), common.ZONE_COLORS):
            ax.axhspan(lo, min(hi, y_max), facecolor=zcolor, alpha=0.3, zorder=0)

        x_pts = [0, 1, 2]
        rng = np.random.RandomState(13)
        for label, by_leg_tp, timepoints, color, marker in (
            ("P13", p13_data, p13.TIMEPOINTS, P13_COLOR, 'o'),
            ("P5", p5_data, p5.TIMEPOINTS, P5_COLOR, 's'),
        ):
            series = _stage_series(by_leg_tp, timepoints, leg)
            means = [float(np.mean(v)) if v else np.nan for v in series]
            for xc, vals in zip(x_pts, series):
                for v in vals:
                    ax.scatter(xc + rng.uniform(-0.06, 0.06), v, color=color,
                             s=16, alpha=0.4, zorder=2)
            valid = [(xc, m) for xc, m in zip(x_pts, means) if np.isfinite(m)]
            if valid:
                ax.plot([p[0] for p in valid], [p[1] for p in valid], color=color,
                       linewidth=2.2, marker=marker, markersize=7, zorder=4, label=label)
            if len(valid) >= 2:
                delta = valid[-1][1] - valid[0][1]
                ax.annotate(f"{label} Δ{delta:+.2f}", xy=valid[-1], xytext=(8, 0),
                          textcoords='offset points', color=color, fontsize=8,
                          fontweight='bold', va='center')

        ax.set_title(f'{leg_label} Leg – PT Score Trajectory (P13 vs P5)',
                    fontsize=10, fontweight='bold', pad=10)
        ax.set_xticks(x_pts)
        ax.set_xticklabels(STAGE_LABELS, fontsize=8)
        ax.set_ylabel('PT Score (7-parameter)', fontsize=8)
        ax.tick_params(labelsize=8)
        ax.set_xlim(-0.4, 2.6)
        ax.set_ylim(0, y_max)
        ax.legend(loc='lower right', fontsize=8, framealpha=0.9)

        # ── Row 2: Baseline spasticity signature (first timepoint) ─────────
        ax = axes[1, col_idx]
        ax.set_facecolor('#f8f9fa')
        ax.grid(True, color=common.BG_GRID, linestyle='-', linewidth=0.8, axis='y')
        x = np.arange(len(common._PARAM_KEYS))
        width = 0.35
        for offset, (label, by_leg_tp, timepoints, color) in zip(
            (-1, 1),
            (("P13", p13_data, p13.TIMEPOINTS, P13_COLOR), ("P5", p5_data, p5.TIMEPOINTS, P5_COLOR)),
        ):
            means = _param_means(by_leg_tp, timepoints, leg, 0)
            vals = [means[k] if means else 0.0 for k in common._PARAM_KEYS]
            ax.bar(x + offset * width / 2, vals, width, label=label, color=color, alpha=0.85)
        ax.set_title(f'{leg_label} Leg – Baseline Spasticity Signature (P13 vs P5)',
                    fontsize=10, fontweight='bold', pad=10)
        ax.set_xticks(x)
        ax.set_xticklabels(common.PARAM_DISPLAY, fontsize=8)
        ax.set_ylabel('Baseline value (log scale)', fontsize=8)
        ax.set_yscale('log')
        ax.set_ylim(bottom=1e-3)
        ax.tick_params(labelsize=8)
        ax.legend(loc='upper right', fontsize=8, framealpha=0.9)

        # ── Row 3: Parameter change, Baseline -> +1wk Post ───────────────────
        ax = axes[2, col_idx]
        ax.set_facecolor('#f8f9fa')
        ax.grid(True, color=common.BG_GRID, linestyle='-', linewidth=0.8, axis='y')
        for offset, (label, by_leg_tp, timepoints, color) in zip(
            (-1, 1),
            (("P13", p13_data, p13.TIMEPOINTS, P13_COLOR), ("P5", p5_data, p5.TIMEPOINTS, P5_COLOR)),
        ):
            m0 = _param_means(by_leg_tp, timepoints, leg, 0)
            m2 = _param_means(by_leg_tp, timepoints, leg, len(timepoints) - 1)
            if m0 and m2:
                deltas = [m2[k] - m0[k] for k in common._PARAM_KEYS]
            else:
                deltas = [0.0] * len(common._PARAM_KEYS)
            ax.bar(x + offset * width / 2, deltas, width, label=label, color=color, alpha=0.85)
        ax.axhline(0, color='#888888', linewidth=0.8)
        ax.set_title(f'{leg_label} Leg – Parameter Change, Baseline→+1wk Post (P13 vs P5)',
                    fontsize=10, fontweight='bold', pad=10)
        ax.set_xticks(x)
        ax.set_xticklabels(common.PARAM_DISPLAY, fontsize=8)
        ax.set_ylabel('Δ value (symlog scale)', fontsize=8)
        ax.set_yscale('symlog', linthresh=0.1)
        ax.tick_params(labelsize=8)
        ax.legend(loc='upper right', fontsize=8, framealpha=0.9)

    fig.suptitle(
        "Participant 13 vs Participant 5 — Spasticity Signature & Change Comparison\n"
        "Both use the full 7-parameter Popovic PT score. Timepoints aligned by ordinal position "
        "(baseline / post / +1wk), not by matching protocol -- P13's conditions differ from P5's.",
        fontsize=10, y=0.997, color='#333333')
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    out_path = os.path.join(OUT_DIR, "P13_vs_P5_comparison.png")
    fig.savefig(out_path, dpi=150, facecolor='white')
    plt.close(fig)
    print(f"-> {out_path}")


if __name__ == "__main__":
    p13_data = p13.collect_all()
    p5_data = p5.collect_all()
    make_comparison_figure(p13_data, p5_data)
