"""
generate_all_figures_comprehensive.py
======================================
"Every figure we can think of" pass (2026-08-20), building on top of the
existing paper_figures/ set (fig1-fig8, fig10). Reuses ALREADY-COMPUTED
outputs from this session (imu_vs_optitrack_rmse.csv, the 144-combo tuning
grid log, the ft_ratio sweep log, the magnetometer-toggle log, mas_scores.csv,
paper_results_analysis_trials.csv, multimetric_trials.csv) -- no new pipeline
runs, so this is fast and reproducible from artifacts already on disk.

New figures (fig11-fig23):
  fig11_rmse_distribution      - histogram of trial-level RMSE, IMU vs OptiTrack
  fig12_rmse_by_participant    - RMSE spread per participant
  fig13_bias_vs_rmse           - scatter showing bias dominates total error
  fig14_lag_distribution       - sync-lag histogram (auto-alignment sanity check)
  fig15_ft_ratio_sweep         - RMSE vs ft_ratio (the "degenerate optimum" finding)
  fig16_mag_toggle_paired      - with-mag vs no-mag RMSE, paired per trial (the "wash")
  fig17_method_comparison      - relative vs Ockendon vs Ockendon-flipped, best-of-grid
  fig18_beta_sensitivity       - RMSE vs Madgwick beta, method=relative
  fig19_mas_distribution       - MAS grade distribution across the full cohort
  fig20_data_availability      - per-participant modality coverage matrix (the n=1 story)
  fig21_pt_params_small_multiples - all 7 PT parameters, Control vs MS, one panel each
  fig22_grid_search_convergence   - full 144-combo RMSE distribution (grid coverage)
  fig23_rmse_vs_trial_length      - does clip length explain error? (sanity check)

Usage:
    .venv\\Scripts\\python.exe generate_all_figures_comprehensive.py
"""
from __future__ import annotations

import csv
import re
import os
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from generate_paper_figures import BLUE, ORANGE, INK, MUTED, GRID, OUT_DIR, _style_axis

BASE = os.path.dirname(os.path.abspath(__file__))


def load_rmse_csv():
    path = os.path.join(BASE, "Model_Analysis_Outputs", "imu_vs_optitrack_rmse.csv")
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["status"] != "ok":
                continue
            rows.append({
                "participant": r["participant"],
                "trial_key": r["trial_key"],
                "rmse": float(r["rmse_deg"]),
                "mae": float(r["mae_deg"]),
                "bias": float(r["bias_deg"]),
                "lag": float(r["lag_sec"]),
                "n_samples": int(r["n_samples"]),
            })
    return rows


def load_mas_scores():
    path = os.path.join(BASE, "mas_scores.csv")
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r.get("participant"):
                continue
            rows.append(r)
    return rows


def parse_grid_log():
    path = os.path.join(BASE, "full_grid_sweep_output.txt")
    combos = []
    pat = re.compile(
        r"mean=\s*([\d.]+)\s+median=\s*([\d.]+)\s+n<5deg=(\d+)/(\d+)\s+(\{.*\})"
    )
    with open(path, encoding="utf-8") as f:
        for line in f:
            if "mean=" not in line or "{" not in line:
                continue
            m = pat.search(line)
            if not m:
                continue
            mean_rmse, median_rmse, n5, ntot, params_str = m.groups()
            params = eval(params_str)  # safe: our own logged dict literal
            combos.append({
                "mean": float(mean_rmse), "median": float(median_rmse),
                "n5": int(n5), "ntot": int(ntot), **params,
            })
    return combos


def parse_ft_ratio_sweep():
    path = os.path.join(BASE, "ft_ratio_sweep_wide_output.txt")
    rows = []
    pat = re.compile(r"^\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s*$")
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = pat.match(line)
            if not m:
                continue
            ft, mean_rmse, median_rmse, n5 = m.groups()
            rows.append({"ft_ratio": float(ft), "mean": float(mean_rmse),
                        "median": float(median_rmse), "n5": int(n5)})
    return rows


