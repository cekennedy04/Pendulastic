"""
ms_vs_healthy_analysis.py
=========================
Compare Wartenberg Pendulum Test parameters between MS and healthy control
participants using OptiTrack ground-truth PT scores.

Group assignment:
  Priority 1 — reads diagnosis from  Recordings/Participant_<pid>/metadata.json
  Fallback  — "MS" / "Stroke" if pid numeric prefix >= 4 (odd convention)
              "healthy" otherwise
Classification map:
  diagnosis field contains "ms" or "stroke"  → "MS"
  diagnosis field contains "control"         → "Healthy"

Statistics (non-parametric, n is small):
  • Mann-Whitney U  (two-sided)
  • Cliff's delta   (non-parametric effect size, –1 to +1)
  • Median ± IQR per group

Outputs  (Model_Analysis_Outputs/MS_vs_Healthy/):
  ms_vs_healthy_stats.csv        — statistical summary table
  ms_vs_healthy_boxplots.png     — 6-parameter box + strip chart
  ms_vs_healthy_radar.png        — radar: healthy ref vs mean vs MS mean
  ms_vs_healthy_ptscores.png     — PT score per trial, colour-coded by MAS

Run:
    .venv\\Scripts\\python.exe ms_vs_healthy_analysis.py
"""
from __future__ import annotations

import glob
import json
import math
import os
import sys
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd

# ── optional scipy for Mann-Whitney U ─────────────────────────────────────────
try:
    from scipy.stats import mannwhitneyu
    _SCIPY = True
except ImportError:
    _SCIPY = False
    print("scipy not found — install it for p-values:  pip install scipy")

# ── paths ─────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
OPTI_CSV    = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "PT_Scores",
                           "pendulum_test_scores.csv")
REC_ROOT    = os.path.join(BASE_DIR, "Recordings")
OUT_DIR     = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "MS_vs_Healthy")
os.makedirs(OUT_DIR, exist_ok=True)

# ── visual constants ───────────────────────────────────────────────────────────
_BG     = "#12172a"
_PANEL  = "#1c2340"
_G_COL  = "#00E87A"   # healthy = green
_MS_COL = "#FF5533"   # MS / clinical = orange-red
_MAS_COLOR = {
    "0": "#00E87A", "1": "#A0E030", "1+": "#FFE000",
    "2": "#FF9000", "3": "#FF4500", "4": "#FF1A1A", "?": "#555577",
}

# Parameters to compare (label, CSV column, healthy reference)
PARAMS = [
    ("Relaxation (R2n)",    "R2n",           0.91),
    ("Swing count (N)",     "N",             8.0),
    ("Excursion (phi_max)", "phi_max_ratio", 0.65),
    ("Peak vel. (omega_max)","omega_max_n",  4.5),
    ("First angle A0 (deg)","A0_deg",        None),
    ("PT Score (4-param)",  "pt_score_4param", 0.0),
]


# ══════════════════════════════════════════════════════════════════════════════
# Group classification
# ══════════════════════════════════════════════════════════════════════════════

def _load_metadata_diagnosis() -> dict[str, str]:
    """
    Scan all Recordings/Participant_*/metadata.json files.
    Returns {participant_id: "MS" | "Healthy" | "Unknown"}.
    """
    result = {}
    pattern = os.path.join(REC_ROOT, "Participant_*", "metadata.json")
    for path in glob.glob(pattern):
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
            pid   = str(meta.get("participant_id", "")).strip()
            diag  = str(meta.get("diagnosis", "")).lower().strip()
            if not pid:
                continue
            if "ms" in diag or "stroke" in diag or "motor impairment" in diag:
                group = "MS"
            elif "control" in diag or "unaffected" in diag or "healthy" in diag:
                group = "Healthy"
            else:
                group = "Unknown"
            result[pid] = group
        except Exception:
            pass
    return result


def _pid_fallback(pid: str) -> str:
    """Fallback group from numeric prefix: 0–3 = Healthy, 4+ = MS."""
    try:
        num = int(str(pid).split("_")[0])
        return "MS" if num >= 4 else "Healthy"
    except ValueError:
        return "Unknown"


