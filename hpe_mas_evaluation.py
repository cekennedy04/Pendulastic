"""
hpe_mas_evaluation.py
=====================
Compare PT/MAS scores from every HPE model file in Recordings/ against the
OptiTrack ground truth from pendulastic_pt_score.py.

Outputs (Model_Analysis_Outputs/HPE_Comparison/):
  - hpe_mas_comparison.csv       — trial × model matrix of PT + MAS scores
  - hpe_accuracy_summary.csv     — per-model accuracy metrics
  - hpe_mas_heatmap.png          — MAS grade heatmap across trials and models
  - hpe_accuracy_bar.png         — accuracy and correlation bar chart

Run:
  .venv\\Scripts\\python.exe hpe_mas_evaluation.py
"""
from __future__ import annotations

import math
import os
import re
import sys
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd

# ── Import PT scoring functions from the main script ──────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pendulastic_pt_score import (
    compute_pt_params, compute_pt_score_simple, pt_to_mas,
    BASE_DIR, OUT_DIR as OPTI_OUT_DIR,
)

# ── Paths ──────────────────────────────────────────────────────────────────────
RECORDINGS_ROOT = os.path.join(BASE_DIR, "Recordings")
OUT_DIR         = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "HPE_Comparison")
OPTI_CSV        = os.path.join(OPTI_OUT_DIR, "pendulum_test_scores.csv")
os.makedirs(OUT_DIR, exist_ok=True)

# ── Colours matching the PT score plots ───────────────────────────────────────
_BG = "#12172a"; _PANEL = "#1c2340"; _HDR = "#252e50"
_MAS_COLOR = {
    "0":  "#00E87A", "1": "#A0E030", "1+": "#FFE000",
    "2":  "#FF9000", "3": "#FF4500", "4":  "#FF1A1A",
    "?":  "#444466",
}
_MAS_GRADES = ["0", "1", "1+", "2", "3", "4", "?"]
_MAS_NUMS   = {"0": 0, "1": 1, "1+": 1.5, "2": 2, "3": 3, "4": 4, "?": -1}


# ══════════════════════════════════════════════════════════════════════════════
# Discovery helpers
# ══════════════════════════════════════════════════════════════════════════════

def discover_hpe_files() -> list[dict]:
    """
    Walk Recordings/ and return one dict per HPE CSV file with keys:
      pid, pos, trial, model, csv_path
    """
    records = []
    pat = re.compile(
        r"P_(.+?)_Pos_(\d+)_H_Joint-Level_T_(\d+)_(.+?)\.csv$", re.I
    )
    for root, _, files in os.walk(RECORDINGS_ROOT):
        for fn in files:
            m = pat.match(fn)
            if not m:
                continue
            pid, pos, trial, model = m.group(1), m.group(2), m.group(3), m.group(4)
            records.append({
                "pid":   pid,
                "pos":   pos,
                "trial": trial,
                "model": model,
                "csv_path": os.path.join(root, fn),
            })
    return records


def model_family(model_str: str) -> str:
    """Return the base model family (e.g. 'rtmpose_m_0.3' → 'rtmpose_m')."""
    # Strip trailing confidence threshold (last underscore + number)
    parts = model_str.rsplit("_", 1)
    try:
        float(parts[-1])
        return parts[0]
    except ValueError:
        return model_str


# ══════════════════════════════════════════════════════════════════════════════
# PT score from an HPE CSV file
# ══════════════════════════════════════════════════════════════════════════════

