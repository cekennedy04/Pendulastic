"""
generate_paper_figures.py
============================
Three figures for the Results/Data-Analysis draft, matching conventions
found in the literature search (De Santis & Perez 2024 JNER; Whelan et al.
2018 JNER; Willaert et al. 2020 pendulum-test kinematics), using the
dataviz skill's validated colorblind-safe categorical palette (blue
#2a78d6 = reference/OptiTrack, orange #eb6834 = test modality):

  Figure 1 (fig1_bland_altman.png): Bland-Altman, IMU vs OptiTrack and
    MediaPipe vs OptiTrack, on the relaxation index (R2n) -- the headline
    clinical metric, matching Perez's own use of relaxation index.
  Figure 2 (fig2_metrics_by_mas.png): grouped bar chart, mean +/- SD of
    PT metrics by MAS category -- direct match to Whelan et al. 2018
    Figure 3's convention (their paper is in the same research lineage
    as the Perez lab's pendulum-test work).
  Figure 3 (fig3_trajectory_example.png): example single-trial knee-angle
    trajectory, IMU vs OptiTrack overlaid -- matches Willaert et al. 2020
    Figure 1b's convention.

Usage:
    .venv\\Scripts\\python.exe generate_paper_figures.py
"""
from __future__ import annotations

import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import batch_imu_vs_optitrack_rmse as batch
import workbench_engine as engine
from generate_paper_results_analysis import load_mas_scores

BLUE = "#2a78d6"    # reference / OptiTrack
ORANGE = "#eb6834"  # test modality (IMU / MediaPipe)
INK = "#0b0b0b"
MUTED = "#898781"
GRID = "#e1e0d9"

OUT_DIR = "Model_Analysis_Outputs/paper_figures"
os.makedirs(OUT_DIR, exist_ok=True)


def _style_axis(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(MUTED)
    ax.spines["bottom"].set_color(MUTED)
    ax.tick_params(colors=INK, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)


def load_trials_csv():
    with open("Model_Analysis_Outputs/paper_results_analysis_trials.csv", newline="") as f:
        return list(csv.DictReader(f))


def figure1_bland_altman(rows):
    """Bland-Altman for R2n: IMU vs OptiTrack. (MediaPipe PT-param values
    are not in the saved per-trial CSV -- IMU panel only, single-panel
    figure; a second panel can be added once MediaPipe PT params are
    merged into the same table.)"""
    opti = np.array([float(r["opti_R2n"]) for r in rows])
    imu = np.array([float(r["imu_R2n"]) for r in rows])
    mean = (opti + imu) / 2
    diff = imu - opti
    bias = float(np.mean(diff))
    sd = float(np.std(diff, ddof=1))
    loa_lo, loa_hi = bias - 1.96 * sd, bias + 1.96 * sd

    fig, ax = plt.subplots(figsize=(5, 4.2), dpi=200, facecolor="white")
    ax.scatter(mean, diff, s=26, facecolor=ORANGE, edgecolor="white",
              linewidth=0.6, alpha=0.85, zorder=3)
    ax.axhline(bias, color=BLUE, linewidth=1.6, zorder=2)
    ax.axhline(loa_lo, color=MUTED, linewidth=1.2, linestyle="--", zorder=2)
    ax.axhline(loa_hi, color=MUTED, linewidth=1.2, linestyle="--", zorder=2)
    ax.text(ax.get_xlim()[1] if ax.get_xlim()[1] else 1, bias, f" bias={bias:.2f}",
            color=BLUE, fontsize=8, va="bottom")
    ax.text(0.98, 0.05, f"n={len(rows)}", transform=ax.transAxes, fontsize=8,
            color=MUTED, ha="right")
    ax.set_xlabel("Mean of IMU and OptiTrack R2n", fontsize=9, color=INK)
    ax.set_ylabel("IMU − OptiTrack R2n", fontsize=9, color=INK)
    ax.set_title("Figure 1. Bland-Altman: IMU vs. OptiTrack\n(relaxation index, R2n)",
                 fontsize=10, color=INK)
    _style_axis(ax)
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "fig1_bland_altman.png")
    fig.savefig(out, facecolor="white")
    plt.close(fig)
    print(f"Saved {out}  (bias={bias:.3f}, LoA=[{loa_lo:.3f}, {loa_hi:.3f}])")