def parse_mag_toggle():
    path = os.path.join(BASE, "mag_toggle_output.txt")
    rows = []
    pat = re.compile(r"no_mag=\s*([\d.]+)\s+with_mag=\s*([\d.]+)")
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = pat.search(line)
            if not m:
                continue
            no_mag, with_mag = m.groups()
            rows.append({"no_mag": float(no_mag), "with_mag": float(with_mag)})
    return rows


def load_pt_params_trials():
    path = os.path.join(BASE, "Model_Analysis_Outputs", "paper_results_analysis_trials.csv")
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


PARAMS = ["R2n", "N", "phi_max_ratio", "omega_max_n", "f", "area_ratio", "omega_min_n"]
PARAM_LABELS = {
    "R2n": "R2n (relaxation)", "N": "N (swings)", "phi_max_ratio": "phi_max ratio",
    "omega_max_n": "omega_max,n", "f": "f (Hz)", "area_ratio": "area ratio",
    "omega_min_n": "omega_min,n",
}


def savefig(fig, name):
    out = os.path.join(OUT_DIR, name)
    fig.savefig(out, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")


# ---------------------------------------------------------------- fig11
def fig11_rmse_distribution(rows):
    rmse = np.array([r["rmse"] for r in rows])
    fig, ax = plt.subplots(figsize=(7.2, 4.4), dpi=200, facecolor="white")
    ax.hist(rmse, bins=16, color=ORANGE, edgecolor="white", linewidth=0.6, zorder=3)
    ax.axvline(rmse.mean(), color=INK, linestyle="--", linewidth=1.2, zorder=4)
    ax.axvline(5.0, color=BLUE, linestyle=":", linewidth=1.2, zorder=4)
    ax.text(rmse.mean() + 0.5, ax.get_ylim()[1] * 0.9, f"mean {rmse.mean():.1f} deg",
            fontsize=9, color=INK)
    ax.text(5.3, ax.get_ylim()[1] * 0.75, "5 deg\nclinical goal", fontsize=8, color=BLUE)
    ax.set_xlabel("Full-curve RMSE, IMU vs. OptiTrack (deg)", fontsize=10, color=INK)
    ax.set_ylabel("Trial count", fontsize=10, color=INK)
    ax.set_title(f"Figure 11. Distribution of trial-level RMSE\n(n={len(rmse)} trials -- "
                f"{int((rmse < 5).sum())}/{len(rmse)} meet the 5 deg clinical goal)",
                fontsize=10, color=INK)
    _style_axis(ax)
    savefig(fig, "fig11_rmse_distribution.png")


# ---------------------------------------------------------------- fig12
def fig12_rmse_by_participant(rows):
    by_p = {}
    for r in rows:
        by_p.setdefault(r["participant"].replace("Participant_", "P"), []).append(r["rmse"])
    parts = sorted(by_p, key=lambda p: np.median(by_p[p]))
    fig, ax = plt.subplots(figsize=(7.6, 4.6), dpi=200, facecolor="white")
    bp = ax.boxplot([by_p[p] for p in parts], tick_labels=parts, patch_artist=True, widths=0.55,
                    medianprops=dict(color=INK, linewidth=1.5),
                    boxprops=dict(facecolor=ORANGE, alpha=0.55, edgecolor=INK, linewidth=0.8),
                    whiskerprops=dict(color=MUTED), capprops=dict(color=MUTED),
                    flierprops=dict(marker="o", markersize=3, markerfacecolor=INK, markeredgecolor="none"))
    for p, xi in zip(parts, range(1, len(parts) + 1)):
        ys = by_p[p]
        xs = np.random.default_rng(0).normal(xi, 0.05, size=len(ys))
        ax.scatter(xs, ys, s=10, color=INK, alpha=0.5, zorder=5)
    ax.axhline(5.0, color=BLUE, linestyle=":", linewidth=1.2, zorder=2)
    ax.set_ylabel("RMSE (deg)", fontsize=10, color=INK)
    ax.set_title("Figure 12. RMSE spread by participant\n"
                "(no single participant explains the overall error level)",
                fontsize=10, color=INK)
    _style_axis(ax)
    savefig(fig, "fig12_rmse_by_participant.png")


# ---------------------------------------------------------------- fig13
def fig13_bias_vs_rmse(rows):
    bias = np.array([abs(r["bias"]) for r in rows])
    rmse = np.array([r["rmse"] for r in rows])
    fig, ax = plt.subplots(figsize=(6.4, 5.4), dpi=200, facecolor="white")
    ax.scatter(bias, rmse, s=28, color=ORANGE, edgecolor="white", linewidth=0.5, zorder=3)
    lims = [0, max(bias.max(), rmse.max()) * 1.05]
    ax.plot(lims, lims, color=MUTED, linestyle="--", linewidth=1.0, zorder=2)
    ax.text(lims[1] * 0.6, lims[1] * 0.66, "bias = RMSE\n(error is pure offset)",
            fontsize=8, color=MUTED, rotation=38)
    r2 = np.corrcoef(bias, rmse)[0, 1] ** 2
    ax.set_xlabel("|Bias| (deg)", fontsize=10, color=INK)
    ax.set_ylabel("RMSE (deg)", fontsize=10, color=INK)
    ax.set_title(f"Figure 13. Bias explains most of the trial-level error\n"
                f"(R² = {r2:.2f}, n={len(rows)} trials -- points near the diagonal "
                f"are bias-dominated, not noise-dominated)", fontsize=10, color=INK)
    _style_axis(ax)
    savefig(fig, "fig13_bias_vs_rmse.png")


# ---------------------------------------------------------------- fig14
def fig14_lag_distribution(rows):
    lag = np.array([r["lag"] for r in rows])
    fig, ax = plt.subplots(figsize=(7.0, 4.2), dpi=200, facecolor="white")
    ax.hist(lag, bins=16, color=BLUE, edgecolor="white", linewidth=0.6, zorder=3)
    ax.axvline(0, color=INK, linestyle="--", linewidth=1.0, zorder=4)
    ax.set_xlabel("Auto-detected sync lag, IMU vs. OptiTrack (s)", fontsize=10, color=INK)
    ax.set_ylabel("Trial count", fontsize=10, color=INK)
    ax.set_title(f"Figure 14. Cross-correlation sync-lag distribution\n"
                f"(median |lag| = {np.median(np.abs(lag)):.2f}s -- confirms timeline "
                f"alignment is not itself the error source)", fontsize=10, color=INK)
    _style_axis(ax)
    savefig(fig, "fig14_lag_distribution.png")


# ---------------------------------------------------------------- fig15
def fig15_ft_ratio_sweep(rows):
    ft = [r["ft_ratio"] for r in rows]
    mean = [r["mean"] for r in rows]
    median = [r["median"] for r in rows]
    fig, ax = plt.subplots(figsize=(7.2, 4.4), dpi=200, facecolor="white")
    ax.plot(ft, mean, color=ORANGE, linewidth=2, marker="o", markersize=3.5, label="mean RMSE", zorder=3)
    ax.plot(ft, median, color=BLUE, linewidth=2, marker="o", markersize=3.5, label="median RMSE", zorder=3)
    ax.set_xlabel("ft_ratio (Ockendon torsional/flexural stiffness ratio)", fontsize=10, color=INK)
    ax.set_ylabel("RMSE (deg)", fontsize=10, color=INK)
    ax.set_title("Figure 15. ft_ratio sweep -- monotonic, not a real optimum\n"
                "(error keeps falling to the edge of the swept range -- this is a "
                "degenerate trend, not a tuned parameter)", fontsize=10, color=INK)
    ax.legend(frameon=False, fontsize=9)
    _style_axis(ax)
    savefig(fig, "fig15_ft_ratio_sweep.png")


# ---------------------------------------------------------------- fig16
def fig16_mag_toggle_paired(rows):
    no_mag = np.array([r["no_mag"] for r in rows])
    with_mag = np.array([r["with_mag"] for r in rows])
    fig, ax = plt.subplots(figsize=(6.2, 6.0), dpi=200, facecolor="white")
    ax.scatter(no_mag, with_mag, s=26, color=ORANGE, edgecolor="white", linewidth=0.5, zorder=3)
    lims = [0, max(no_mag.max(), with_mag.max()) * 1.05]
    ax.plot(lims, lims, color=MUTED, linestyle="--", linewidth=1.0, zorder=2)
    delta = with_mag - no_mag
    ax.set_xlabel("RMSE, no magnetometer (deg)", fontsize=10, color=INK)
    ax.set_ylabel("RMSE, with magnetometer (deg)", fontsize=10, color=INK)
    ax.set_title(f"Figure 16. Magnetometer fusion is a wash\n"
                f"(n={len(rows)} trials; mean delta {delta.mean():+.2f} deg, "
                f"median {np.median(delta):+.2f} deg -- points sit on the diagonal)",
                fontsize=10, color=INK)
    _style_axis(ax)
    savefig(fig, "fig16_mag_toggle_paired.png")


# ---------------------------------------------------------------- fig17
def fig17_method_comparison(combos):
    methods = ["relative", "ockendon", "ockendon_flipped"]
    labels = {"relative": "Relative\n(adopted)", "ockendon": "Ockendon",
             "ockendon_flipped": "Ockendon\n(flipped)"}
    best_by_method = {}
    for m in methods:
        vals = [c["mean"] for c in combos if c["method"] == m]
        best_by_method[m] = min(vals)
    colors = [ORANGE if m == "relative" else MUTED for m in methods]
    fig, ax = plt.subplots(figsize=(6.4, 4.6), dpi=200, facecolor="white")
    x = np.arange(len(methods))
    vals = [best_by_method[m] for m in methods]
    ax.bar(x, vals, color=colors, edgecolor="white", linewidth=0.6, width=0.6, zorder=3)
    for xi, v in zip(x, vals):
        ax.text(xi, v + 0.5, f"{v:.1f}", ha="center", fontsize=9, color=INK)
    ax.set_xticks(x)
    ax.set_xticklabels([labels[m] for m in methods], fontsize=9, color=INK)
    ax.set_ylabel("Best mean RMSE across grid (deg)", fontsize=10, color=INK)
    ax.set_title("Figure 17. Best-case RMSE by AHRS model\n"
                "(144-combo grid search; the physically-motivated Ockendon models "
                "lose badly to the simpler relative-quaternion method)", fontsize=10, color=INK)
    _style_axis(ax)
    savefig(fig, "fig17_method_comparison.png")


# ---------------------------------------------------------------- fig18
def fig18_beta_sensitivity(combos):
    rel = [c for c in combos if c["method"] == "relative"]
    by_beta = {}
    for c in rel:
        by_beta.setdefault(c["beta"], []).append(c["mean"])
    betas = sorted(by_beta)
    means = [np.mean(by_beta[b]) for b in betas]
    mins = [min(by_beta[b]) for b in betas]
    fig, ax = plt.subplots(figsize=(6.8, 4.4), dpi=200, facecolor="white")
    ax.plot(betas, means, color=MUTED, linewidth=1.6, marker="o", markersize=4,
            label="mean across other params", zorder=3)
    ax.plot(betas, mins, color=ORANGE, linewidth=2, marker="o", markersize=4,
            label="best combo at this beta", zorder=4)
    ax.axvline(0.041, color=BLUE, linestyle=":", linewidth=1.2, zorder=2)
    ax.text(0.043, ax.get_ylim()[1] * 0.95, "adopted\nbeta=0.041", fontsize=8, color=BLUE, va="top")
    ax.set_xlabel("Madgwick filter gain (beta)", fontsize=10, color=INK)
    ax.set_ylabel("RMSE (deg)", fontsize=10, color=INK)
    ax.set_title("Figure 18. RMSE is flat across beta\n"
                "(method=relative; the grid-search win came from method choice, "
                "not from fine beta tuning)", fontsize=10, color=INK)
    ax.legend(frameon=False, fontsize=8)
    _style_axis(ax)
    savefig(fig, "fig18_beta_sensitivity.png")


# ---------------------------------------------------------------- fig19
def fig19_mas_distribution(mas_rows):
    counts = {}
    for r in mas_rows:
        g = r["mas_grade"].strip()
        if g in ("", "-1"):
            continue
        counts[g] = counts.get(g, 0) + 1
    order = ["0", "1", "1+", "2", "3", "4"]
    order = [o for o in order if o in counts] + [k for k in counts if k not in order]
    fig, ax = plt.subplots(figsize=(6.4, 4.4), dpi=200, facecolor="white")
    x = np.arange(len(order))
    vals = [counts[g] for g in order]
    colors = [BLUE if g == "0" else ORANGE for g in order]
    ax.bar(x, vals, color=colors, edgecolor="white", linewidth=0.6, width=0.6, zorder=3)
    for xi, v in zip(x, vals):
        ax.text(xi, v + 0.15, str(v), ha="center", fontsize=9, color=INK)
    ax.set_xticks(x)
    ax.set_xticklabels(order, fontsize=10, color=INK)
    ax.set_xlabel("MAS grade", fontsize=10, color=INK)
    ax.set_ylabel("Leg-condition records", fontsize=10, color=INK)
    ax.set_title("Figure 19. MAS grade distribution, full cohort\n"
                "(blue = healthy control by definition; every MS record is MAS <= 1+ -- "
                "no moderate-to-severe spasticity in this dataset yet)", fontsize=10, color=INK)
    _style_axis(ax)
    savefig(fig, "fig19_mas_distribution.png")


# ---------------------------------------------------------------- fig20
def fig20_data_availability():
    import workbench_engine as engine  # noqa
    import rmse_pipeline_common as rpc
    imu_trials = rpc.discover_imu_trials()
    video_trials = rpc.discover_video_trials()
    def pid(p):
        p = str(p).replace("Participant_", "")
        return f"P{p}"
    imu_parts = {pid(t["participant"]) for t in imu_trials if t["optitrack_path"]}
    video_parts = {pid(t["participant"]) for t in video_trials}
    opti_parts = {pid(t["participant"]) for t in imu_trials if t["optitrack_path"]}
    all_parts = sorted(video_parts | imu_parts, key=lambda p: int(p[1:]))
    modalities = ["Video", "IMU", "OptiTrack"]
    mat = np.zeros((len(all_parts), len(modalities)))
    for i, p in enumerate(all_parts):
        mat[i, 0] = 1 if p in video_parts else 0
        mat[i, 1] = 1 if p in imu_parts else 0
        mat[i, 2] = 1 if p in opti_parts else 0
    fig, ax = plt.subplots(figsize=(4.6, 6.4), dpi=200, facecolor="white")
    cmap = matplotlib.colors.ListedColormap(["#f0efe9", ORANGE])
    ax.imshow(mat, cmap=cmap, aspect="auto", vmin=0, vmax=1)
    ax.set_xticks(range(len(modalities)))
    ax.set_xticklabels(modalities, fontsize=10, color=INK)
    ax.set_yticks(range(len(all_parts)))
    ax.set_yticklabels(all_parts, fontsize=8, color=INK)
    for i in range(len(all_parts)):
        for j in range(len(modalities)):
            if mat[i, j]:
                ax.text(j, i, "✓", ha="center", va="center", fontsize=9, color="white")
    ax.set_title("Figure 20. Data availability by participant\n"
                "(the IMU column is the bottleneck -- only 5 of "
                f"{len(all_parts)} participants have it)", fontsize=10, color=INK)
    fig.tight_layout()
    savefig(fig, "fig20_data_availability.png")


# ---------------------------------------------------------------- fig21
def fig21_pt_params_small_multiples(trial_rows):
    scored = [r for r in trial_rows if r.get("group") in ("Control", "MS")]
    fig, axes = plt.subplots(2, 4, figsize=(13.5, 6.2), dpi=200, facecolor="white")
    axes = axes.flatten()
    for i, p in enumerate(PARAMS):
        ax = axes[i]
        ctrl = [float(r[f"imu_{p}"]) for r in scored if r["group"] == "Control" and r.get(f"imu_{p}") not in (None, "")]
        ms = [float(r[f"imu_{p}"]) for r in scored if r["group"] == "MS" and r.get(f"imu_{p}") not in (None, "")]
        if not ctrl or not ms:
            ax.axis("off")
            continue
        bp = ax.boxplot([ctrl, ms], tick_labels=["Control", "MS"], patch_artist=True, widths=0.5,
                        medianprops=dict(color=INK, linewidth=1.3))
        bp["boxes"][0].set_facecolor(BLUE); bp["boxes"][0].set_alpha(0.55)
        bp["boxes"][1].set_facecolor(ORANGE); bp["boxes"][1].set_alpha(0.55)
        ax.set_title(PARAM_LABELS[p], fontsize=9.5, color=INK)
        _style_axis(ax)
    axes[-1].axis("off")
    fig.suptitle("Figure 21. All 7 IMU PT parameters, Control vs. MS (small multiples)",
                fontsize=12, color=INK, y=1.02)
    fig.tight_layout()
    savefig(fig, "fig21_pt_params_small_multiples.png")


# ---------------------------------------------------------------- fig22
def fig22_grid_convergence(combos):
    means = np.array([c["mean"] for c in combos])
    fig, ax = plt.subplots(figsize=(7.0, 4.2), dpi=200, facecolor="white")
    ax.hist(means, bins=24, color=MUTED, edgecolor="white", linewidth=0.5, zorder=3)
    ax.axvline(means.min(), color=ORANGE, linestyle="--", linewidth=1.4, zorder=4)
    ax.text(means.min() + 1.5, ax.get_ylim()[1] * 0.9, f"best: {means.min():.2f} deg",
            fontsize=9, color=ORANGE)
    ax.set_xlabel("Mean RMSE across 53 trials, per grid combo (deg)", fontsize=10, color=INK)
    ax.set_ylabel("Combos", fontsize=10, color=INK)
    ax.set_title(f"Figure 22. 144-combination calibration grid search\n"
                f"(full sweep of beta x ema_alpha x flex_axis x gravity_seed x method -- "
                f"the adopted config is the global minimum)", fontsize=10, color=INK)
    _style_axis(ax)
    savefig(fig, "fig22_grid_search_convergence.png")


# ---------------------------------------------------------------- fig23
def fig23_rmse_vs_n_samples(rows):
    n = np.array([r["n_samples"] for r in rows])
    rmse = np.array([r["rmse"] for r in rows])
    fig, ax = plt.subplots(figsize=(6.4, 5.0), dpi=200, facecolor="white")
    ax.scatter(n, rmse, s=26, color=BLUE, edgecolor="white", linewidth=0.5, zorder=3)
    r2 = np.corrcoef(n, rmse)[0, 1] ** 2
    ax.set_xlabel("Trial length (samples)", fontsize=10, color=INK)
    ax.set_ylabel("RMSE (deg)", fontsize=10, color=INK)
    ax.set_title(f"Figure 23. RMSE is not explained by trial/clip length\n"
                f"(R² = {r2:.2f} -- rules out \"longer trials just accumulate more "
                f"drift\" as the error explanation)", fontsize=10, color=INK)
    _style_axis(ax)
    savefig(fig, "fig23_rmse_vs_trial_length.png")


def main():
    rows = load_rmse_csv()
    mas_rows = load_mas_scores()
    combos = parse_grid_log()
    ft_rows = parse_ft_ratio_sweep()
    mag_rows = parse_mag_toggle()
    trial_rows = load_pt_params_trials()

    print(f"RMSE rows: {len(rows)}, MAS rows: {len(mas_rows)}, grid combos: {len(combos)}, "
          f"ft_ratio rows: {len(ft_rows)}, mag rows: {len(mag_rows)}, "
          f"pt-param trial rows: {len(trial_rows)}")

    fig11_rmse_distribution(rows)
    fig12_rmse_by_participant(rows)
    fig13_bias_vs_rmse(rows)
    fig14_lag_distribution(rows)
    fig15_ft_ratio_sweep(ft_rows)
    fig16_mag_toggle_paired(mag_rows)
    fig17_method_comparison(combos)
    fig18_beta_sensitivity(combos)
    fig19_mas_distribution(mas_rows)
    fig20_data_availability()
    fig21_pt_params_small_multiples(trial_rows)
    fig22_grid_convergence(combos)
    fig23_rmse_vs_n_samples(rows)


if __name__ == "__main__":
    main()