def hpe_pt_score(csv_path: str) -> dict | None:
    """
    Load a single HPE model CSV, compute 4-param PT score and MAS grade.
    Returns None if insufficient data.
    """
    try:
        df = pd.read_csv(csv_path)
        if "time_sec" not in df.columns or "knee_angle_deg" not in df.columns:
            return None
        df = df.dropna(subset=["time_sec", "knee_angle_deg"])
        if len(df) < 30:
            return None
        t   = df["time_sec"].values.astype(float)
        ang = df["knee_angle_deg"].values.astype(float)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            params = compute_pt_params(t, ang)
        if params is None:
            return None
        pt  = compute_pt_score_simple(params)
        mas = pt_to_mas(pt)
        return {
            "pt":     round(pt, 4),
            "mas":    mas,
            "N":      float(params.get("N", 0)),
            "A0_deg": float(params.get("A0_deg", 0)),
        }
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Canonical model selection  — one representative per model family per trial
# ══════════════════════════════════════════════════════════════════════════════

# Priority order within a model family (lower index = preferred)
_CONF_PREF = ["0.3", "0.35", "0.5", "0.2", "0.6", "0.75", "0.1"]

def pick_canonical(group: pd.DataFrame) -> pd.DataFrame:
    """
    Within a group of rows sharing the same (pid, pos, trial, family),
    return the single row with the highest-priority configuration.
    """
    def _conf_rank(model_str: str) -> int:
        # Last token after the family name is the confidence
        suffix = model_str[len(model_str.split("_")[0]):]
        for i, c in enumerate(_CONF_PREF):
            if model_str.endswith(c):
                return i
        return 99
    group = group.copy()
    group["_rank"] = group["model"].map(_conf_rank)
    return group.nsmallest(1, "_rank").drop(columns="_rank")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    sep = "=" * 72
    print(f"\n{sep}\n  HPE MAS Evaluation vs OptiTrack Ground Truth\n{sep}\n")

    # ── Load OptiTrack reference ──────────────────────────────────────────────
    if not os.path.exists(OPTI_CSV):
        print(f"OptiTrack CSV not found: {OPTI_CSV}\nRun pendulastic_pt_score.py first.")
        return
    opti_df = pd.read_csv(OPTI_CSV)
    # Keep only rows with valid PT scores; use 4-param as ground truth
    opti_df = opti_df.dropna(subset=["pt_score_4param"])
    opti_df["pid"]   = opti_df["pid"].astype(str)
    opti_df["pos"]   = opti_df["pos"].astype(str)
    opti_df["trial"] = opti_df["trial"].astype(str)
    opti_df["quality_warn"] = opti_df["quality_warn"].fillna("")
    opti_df = opti_df[opti_df["quality_warn"] == ""]  # exclude unreliable duo trials

    opti_key = {
        (r["pid"], r["pos"], r["trial"]): {
            "opti_pt":     r["pt_score_4param"],
            "opti_mas":    r["est_MAS_4param"],
            "is_healthy":  str(r["pid"]).startswith(("0", "1", "2")),
            "opti_N":      float(r["N"])      if pd.notna(r["N"])      else 0.0,
            "opti_A0_deg": float(r["A0_deg"]) if pd.notna(r["A0_deg"]) else 0.0,
        }
        for _, r in opti_df.iterrows()
    }
    print(f"OptiTrack reference: {len(opti_key)} valid trials")

    # ── Discover and score all HPE files ─────────────────────────────────────
    hpe_files = discover_hpe_files()
    print(f"HPE files found: {len(hpe_files)}\nScoring...\n")

    rows = []
    for rec in hpe_files:
        key = (rec["pid"], rec["pos"], rec["trial"])
        if key not in opti_key:
            continue   # no OptiTrack reference for this trial → skip

        result = hpe_pt_score(rec["csv_path"])
        if result is None:
            continue

        family = model_family(rec["model"])
        ref    = opti_key[key]
        rows.append({
            "pid":          rec["pid"],
            "pos":          rec["pos"],
            "trial":        rec["trial"],
            "model":        rec["model"],
            "family":       family,
            "is_healthy":   ref["is_healthy"],
            "opti_pt":      ref["opti_pt"],
            "opti_mas":     ref["opti_mas"],
            "opti_N":       ref["opti_N"],
            "opti_A0_deg":  ref["opti_A0_deg"],
            "hpe_pt":       result["pt"],
            "hpe_mas":      result["mas"],
            "hpe_N":        result["N"],
            "hpe_A0_deg":   result["A0_deg"],
            "delta_N":      result["N"]      - ref["opti_N"],
            "delta_A0":     result["A0_deg"] - ref["opti_A0_deg"],
            "mas_match":    result["mas"] == ref["opti_mas"],
            "pt_diff":      abs(result["pt"] - ref["opti_pt"]),
        })

    if not rows:
        print("No matching HPE + OptiTrack trial pairs found."); return

    df = pd.DataFrame(rows)

    # ── Pick canonical config per model family per trial ──────────────────────
    # For each (pid, pos, trial, family), pick the row with best-priority conf.
    conf_rank = {c: i for i, c in enumerate(_CONF_PREF)}
    def _row_rank(model_str: str) -> int:
        for c, r in conf_rank.items():
            if model_str.endswith(c): return r
        return 99
    df["_rank"] = df["model"].map(_row_rank)
    df_canon = (
        df.sort_values("_rank")
          .groupby(["pid", "pos", "trial", "family"], sort=False)
          .first()
          .reset_index()
          .drop(columns=["_rank"])
    )
    print(f"Canonical comparisons: {len(df_canon)} (trial × model family)")

    # ── Accuracy metrics per model family ─────────────────────────────────────
    accuracy_rows = []
    for family, grp in df_canon.groupby("family"):
        n         = len(grp)
        mas_match = grp["mas_match"].mean()
        # Adjacent agreement: |num(opti) - num(hpe)| <= 1
        adj = grp.apply(
            lambda r: abs(_MAS_NUMS.get(r["opti_mas"], -1)
                          - _MAS_NUMS.get(r["hpe_mas"], -1)) <= 1, axis=1
        ).mean()
        # Healthy correct: healthy subject, HPE MAS=0
        healthy = grp[grp["is_healthy"]]
        h_correct = (healthy["hpe_mas"] == "0").mean() if len(healthy) else float("nan")
        # Spastic correct: unhealthy subject, HPE MAS≥1 (not "0")
        spastic = grp[~grp["is_healthy"]]
        s_correct = (spastic["hpe_mas"] != "0").mean() if len(spastic) else float("nan")
        # Correlation
        corr = grp[["opti_pt", "hpe_pt"]].corr().iloc[0, 1]
        mae  = grp["pt_diff"].mean()

        accuracy_rows.append({
            "model_family":        family,
            "n_trials":            n,
            "n_healthy":           len(healthy),
            "n_spastic":           len(spastic),
            "exact_MAS_agree_%":   round(mas_match * 100, 1),
            "adj_MAS_agree_%":     round(adj * 100, 1),
            "healthy_MAS0_%":      round(h_correct * 100, 1) if not math.isnan(h_correct) else float("nan"),
            "spastic_MAS1+_%":     round(s_correct * 100, 1) if not math.isnan(s_correct) else float("nan"),
            "PT_correlation":      round(corr, 3),
            "PT_MAE":              round(mae, 4),
            "mean_delta_N":        round(grp["delta_N"].mean(), 2),
            "mean_abs_delta_N":    round(grp["delta_N"].abs().mean(), 2),
            "mean_delta_A0_deg":   round(grp["delta_A0"].mean(), 2),
            "mean_abs_delta_A0_deg": round(grp["delta_A0"].abs().mean(), 2),
        })

    acc_df = pd.DataFrame(accuracy_rows).sort_values("adj_MAS_agree_%", ascending=False)

    # ── Save CSVs ─────────────────────────────────────────────────────────────
    detail_csv = os.path.join(OUT_DIR, "hpe_mas_comparison.csv")
    summ_csv   = os.path.join(OUT_DIR, "hpe_accuracy_summary.csv")
    df_canon.to_csv(detail_csv, index=False)
    acc_df.to_csv(summ_csv, index=False)

    # ── Console summary ───────────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"{'Model':<24} {'n':>4}  {'dN(mean)':>9} {'|dN|':>6}  {'dA0(mean)':>10} {'|dA0|':>6}  {'Adj%':>5}")
    print("-" * 72)
    for _, r in acc_df.iterrows():
        print(f"{r['model_family']:<24} {r['n_trials']:>4}  "
              f"{r['mean_delta_N']:>+9.2f} {r['mean_abs_delta_N']:>6.2f}  "
              f"{r['mean_delta_A0_deg']:>+10.2f} {r['mean_abs_delta_A0_deg']:>6.2f}  "
              f"{r['adj_MAS_agree_%']:>5.1f}%")
    print(sep)

    # ── Plots ─────────────────────────────────────────────────────────────────
    _plot_heatmap(df_canon, acc_df)
    _plot_accuracy(acc_df)
    _plot_param_diffs(df_canon)
    print(f"\nOutputs -> {OUT_DIR}")