def figure2_metrics_by_mas(rows):
    mas = load_mas_scores()
    param_labels = {"R2n": "Relaxation index (R2n)", "N": "Oscillation count (N)",
                    "area_ratio": "Area ratio"}
    # Bin MAS into 0 / 1(+) / >=2, matching Whelan et al.'s grouping convention
    def mas_bin(g):
        if g == 0:
            return "0 (none)"
        if g < 2:
            return "1–1+ (mild)"
        return "≥2 (moderate+)"
    bin_order = ["0 (none)", "1–1+ (mild)", "≥2 (moderate+)"]

    binned = {b: {p: [] for p in param_labels} for b in bin_order}
    for r in rows:
        g = mas.get(r["pid"])
        if g is None:
            continue
        b = mas_bin(g)
        for p in param_labels:
            binned[b][p].append(float(r[f"imu_{p}"]))

    fig, axes = plt.subplots(1, len(param_labels), figsize=(4.2 * len(param_labels), 4), dpi=200,
                             facecolor="white")
    for ax, (p, label) in zip(axes, param_labels.items()):
        means = [np.mean(binned[b][p]) if binned[b][p] else np.nan for b in bin_order]
        sds = [np.std(binned[b][p], ddof=1) if len(binned[b][p]) > 1 else 0 for b in bin_order]
        ns = [len(binned[b][p]) for b in bin_order]
        x = np.arange(len(bin_order))
        ax.bar(x, means, yerr=sds, capsize=4, color=BLUE, edgecolor="white",
               linewidth=0.6, width=0.6, zorder=3,
               error_kw={"ecolor": INK, "elinewidth": 1.2, "capthick": 1.2})
        ax.set_xticks(x)
        tick_labels = [f"{b}\n(n={n})" for b, n in zip(bin_order, ns)]
        ax.set_xticklabels(tick_labels, fontsize=8, color=INK)
        ax.set_title(label, fontsize=9, color=INK)
        ax.set_ylabel("IMU-derived value (mean ± SD)", fontsize=8, color=INK)
        _style_axis(ax)
        y_top = ax.get_ylim()[1]
        for xi, n in zip(x, ns):
            if n == 0:
                ax.text(xi, y_top * 0.5, "no data\nin current\ncohort", ha="center",
                        va="center", fontsize=7, color=MUTED, style="italic")
    fig.suptitle("Figure 2. IMU-derived pendulum-test metrics by MAS category",
                fontsize=11, color=INK, y=1.03)
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "fig2_metrics_by_mas.png")
    fig.savefig(out, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")


def figure3_trajectory_example():
    trials = batch.discover_trials()
    target = next((t for t in trials if "Participant_16" in t["imu"]
                   and "Trial_1_imu.csv" in t["imu"] and t["optitrack_path"]), None)
    if target is None:
        print("No suitable trial found for Figure 3 -- skipping.")
        return
    validations = {
        "accel": engine.validate_component_csv(target["accel"], "accel"),
        "gyro": engine.validate_component_csv(target["gyro"], "gyro"),
        "mag": engine.validate_component_csv(target["mag"], "mag"),
        "imu": engine.validate_component_csv(target["imu"], "imu"),
    }
    if any(not v["ok"] for v in validations.values()):
        print("Target trial's components invalid -- skipping Figure 3.")
        return
    t_imu, ang_imu, _ref = engine.load_imu_trial_from_components(validations, method="relative")
    t_opti, ang_opti, _m = engine.load_optitrack_trial(target["optitrack_path"])

    fig, ax = plt.subplots(figsize=(6, 4), dpi=200, facecolor="white")
    ax.plot(t_opti, ang_opti, color=BLUE, linewidth=1.8, label="OptiTrack (reference)", zorder=3)
    ax.plot(t_imu, ang_imu, color=ORANGE, linewidth=1.6, label="IMU", zorder=2, alpha=0.9)
    ax.set_xlabel("Time (s)", fontsize=9, color=INK)
    ax.set_ylabel("Knee angle (deg)", fontsize=9, color=INK)
    ax.set_title(f"Figure 3. Example trial trajectory\n({target['participant']}, {target['trial_key']})",
                fontsize=10, color=INK)
    ax.legend(frameon=False, fontsize=8, loc="best")
    _style_axis(ax)
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "fig3_trajectory_example.png")
    fig.savefig(out, facecolor="white")
    plt.close(fig)
    print(f"Saved {out}")


def main():
    rows = load_trials_csv()
    figure1_bland_altman(rows)
    figure2_metrics_by_mas(rows)
    figure3_trajectory_example()


if __name__ == "__main__":
    main()
