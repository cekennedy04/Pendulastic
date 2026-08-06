"""
plot_extension_hypertonia_comparison.py
=======================================
Publication figure: Panel A (normal T1) vs Panel B (extension catch T3).
Wired to real P5 left-post OptiTrack data.

Run:  .venv\Scripts\python.exe plot_extension_hypertonia_comparison.py
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import savgol_filter, find_peaks

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
OPTI_ROOT = os.path.join(BASE_DIR, "OptiTrack_Recordings")
OUT_DIR   = os.path.join(BASE_DIR, "figures")
os.makedirs(OUT_DIR, exist_ok=True)
sys.path.insert(0, BASE_DIR)

from pendulastic_pt_score import load_optitrack


# ─── Style ────────────────────────────────────────────────────────────────────

def _despine(ax, offset=5):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_position(("outward", offset))
    ax.spines["bottom"].set_position(("outward", offset))


def _pub_style():
    plt.rcParams.update({
        "axes.spines.top":   False,
        "axes.spines.right": False,
        "font.family":       "sans-serif",
        "font.sans-serif":   ["Arial", "Helvetica", "Liberation Sans"],
        "axes.titlesize":    11,
        "axes.labelsize":    10,
        "xtick.labelsize":   8,
        "ytick.labelsize":   8,
        "legend.fontsize":   8,
        "figure.titlesize":  12,
        "pdf.fonttype":      42,
        "ps.fonttype":       42,
    })


# ─── Data loading ─────────────────────────────────────────────────────────────

def _load(folder: str, trial: int):
    """Load OptiTrack CSV, SG-smooth, return (t, smooth, vel, df)."""
    path = os.path.join(OPTI_ROOT, folder, "Position_1",
                        "Height_Joint-Level", f"trial_{trial}_optitrack.csv")
    t_raw, angle_raw = load_optitrack(path)
    fs     = 1.0 / float(np.median(np.diff(t_raw)))
    sg_w   = max(5, int(fs * 0.15) // 2 * 2 + 1)
    smooth = savgol_filter(angle_raw, sg_w, polyorder=2)
    vel    = savgol_filter(angle_raw, sg_w, polyorder=2, deriv=1) * fs
    df     = pd.DataFrame({"time": t_raw, "angle": smooth})
    return t_raw, smooth, vel, df, fs


def _anchors(smooth: np.ndarray, vel: np.ndarray, fs: float) -> dict:
    """Extract phi_inf, release_idx, active_end_idx from smoothed signal."""
    # Release: first frame where velocity crosses −10 deg/s
    neg     = np.where(vel < -10)[0]
    rel_idx = int(neg[0]) if len(neg) else 0

    # phi_inf: tail-median of last 2 s
    tail    = max(4, int(fs * 2.0))
    phi_inf = float(np.median(smooth[-tail:]))

    # Active-end: last velocity reversal (zero-crossing) after release,
    # with 500 ms padding.  Fall back to 5 s after release.
    post_vel = vel[rel_idx:]
    signs    = np.sign(post_vel)
    xings    = np.where(np.diff(signs) != 0)[0]          # indices into post_vel
    if len(xings) >= 2:
        last_xing = int(xings[-2])                        # penultimate (avoid settling noise)
        active_end_idx = min(len(smooth) - 1,
                             rel_idx + last_xing + int(fs * 0.5))
    else:
        active_end_idx = min(len(smooth) - 1, rel_idx + int(fs * 5.0))

    return {
        "phi_inf":        phi_inf,
        "release_idx":    rel_idx,
        "active_end_idx": active_end_idx,
    }


# ─── Figure ───────────────────────────────────────────────────────────────────

def plot_publication_hypertonia_comparison(
    normal_df, spastic_df, normal_m, spastic_m, save_path=None
):
    """2-panel publication figure: normal vs extension-catch trial."""
    _pub_style()
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 3.5), sharey=True, dpi=300)
    fig.patch.set_facecolor("white")

    color_line     = "#2b2b2b"
    color_inf      = "#7f8c8d"
    color_flex     = "#3498db"
    color_ext      = "#e67e22"
    color_catch    = "#c0392b"

    panel_data = [
        (axes[0], normal_df,  normal_m,
         "Panel A: Symmetrical Decay\n(Normal trial — T1)"),
        (axes[1], spastic_df, spastic_m,
         "Panel B: Extension-Dominant Catch\n(Hypertonia trial — T3)"),
    ]

    for ax, df, m, title in panel_data:
        t       = df["time"].values
        phi     = df["angle"].values
        phi_inf = m["phi_inf"]
        rel_idx = m["release_idx"]
        end_idx = m["active_end_idx"]
        fs_local = 1.0 / float(np.median(np.diff(t)))

        t0     = t - t[rel_idx]
        t_clip = 6.0

        # ── Trajectory
        ax.plot(t0, phi, color=color_line, lw=1.5, zorder=4,
                label=r"Knee angle ($\phi$)")

        # ── Neutral line
        ax.axhline(phi_inf, color=color_inf, ls="--", lw=1.0, zorder=2,
                   label=rf"Neutral ($\phi_\infty = {phi_inf:.1f}°$)")

        # ── Release / active-end bounds (only draw if inside visible clip frame)
        XMIN, XMAX = -0.5, 6.0
        if XMIN <= t0[rel_idx] <= XMAX:
            ax.axvline(t0[rel_idx], color="gray", ls=":", lw=0.8, alpha=0.6, zorder=1)
        if XMIN <= t0[end_idx] <= XMAX:
            ax.axvline(t0[end_idx], color="gray", ls=":", lw=0.8, alpha=0.6, zorder=1)
        else:
            ax.text(XMAX - 0.1, phi_inf + 3, r"Active window $\rightarrow$",
                    fontsize=6, color="gray", ha="right", va="bottom", alpha=0.7)

        # ── Flex/ext shading inside active window
        t_act   = t0[rel_idx:end_idx]
        phi_act = phi[rel_idx:end_idx]
        ax.fill_between(t_act, phi_act, phi_inf,
                        where=(phi_act < phi_inf),
                        color=color_flex, alpha=0.25, zorder=3,
                        label=r"Flexion area ($P_-$)")
        ax.fill_between(t_act, phi_act, phi_inf,
                        where=(phi_act >= phi_inf),
                        color=color_ext, alpha=0.25, zorder=3,
                        label=r"Extension area ($P_+$)")

        # ── First trough annotation
        win_len  = int(fs_local * 1.5)
        chunk    = phi[rel_idx: rel_idx + win_len]
        t_chunk  = t0[rel_idx: rel_idx + win_len]
        min_i    = int(np.argmin(chunk))
        t_trough = float(t_chunk[min_i])
        phi_trough = float(chunk[min_i])

        if phi_trough >= phi_inf:
            # Pathognomonic: leg never crossed into flexion
            ax.scatter(t_trough, phi_trough,
                       color=color_catch, s=30, zorder=5,
                       edgecolor="black", linewidth=0.5)
            ax.annotate(
                r"Aborted downswing" + "\n" + r"$A_{1\_flex} < 0$",
                xy=(t_trough, phi_trough),
                xytext=(t_trough + 0.65, phi_trough - 14),
                arrowprops=dict(arrowstyle="->", color=color_catch,
                                lw=0.9, connectionstyle="arc3,rad=-0.12"),
                fontsize=7, color=color_catch, fontweight="bold",
                bbox=dict(boxstyle="square,pad=0.25", fc="white",
                          ec=color_catch, alpha=0.92, lw=0.6),
                zorder=6,
            )
        else:
            ax.scatter(t_trough, phi_trough,
                       color=color_flex, s=18, zorder=5)

        # ── phi_0 tick
        phi_0 = float(np.mean(phi[max(0, rel_idx - 5): rel_idx + 1]))
        ax.annotate(
            rf"$\phi_0={phi_0:.1f}°$",
            xy=(t0[rel_idx], phi_0),
            xytext=(t0[rel_idx] + 0.25, phi_0 + 1.5),
            fontsize=7, color="#475569",
        )

        # ── Metrics callout — top-right corner (always clear above the trajectory)
        a1_sign = "+" if m["A1_flex"] >= 0 else ""
        stats_text = (
            f"$\\bf{{Active\\ Window\\ Metrics}}$\n"
            f"Area ratio:  {m['area_ratio']:.4f}\n"
            f"PT score:    {m['pt_score']:.3f}\n"
            f"$A_{{1,flex}}$:    {a1_sign}{m['A1_flex']:.2f}°"
        )
        ax.text(0.97, 0.97, stats_text, transform=ax.transAxes,
                fontsize=7.5, color="#2b2b2b", linespacing=1.5,
                ha="right", va="top",
                bbox=dict(boxstyle="square,pad=0.4", fc="#fcfcfc",
                          ec="#dcdcdc", lw=0.5, alpha=0.92))

        ax.set_title(title, pad=8)
        ax.set_xlabel("Time from release (s)")
        ax.set_xlim(-0.5, t_clip)
        ax.grid(False)
        _despine(ax)

    axes[0].set_ylabel("Knee extension angle (°)")
    axes[0].set_ylim(80, 195)

    # Single shared legend on Panel A
    handles, labels = axes[0].get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    axes[0].legend(by_label.values(), by_label.keys(),
                   loc="lower left", frameon=True,
                   facecolor="white", edgecolor="none", fontsize=7.5)

    fig.suptitle(
        "Wartenberg Pendulum Test — P5 Left Leg, Post-MINT\n"
        r"Pathognomonic sign of extension spasticity: $A_{1\_flex} < 0$",
        fontsize=10, fontweight="bold", y=1.01,
    )
    plt.tight_layout()

    if save_path:
        fig.savefig(save_path, bbox_inches="tight", dpi=300)
        print(f"-> {save_path}")
    plt.close(fig)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    folder = "Participant_5_left_post"

    print("Loading T1 (normal)…")
    t1_t, t1_sm, t1_vel, t1_df, t1_fs = _load(folder, 1)
    t1_m = _anchors(t1_sm, t1_vel, t1_fs)
    t1_m.update({"trial_name": "T1", "area_ratio": 0.0665,
                 "pt_score": 0.1288, "A1_flex": 25.98})
    print(f"  phi_inf={t1_m['phi_inf']:.2f}  rel={t1_m['release_idx']}  "
          f"end={t1_m['active_end_idx']}")

    print("Loading T3 (hypertonia catch)…")
    t3_t, t3_sm, t3_vel, t3_df, t3_fs = _load(folder, 3)
    t3_m = _anchors(t3_sm, t3_vel, t3_fs)
    t3_m.update({"trial_name": "T3", "area_ratio": 0.5443,
                 "pt_score": 0.8599, "A1_flex": -0.95})
    print(f"  phi_inf={t3_m['phi_inf']:.2f}  rel={t3_m['release_idx']}  "
          f"end={t3_m['active_end_idx']}")

    out = os.path.join(OUT_DIR, "Fig_P5_extension_hypertonia.png")
    plot_publication_hypertonia_comparison(t1_df, t3_df, t1_m, t3_m, save_path=out)
    print("Done.")


if __name__ == "__main__":
    main()
