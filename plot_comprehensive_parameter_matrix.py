"""
plot_comprehensive_parameter_matrix.py
=======================================
4x2 publication grid: Left vs Right, Pre vs Post, across 7 parameters for P5.

Run:  .venv\Scripts\python.exe plot_comprehensive_parameter_matrix.py
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import savgol_filter

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
OPTI_ROOT = os.path.join(BASE_DIR, "OptiTrack_Recordings")
OUT_DIR   = os.path.join(BASE_DIR, "figures")
os.makedirs(OUT_DIR, exist_ok=True)
sys.path.insert(0, BASE_DIR)

from pendulastic_pt_score import load_optitrack, compute_pt_params, compute_pt_score

# Pre-session folders use "_side" naming convention
GROUPS = {
    ("Left",  "Pre"):  "Participant_5_left_side",
    ("Left",  "Post"): "Participant_5_left_post",
    ("Right", "Pre"):  "Participant_5_right_side",
    ("Right", "Post"): "Participant_5_right_post",
}
TRIALS = [1, 2, 3, 4, 5]


# ─── Data extraction ─────────────────────────────────────────────────────────

def _extract_trial(folder: str, trial: int) -> dict | None:
    path = os.path.join(OPTI_ROOT, folder, "Position_1",
                        "Height_Joint-Level", f"trial_{trial}_optitrack.csv")
    if not os.path.isfile(path):
        return None
    t, angle = load_optitrack(path)
    fs   = 1.0 / float(np.median(np.diff(t)))
    sg_w = max(5, int(fs * 0.15) // 2 * 2 + 1)
    smooth = savgol_filter(angle, sg_w, polyorder=2)
    vel    = savgol_filter(angle, sg_w, polyorder=2, deriv=1) * fs

    neg     = np.where(vel < -10)[0]
    rel_idx = int(neg[0]) if len(neg) else 0
    phi_0   = float(np.mean(smooth[max(0, rel_idx - 5): rel_idx + 1]))
    tail    = max(4, int(fs * 2.0))
    phi_inf = float(np.median(smooth[-tail:]))

    params = compute_pt_params(t, smooth)
    if params is None:
        return None
    pt6 = compute_pt_score(params)

    return {
        "phi_0":         phi_0,
        "phi_inf":       phi_inf,
        "phi_max_ratio": params.get("phi_max_ratio", float("nan")),
        "omega_max_n":   params.get("omega_max_n",   float("nan")),
        "area_ratio":    params.get("area_ratio",     float("nan")),
        "pt_score":      pt6,
        "N":             params.get("N",              float("nan")),
    }


def build_dataframe() -> pd.DataFrame:
    rows = []
    for (leg, session), folder in GROUPS.items():
        for trial in TRIALS:
            rec = _extract_trial(folder, trial)
            if rec is None:
                continue
            rec["leg"]     = leg
            rec["session"] = session
            rec["trial"]   = trial
            rows.append(rec)
    return pd.DataFrame(rows)


# ─── Style helpers ────────────────────────────────────────────────────────────

def _pub_style():
    plt.rcParams.update({
        "font.family":       "sans-serif",
        "font.sans-serif":   ["Arial", "Helvetica", "Liberation Sans"],
        "axes.titlesize":    9.5,
        "axes.labelsize":    8.5,
        "xtick.labelsize":   8,
        "ytick.labelsize":   8,
        "legend.fontsize":   8,
        "figure.titlesize":  12,
        "pdf.fonttype":      42,
        "ps.fonttype":       42,
        "axes.edgecolor":    "#cccccc",
        "axes.linewidth":    0.8,
    })


def _despine(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#cccccc")
    ax.spines["bottom"].set_color("#cccccc")


# ─── Main figure ─────────────────────────────────────────────────────────────

PARAMETERS = [
    ("phi_0",         r"Initial release angle $\phi_0$ (°)",           None),
    ("phi_inf",       r"Resting neutral $\phi_\infty$ (°)",             None),
    ("phi_max_ratio", r"First-swing ratio $\phi_{max,ratio}$",         0.787),
    ("omega_max_n",   r"Norm. peak velocity $\omega_{max,n}$",         5.692),
    ("area_ratio",    r"Extension area ratio",                          0.09),
    ("pt_score",      r"PT score (6-param)",                           0.09),
    ("N",             r"Oscillation cycles $N$",                        7.0),
]

COLOR_PRE  = "#7f8c8d"   # slate gray
COLOR_POST = "#c0392b"   # deep crimson

BOX_W    = 0.22
POS_LEFT  = 1.0
POS_RIGHT = 2.0
OFFSETS   = [-BOX_W * 0.75, BOX_W * 0.75]  # [pre, post] within each leg group


def plot_all_parameters_matrix(df: pd.DataFrame, save_path: str | None = None):
    _pub_style()
    fig, axes = plt.subplots(4, 2, figsize=(7.2, 9.5), dpi=300)
    fig.patch.set_facecolor("white")
    axes_flat = axes.flatten()

    leg_positions = {"Left": POS_LEFT, "Right": POS_RIGHT}

    for i, (col, title, ref) in enumerate(PARAMETERS):
        ax = axes_flat[i]

        for leg, lp in leg_positions.items():
            for si, session in enumerate(["Pre", "Post"]):
                vals = (df[(df["leg"] == leg) & (df["session"] == session)][col]
                        .dropna().values)
                if len(vals) == 0:
                    continue
                pos  = lp + OFFSETS[si]
                col_c = COLOR_PRE if session == "Pre" else COLOR_POST
                med_c = "#2b2b2b" if session == "Pre" else "white"

                ax.boxplot(
                    vals,
                    positions=[pos],
                    widths=BOX_W,
                    patch_artist=True,
                    showfliers=False,
                    boxprops=dict(facecolor=col_c, color="#2b2b2b",
                                  alpha=0.78, linewidth=0.7),
                    whiskerprops=dict(color="#555555", linewidth=0.7),
                    capprops=dict(color="#555555", linewidth=0.7),
                    medianprops=dict(color=med_c, linewidth=1.1),
                )
                jitter = np.random.uniform(-0.035, 0.035, size=len(vals))
                ax.scatter(
                    np.full(len(vals), pos) + jitter, vals,
                    color="#2b2b2b", s=7, alpha=0.45, zorder=5,
                    edgecolors="none",
                )

        # Healthy reference line
        if ref is not None:
            ax.axhline(ref, color="#16A34A", lw=1.0, ls="--",
                       alpha=0.7, zorder=2, label="Healthy ref")
            ax.text(2.42, ref, "ref", fontsize=6.5, color="#16A34A",
                    va="center", ha="left")

        ax.set_title(title, pad=5, weight="bold")
        ax.set_xticks([POS_LEFT, POS_RIGHT])
        ax.set_xticklabels(["Left", "Right"])
        ax.set_xlim(0.55, 2.45)
        ax.grid(axis="y", ls=":", lw=0.5, color="#e0e0e0", alpha=0.7)
        ax.set_axisbelow(True)
        _despine(ax)

    # ── 8th slot: legend ──────────────────────────────────────────────────────
    ax_leg = axes_flat[7]
    ax_leg.axis("off")

    import matplotlib.patches as mpatches
    import matplotlib.lines as mlines

    patches = [
        mpatches.Patch(facecolor=COLOR_PRE,  edgecolor="#2b2b2b",
                       alpha=0.78, label="Pre-MINT session"),
        mpatches.Patch(facecolor=COLOR_POST, edgecolor="#2b2b2b",
                       alpha=0.78, label="Post-MINT session"),
        mlines.Line2D([], [], color="#16A34A", ls="--",
                      lw=1.2, label="Healthy reference"),
    ]
    ax_leg.legend(
        handles=patches,
        loc="center",
        frameon=True,
        facecolor="#fcfcfc",
        edgecolor="#dcdcdc",
        title="Assessment timeline",
        title_fontproperties={"weight": "bold", "size": 8.5},
        borderpad=1.0,
        labelspacing=0.9,
        fontsize=8.5,
    )

    fig.suptitle(
        "P5 — Wartenberg Pendulum Test: All Parameters\n"
        "Left vs Right  ·  Pre vs Post MINT Training",
        fontsize=11, fontweight="bold", y=1.005,
    )
    plt.tight_layout(h_pad=1.6, w_pad=1.2)

    if save_path:
        fig.savefig(save_path, bbox_inches="tight", dpi=300)
        print(f"-> {save_path}")
    plt.close(fig)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("Extracting parameters from all 4 groups…")
    df = build_dataframe()
    print(df.groupby(["leg", "session"])["pt_score"].describe().round(3))

    out = os.path.join(OUT_DIR, "Fig_P5_parameter_matrix.png")
    plot_all_parameters_matrix(df, save_path=out)
    print("Done.")


if __name__ == "__main__":
    main()