def classify_pid(pid: str, meta_map: dict[str, str]) -> str:
    """Return 'MS', 'Healthy', or 'Unknown' for a PT score CSV row pid."""
    pid = str(pid)
    # Exact match first
    if pid in meta_map:
        return meta_map[pid]
    # Prefix match  (e.g. CSV pid="4_right", metadata pid="4_right")
    for k, v in meta_map.items():
        if pid.startswith(k) or k.startswith(pid):
            return v
    # Fallback
    return _pid_fallback(pid)


# ══════════════════════════════════════════════════════════════════════════════
# Statistics helpers
# ══════════════════════════════════════════════════════════════════════════════

def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    """
    Cliff's delta: proportion of (b > a) pairs minus (a > b) pairs.
    Range –1 (all a > b) to +1 (all b > a).  |d| > 0.33 = medium effect.
    """
    n = len(a) * len(b)
    if n == 0:
        return float("nan")
    pairs = sum((1 if bi > ai else (-1 if ai > bi else 0))
                for ai in a for bi in b)
    return pairs / n


def mann_whitney(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Return (U statistic, two-sided p-value). Returns (nan, nan) without scipy."""
    if not _SCIPY or len(a) == 0 or len(b) == 0:
        return float("nan"), float("nan")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stat, p = mannwhitneyu(a, b, alternative="two-sided")
    return float(stat), float(p)


def effect_label(d: float) -> str:
    ad = abs(d)
    if math.isnan(ad):  return "n/a"
    if ad < 0.147:      return "negligible"
    if ad < 0.330:      return "small"
    if ad < 0.474:      return "medium"
    return "large"


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    sep = "=" * 72
    print(f"\n{sep}\n  MS vs Healthy — Pendulum Test Parameter Analysis\n{sep}\n")

    if not os.path.exists(OPTI_CSV):
        print(f"OptiTrack CSV not found: {OPTI_CSV}\nRun pendulastic_pt_score.py first.")
        return

    df = pd.read_csv(OPTI_CSV)
    df["quality_warn"] = df["quality_warn"].fillna("")
    # Exclude duo artifact trials and any error rows
    df = df[df["quality_warn"] == ""]
    df = df.dropna(subset=["pt_score_4param"])
    df["pid"] = df["pid"].astype(str)

    meta_map = _load_metadata_diagnosis()
    print(f"Metadata loaded for {len(meta_map)} participant folders:")
    for pid, grp in sorted(meta_map.items()):
        print(f"  {pid:20s} -> {grp}")
    print()

    df["group"] = df["pid"].apply(lambda p: classify_pid(p, meta_map))

    healthy = df[df["group"] == "Healthy"].copy()
    ms      = df[df["group"] == "MS"].copy()
    unknown = df[df["group"] == "Unknown"].copy()

    print(f"Healthy trials : {len(healthy)}  (pids: {sorted(healthy['pid'].unique())})")
    print(f"MS trials      : {len(ms)}  (pids: {sorted(ms['pid'].unique())})")
    if len(unknown):
        print(f"Unknown trials : {len(unknown)}  (pids: {sorted(unknown['pid'].unique())})")
    print()

    if len(healthy) == 0 or len(ms) == 0:
        print("ERROR: Need at least one trial in each group to compare.")
        return

    # ── Per-parameter statistics ───────────────────────────────────────────────
    stat_rows = []
    print(f"\n{sep}")
    print(f"{'Parameter':<26} {'H med':>8} {'H IQR':>8}  {'MS med':>8} {'MS IQR':>8}  "
          f"{'p':>7} {'Cliff d':>8} {'Effect':>12}")
    print("-" * 90)

    for label, col, ref in PARAMS:
        if col not in df.columns:
            continue
        h_vals  = healthy[col].dropna().values.astype(float)
        ms_vals = ms[col].dropna().values.astype(float)
        if len(h_vals) == 0 or len(ms_vals) == 0:
            continue

        h_med   = float(np.median(h_vals))
        ms_med  = float(np.median(ms_vals))
        h_iqr   = float(np.percentile(h_vals, 75) - np.percentile(h_vals, 25))
        ms_iqr  = float(np.percentile(ms_vals, 75) - np.percentile(ms_vals, 25))
        d       = cliffs_delta(h_vals, ms_vals)
        _, p    = mann_whitney(h_vals, ms_vals)
        el      = effect_label(d)

        p_str = f"{p:.4f}" if not math.isnan(p) else "n/a"
        print(f"{label:<26} {h_med:>8.3f} {h_iqr:>8.3f}  {ms_med:>8.3f} {ms_iqr:>8.3f}  "
              f"{p_str:>7} {d:>+8.3f} {el:>12}")

        stat_rows.append({
            "parameter":      label,
            "column":         col,
            "healthy_ref":    ref,
            "healthy_n":      len(h_vals),
            "healthy_median": round(h_med, 4),
            "healthy_IQR":    round(h_iqr, 4),
            "ms_n":           len(ms_vals),
            "ms_median":      round(ms_med, 4),
            "ms_IQR":         round(ms_iqr, 4),
            "mann_whitney_p": round(p, 5) if not math.isnan(p) else None,
            "cliffs_delta":   round(d, 4),
            "effect_size":    el,
        })
    print(sep)

    stats_df = pd.DataFrame(stat_rows)
    stats_csv = os.path.join(OUT_DIR, "ms_vs_healthy_stats.csv")
    stats_df.to_csv(stats_csv, index=False)
    print(f"\nStats -> {stats_csv}")

    # ── Visualizations ────────────────────────────────────────────────────────
    _plot_boxplots(healthy, ms, stat_rows)
    _plot_radar(healthy, ms)
    _plot_pt_scores(healthy, ms)
    print(f"\nAll outputs -> {OUT_DIR}")


# ══════════════════════════════════════════════════════════════════════════════
# Plots
# ══════════════════════════════════════════════════════════════════════════════

def _jitter(n: int, width: float = 0.12) -> np.ndarray:
    np.random.seed(42)
    return (np.random.rand(n) - 0.5) * 2 * width


def _plot_boxplots(healthy: pd.DataFrame, ms: pd.DataFrame,
                   stat_rows: list[dict]) -> None:
    """2×3 grid of box + strip charts, one per parameter."""
    cols = [r["column"] for r in stat_rows]
    labels = [r["parameter"] for r in stat_rows]
    n = len(cols)
    ncols = 3
    nrows = math.ceil(n / ncols)

    fig, axes = plt.subplots(nrows, ncols, figsize=(14, 4.5 * nrows), facecolor=_BG)
    axes = np.array(axes).flatten()

    for i, (col, lbl) in enumerate(zip(cols, labels)):
        ax = axes[i]
        ax.set_facecolor(_PANEL)
        for sp in ax.spines.values():
            sp.set_edgecolor("#3A4A6A")

        h_vals = healthy[col].dropna().values.astype(float)
        m_vals = ms[col].dropna().values.astype(float)

        # Box plots
        bp = ax.boxplot(
            [h_vals, m_vals], positions=[0, 1],
            patch_artist=True,
            widths=0.35,
            medianprops=dict(color="#FFFFFF", lw=2.5),
            whiskerprops=dict(color="#8090B0", lw=1.5),
            capprops=dict(color="#8090B0", lw=1.5),
            flierprops=dict(marker="o", markerfacecolor="#FF6060", markersize=4),
        )
        bp["boxes"][0].set_facecolor(_G_COL + "55")
        bp["boxes"][0].set_edgecolor(_G_COL)
        bp["boxes"][1].set_facecolor(_MS_COL + "55")
        bp["boxes"][1].set_edgecolor(_MS_COL)

        # Strip (individual points)
        ax.scatter(_jitter(len(h_vals)), h_vals, color=_G_COL,
                   s=40, zorder=4, alpha=0.9, edgecolors="none")
        ax.scatter(1 + _jitter(len(m_vals)), m_vals, color=_MS_COL,
                   s=40, zorder=4, alpha=0.9, edgecolors="none")

        # Reference line
        ref = next((r["healthy_ref"] for r in stat_rows if r["column"] == col), None)
        if ref is not None:
            ax.axhline(ref, color="#FFD040", lw=1.2, ls="--", alpha=0.7,
                       label=f"Ref {ref}")

        # p-value annotation
        row = next((r for r in stat_rows if r["column"] == col), {})
        p = row.get("mann_whitney_p")
        d = row.get("cliffs_delta", float("nan"))
        el = row.get("effect_size", "")
        p_str = f"p={p:.4f}" if p is not None else "p=n/a"
        ax.set_title(lbl, color="#D0DEFF", fontsize=11, fontweight="bold", pad=6)
        ax.text(0.98, 0.97, f"{p_str}  |d|={abs(d):.2f} ({el})",
                transform=ax.transAxes, ha="right", va="top",
                color="#A0B8D0", fontsize=8)

        ax.set_xticks([0, 1])
        ax.set_xticklabels(
            [f"Healthy\n(n={len(h_vals)})", f"MS\n(n={len(m_vals)})"],
            color="#C0D0F0", fontsize=10
        )
        ax.tick_params(axis="y", colors="#A0B0C8", labelsize=9)

    # Hide unused subplots
    for j in range(n, len(axes)):
        axes[j].set_visible(False)

    _legend_patches = [
        mpatches.Patch(facecolor=_G_COL + "55", edgecolor=_G_COL, label="Healthy"),
        mpatches.Patch(facecolor=_MS_COL + "55", edgecolor=_MS_COL, label="MS"),
        plt.Line2D([0], [0], color="#FFD040", ls="--", lw=1.2, label="Healthy ref"),
    ]
    fig.legend(handles=_legend_patches, loc="lower center", ncol=3,
               facecolor=_PANEL, edgecolor="#3A4A6A", labelcolor="#C0D0F0", fontsize=10)

    fig.text(0.5, 0.99,
             "MS vs Healthy — Pendulum Test Parameters  (OptiTrack ground truth)",
             ha="center", va="top", color="#D0DEFF", fontsize=14, fontweight="bold")
    plt.tight_layout(rect=[0, 0.04, 1, 0.97])
    out = os.path.join(OUT_DIR, "ms_vs_healthy_boxplots.png")
    plt.savefig(out, dpi=130, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()
    print(f"  -> {os.path.basename(out)}")


def _plot_radar(healthy: pd.DataFrame, ms: pd.DataFrame) -> None:
    """
    Radar chart with 5 parameters (PT score excluded — shown separately).
    Normalised so Healthy Reference = 1.0 at the outer ring.
    Values above ref are spasticity-worsening for MS (lower N, lower phi, lower R2n…)
    so we invert impairment-direction params so the radar reads "smaller = worse".
    """
    RADAR_PARAMS = [
        ("R2n",          "R2n",           0.91, True),   # True = higher is better
        ("N",            "N",             8.0,  True),
        ("phi_max",      "phi_max_ratio", 0.65, True),
        ("omega_max",    "omega_max_n",   4.5,  True),
        ("A0 (deg)",     "A0_deg",        None, None),   # informational only
    ]
    # Drop A0 from radar (no clinical reference direction)
    RADAR_PARAMS = [p for p in RADAR_PARAMS if p[2] is not None]
    N = len(RADAR_PARAMS)
    cats = [p[0] for p in RADAR_PARAMS]
    refs = [p[2] for p in RADAR_PARAMS]

    def _means(df):
        return [float(df[p[1]].dropna().mean()) for p in RADAR_PARAMS]

    h_means  = _means(healthy)
    ms_means = _means(ms)
    # Normalise by reference (1.0 = reference level)
    h_norm   = [v / r if r else 1.0 for v, r in zip(h_means,  refs)]
    ms_norm  = [v / r if r else 1.0 for v, r in zip(ms_means, refs)]
    ref_norm = [1.0] * N

    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    angles += angles[:1]
    for vals in (h_norm, ms_norm, ref_norm):
        vals.append(vals[0])

    fig, ax = plt.subplots(figsize=(7, 7), facecolor=_BG,
                           subplot_kw=dict(polar=True))
    ax.set_facecolor(_PANEL)

    ax.plot(angles, ref_norm,  color="#FFD040", lw=1.5, ls="--",  label="Healthy reference")
    ax.fill(angles, ref_norm,  color="#FFD040", alpha=0.07)
    ax.plot(angles, h_norm,    color=_G_COL,    lw=2.5, label=f"Healthy (n={len(healthy)})")
    ax.fill(angles, h_norm,    color=_G_COL,    alpha=0.20)
    ax.plot(angles, ms_norm,   color=_MS_COL,   lw=2.5, label=f"MS (n={len(ms)})")
    ax.fill(angles, ms_norm,   color=_MS_COL,   alpha=0.20)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(cats, color="#C0D0F0", fontsize=12)
    ax.tick_params(colors="#8090B0", labelsize=9)
    ax.set_ylim(0, max(max(h_norm), max(ms_norm), 1.4) * 1.05)
    ax.yaxis.set_tick_params(labelcolor="#8090B0", labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor("#3A4A6A")

    ax.legend(loc="upper right", bbox_to_anchor=(1.38, 1.12),
              facecolor=_PANEL, edgecolor="#3A4A6A", labelcolor="#C0D0F0", fontsize=10)

    fig.text(0.5, 0.97, "Parameter Radar  (normalised to healthy reference = 1.0)",
             ha="center", va="top", color="#D0DEFF", fontsize=13, fontweight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.95])
    out = os.path.join(OUT_DIR, "ms_vs_healthy_radar.png")
    plt.savefig(out, dpi=130, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()
    print(f"  -> {os.path.basename(out)}")


def _plot_pt_scores(healthy: pd.DataFrame, ms: pd.DataFrame) -> None:
    """Strip chart of PT scores per trial, coloured by MAS grade."""
    all_df = pd.concat([
        healthy.assign(group="Healthy"),
        ms.assign(group="MS"),
    ], ignore_index=True)

    fig, ax = plt.subplots(figsize=(9, 5), facecolor=_BG)
    ax.set_facecolor(_PANEL)

    groups    = ["Healthy", "MS"]
    x_ticks   = [0, 1]
    x_jitter  = {g: xi for g, xi in zip(groups, x_ticks)}

    for _, row in all_df.iterrows():
        g     = row["group"]
        xi    = x_jitter[g]
        pt    = row["pt_score_4param"]
        mas   = str(row.get("est_MAS_4param", "?"))
        color = _MAS_COLOR.get(mas, "#888888")
        jit   = np.random.uniform(-0.15, 0.15)
        ax.scatter(xi + jit, pt, color=color, s=80, zorder=4,
                   edgecolors="#000000", linewidths=0.5)
        pid_lbl = str(row.get("pid", ""))
        ax.text(xi + jit + 0.02, pt, f"P{pid_lbl[:6]}",
                color="#99AABB", fontsize=6.5, va="center")

    # MAS threshold lines
    thresholds = [(0.12, "0/1"), (0.28, "1/1+"), (0.44, "1+/2"),
                  (0.60, "2/3"), (0.78, "3/4")]
    for yv, lbl in thresholds:
        ax.axhline(yv, color="#FFFFFF", lw=0.8, ls=":", alpha=0.35)
        ax.text(1.52, yv, f"MAS {lbl}", color="#888899", fontsize=8, va="center")

    ax.set_xticks(x_ticks)
    ax.set_xticklabels([f"Healthy (n={len(healthy)})", f"MS (n={len(ms)})"],
                       color="#C0D0F0", fontsize=12)
    ax.set_xlim(-0.5, 2.0)
    ax.set_ylabel("4-param PT Score", color="#90A8CC", fontsize=11)
    ax.tick_params(axis="y", colors="#A0B0C8", labelsize=10)
    for sp in ax.spines.values():
        sp.set_edgecolor("#3A4A6A")

    # Legend for MAS colours
    mas_handles = [
        mpatches.Patch(facecolor=c, label=f"MAS {m}")
        for m, c in _MAS_COLOR.items() if m != "?"
    ]
    ax.legend(handles=mas_handles, title="MAS grade", title_fontsize=9,
              loc="center right", facecolor=_PANEL,
              edgecolor="#3A4A6A", labelcolor="#C0D0F0", fontsize=9)

    fig.text(0.5, 0.99, "PT Score per Trial  —  MS vs Healthy (4-param)",
             ha="center", va="top", color="#D0DEFF", fontsize=13, fontweight="bold")
    plt.tight_layout(rect=[0, 0, 0.85, 0.97])
    out = os.path.join(OUT_DIR, "ms_vs_healthy_ptscores.png")
    plt.savefig(out, dpi=130, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()
    print(f"  -> {os.path.basename(out)}")


if __name__ == "__main__":
    main()