# ══════════════════════════════════════════════════════════════════════════════
# Visualisations
# ══════════════════════════════════════════════════════════════════════════════

def _mas_num(mas: str) -> float:
    return float(_MAS_NUMS.get(str(mas), -1))


def _trial_label(pid: str, pos: str, trial: str) -> str:
    return f"P{pid} Pos{pos} T{trial}"


def _plot_heatmap(df: pd.DataFrame, acc_df: pd.DataFrame) -> None:
    """MAS grade heatmap: rows=trials, cols=models (OptiTrack first)."""

    # Build trial list in a sensible order (healthy first, then spastic)
    trials = (
        df[["pid", "pos", "trial", "is_healthy", "opti_mas"]]
        .drop_duplicates()
        .sort_values(["is_healthy", "pid", "pos", "trial"], ascending=[False, True, True, True])
    )
    trial_labels = [_trial_label(r.pid, r.pos, r.trial) for _, r in trials.iterrows()]
    opti_mas     = [r.opti_mas for _, r in trials.iterrows()]

    # Build sorted model list (by adjacent agreement, descending)
    families = list(acc_df["model_family"])

    cols = ["OptiTrack"] + families
    n_rows, n_cols = len(trial_labels), len(cols)

    # Fill grade matrix
    mat = np.full((n_rows, n_cols), -1.0)
    for ri, (_, trow) in enumerate(trials.iterrows()):
        mat[ri, 0] = _mas_num(trow.opti_mas)
        for ci, fam in enumerate(families, 1):
            sub = df[(df["pid"] == trow.pid) &
                     (df["pos"] == trow.pos) &
                     (df["trial"] == trow.trial) &
                     (df["family"] == fam)]
            if not sub.empty:
                mat[ri, ci] = _mas_num(sub.iloc[0]["hpe_mas"])

    # Colormap: green→red for MAS 0–4, grey for missing
    cmap_vals = ["#00E87A", "#A0E030", "#FFE000", "#FF9000", "#FF4500", "#FF1A1A"]
    cmap      = mcolors.ListedColormap(["#2A2A3A"] + cmap_vals)
    bounds    = [-1.5, -0.5, 0.5, 1.25, 1.75, 2.5, 3.5, 4.5]
    norm      = mcolors.BoundaryNorm(bounds, cmap.N)

    fig_w = max(14, n_cols * 0.9 + 3)
    fig_h = max(8,  n_rows * 0.36 + 2.5)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), facecolor=_BG)
    ax.set_facecolor(_BG)

    im = ax.imshow(mat, aspect="auto", cmap=cmap, norm=norm, interpolation="none")

    # Annotate each cell with MAS text
    grade_str = {-1: "", 0: "0", 1: "1", 1.5: "1+", 2: "2", 3: "3", 4: "4"}
    for ri in range(n_rows):
        for ci in range(n_cols):
            v = mat[ri, ci]
            txt = grade_str.get(v, "")
            if txt:
                ax.text(ci, ri, txt, ha="center", va="center",
                        fontsize=9, color="black", fontweight="bold")

    # Divider between healthy and spastic
    n_healthy = int(trials["is_healthy"].sum())
    if 0 < n_healthy < n_rows:
        ax.axhline(n_healthy - 0.5, color="#FFFFFF", lw=2, ls="--", alpha=0.5)
        ax.text(n_cols - 0.5, n_healthy - 0.5,
                " ← Healthy / Spastic →", color="#AABBCC",
                fontsize=9, va="center", ha="right")

    # X axis: model names
    ax.set_xticks(range(n_cols))
    ax.set_xticklabels(cols, rotation=40, ha="right", fontsize=9, color="#C0D0F0")
    ax.set_yticks(range(n_rows))
    ax.set_yticklabels(trial_labels, fontsize=9, color="#C0D0F0")
    ax.set_xlim(-0.5, n_cols - 0.5)
    ax.set_ylim(n_rows - 0.5, -0.5)

    for sp in ax.spines.values(): sp.set_edgecolor("#3A4A6A")

    # Colour bar
    cbar = plt.colorbar(im, ax=ax, pad=0.01, shrink=0.6)
    cbar.set_ticks([-1, 0, 1, 1.5, 2, 3, 4])
    cbar.set_ticklabels(["n/a", "0", "1", "1+", "2", "3", "4"])
    cbar.ax.tick_params(colors="#C0D0F0", labelsize=9)
    cbar.ax.set_ylabel("MAS Grade", color="#90A8CC", fontsize=10)

    fig.text(0.5, 0.99,
             "HPE Model MAS Scores vs OptiTrack Ground Truth  (4-param PT)",
             ha="center", va="top", color="#D0DEFF", fontsize=14, fontweight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.97])
    out = os.path.join(OUT_DIR, "hpe_mas_heatmap.png")
    plt.savefig(out, dpi=130, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()
    print(f"  -> {os.path.basename(out)}")


def _plot_param_diffs(df: pd.DataFrame) -> None:
    """
    Two-panel box-plot: HPE minus OptiTrack for N (swings) and A0 (first angle),
    one box per model family, sorted by median absolute N error.
    """
    families = (
        df.groupby("family")["delta_N"].apply(lambda x: x.abs().median())
          .sort_values()
          .index.tolist()
    )
    # Drop families with <3 observations (too noisy for box plot)
    families = [f for f in families if len(df[df["family"] == f]) >= 3]
    if not families:
        return

    n = len(families)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, max(6, n * 0.55 + 1.5)), facecolor=_BG)
    fig.text(0.5, 0.99,
             "HPE vs OptiTrack: Parameter Differences  (HPE - OptiTrack)",
             ha="center", va="top", color="#D0DEFF", fontsize=13, fontweight="bold")

    def _bplot(ax, col, title, unit, zero_color="#00E8C8"):
        data = [df[df["family"] == f][col].dropna().values for f in families]
        bp = ax.boxplot(
            data, vert=False, patch_artist=True,
            medianprops=dict(color="#FFFFFF", lw=2),
            whiskerprops=dict(color="#6080A0"),
            capprops=dict(color="#6080A0"),
            flierprops=dict(marker="o", markerfacecolor="#FF6060",
                            markersize=4, linestyle="none"),
        )
        # Color boxes by median signed error: green=zero, orange=positive, blue=negative
        for patch, fam in zip(bp["boxes"], families):
            med = np.median(df[df["family"] == fam][col].dropna().values)
            patch.set_facecolor("#336644" if abs(med) < 0.5 else ("#884400" if med > 0 else "#224488"))
            patch.set_alpha(0.85)

        ax.axvline(0, color=zero_color, lw=1.5, ls="--", alpha=0.7)
        ax.set_facecolor(_PANEL)
        ax.set_yticks(range(1, n + 1))
        ax.set_yticklabels(families, fontsize=10, color="#C0D0F0")
        ax.set_xlabel(f"HPE - OptiTrack  ({unit})", color="#90A8CC", fontsize=11)
        ax.set_title(title, color="#D0DEFF", fontsize=12, fontweight="bold", pad=8)
        ax.tick_params(axis="x", colors="#A0B0C8", labelsize=10)
        for sp in ax.spines.values(): sp.set_edgecolor("#3A4A6A")
        # Median labels
        for yi, fam in enumerate(families, 1):
            vals = df[df["family"] == fam][col].dropna().values
            med  = np.median(vals)
            mae  = np.abs(vals).mean()
            ax.text(ax.get_xlim()[1] * 0.98, yi,
                    f" med={med:+.1f}  MAE={mae:.1f}",
                    va="center", ha="right", fontsize=8, color="#99AABB")

    _bplot(ax1, "delta_N",   "Number of Swings  (N)", "swings")
    _bplot(ax2, "delta_A0",  "First Swing Angle  (A0)", "degrees")

    plt.tight_layout(rect=[0, 0, 1, 0.97])
    out = os.path.join(OUT_DIR, "hpe_param_diffs.png")
    plt.savefig(out, dpi=130, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()
    print(f"  -> {os.path.basename(out)}")


def _plot_accuracy(acc_df: pd.DataFrame) -> None:
    """Bar chart: exact MAS agreement, healthy→0 rate, spastic→≥1 rate, correlation."""
    df = acc_df[acc_df["n_trials"] >= 3].copy()   # drop models with too few trials
    if df.empty:
        return

    df = df.sort_values("adj_MAS_agree_%", ascending=True)
    labels = df["model_family"].tolist()
    n      = len(labels)
    y      = np.arange(n)
    h      = max(6, n * 0.55)

    fig, axes = plt.subplots(1, 3, figsize=(15, h), facecolor=_BG)
    titles  = ["Exact MAS Agreement (%)", "Healthy → MAS 0 (%)", "Spastic → MAS ≥1 (%)"]
    cols_d  = ["exact_MAS_agree_%", "healthy_MAS0_%", "spastic_MAS1+_%"]
    bar_c   = ["#00C8A0", "#00E87A", "#FF8040"]

    for ax, title, col, bc in zip(axes, titles, cols_d, bar_c):
        ax.set_facecolor(_PANEL)
        vals = df[col].fillna(0).tolist()
        bars = ax.barh(y, vals, color=bc, alpha=0.85, height=0.65)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=10, color="#C0D0F0")
        ax.set_xlim(0, 105)
        ax.set_xlabel("%", color="#90A8CC", fontsize=11)
        ax.set_title(title, color="#D0DEFF", fontsize=12, fontweight="bold", pad=8)
        ax.tick_params(axis="x", colors="#A0B0C8", labelsize=10)
        ax.axvline(100, color="#FFFFFF", lw=1, ls="--", alpha=0.3)
        for sp in ax.spines.values(): sp.set_edgecolor("#3A4A6A")
        # Value labels
        for bar, v in zip(bars, vals):
            if v > 0:
                ax.text(min(v + 1, 101), bar.get_y() + bar.get_height() / 2,
                        f"{v:.0f}%", va="center", fontsize=9, color="#E0F0FF")
        # N labels on first panel
        if col == "exact_MAS_agree_%":
            for yi, (_, row) in zip(y, df.iterrows()):
                ax.text(1, yi, f"n={int(row['n_trials'])}", va="center",
                        fontsize=8, color="#667788")

    fig.text(0.5, 0.99,
             "HPE Model Accuracy vs OptiTrack PT/MAS  (models with ≥3 trials)",
             ha="center", va="top", color="#D0DEFF", fontsize=13, fontweight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.97])
    out = os.path.join(OUT_DIR, "hpe_accuracy_bar.png")
    plt.savefig(out, dpi=130, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()
    print(f"  -> {os.path.basename(out)}")


if __name__ == "__main__":
    main()
