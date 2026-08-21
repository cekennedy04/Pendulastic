"""
generate_group_condition_figures.py
======================================
Figures 4-5 for the results draft: PT metrics by group (Control vs MS) and
by treatment timepoint (pre vs post, MS participants only). Extends
generate_paper_results_analysis.py's per-trial data with the recording
condition (pre/post/control), which that script didn't capture.

Figure 4 (fig4_metrics_by_group.png): R2n, N, area_ratio by group
  (Control vs MS), individual-participant means overlaid on group bars --
  makes the n=1-control-arm limitation visually honest rather than hidden
  behind an error bar.
Figure 5 (fig5_pre_post.png): R2n by pre/post timepoint, MS participants
  who have both (paired where possible) -- small-n, framed as such.

Usage:
    .venv\\Scripts\\python.exe generate_group_condition_figures.py
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
from generate_paper_figures import BLUE, ORANGE, INK, MUTED, GRID, OUT_DIR, _style_axis
from generate_paper_results_analysis import PARAMS

# Perez-lineage palette extended with slot 3 (aqua) for a third category
AQUA = "#1baf7a"


def normalize_condition(raw_condition: str) -> str:
    c = raw_condition.lower()
    if "control" in c:
        return "control"
    if "post" in c:
        return "post"
    if "pre" in c:
        return "pre"
    return "other"


def build_rows():
    trials = batch.discover_trials()
    matched = [t for t in trials if t["optitrack_path"] is not None]

    # Reuse the same group/demographics loader as the main analysis script
    from generate_paper_results_analysis import load_group_and_demo
    groups = load_group_and_demo()

    rows = []
    for t in matched:
        validations = {
            "accel": engine.validate_component_csv(t["accel"], "accel"),
            "gyro": engine.validate_component_csv(t["gyro"], "gyro"),
            "mag": engine.validate_component_csv(t["mag"], "mag"),
            "imu": engine.validate_component_csv(t["imu"], "imu"),
        }
        if any(not v["ok"] for v in validations.values()):
            continue
        try:
            t_imu, ang_imu, _ref = engine.load_imu_trial_from_components(validations, method="relative")
        except Exception:
            continue
        if len(t_imu) < 10:
            continue
        imu_pt = engine.windowed_pt_params(t_imu, ang_imu)
        pid = t["participant"].replace("Participant_", "")
        raw_cond = os.path.basename(os.path.dirname(t["imu"]))
        row = {"pid": pid, "group": groups.get(pid, {}).get("group"),
              "condition": normalize_condition(raw_cond), "raw_condition": raw_cond}
        for p in PARAMS:
            row[f"imu_{p}"] = imu_pt[p]
        rows.append(row)
    return rows


def figure4_metrics_by_group(rows):
    params = {"R2n": "Relaxation index (R2n)", "N": "Oscillation count (N)",
             "area_ratio": "Area ratio"}
    groups_order = ["Control", "MS"]
    colors = {"Control": BLUE, "MS": ORANGE}

    fig, axes = plt.subplots(1, len(params), figsize=(4.2 * len(params), 4.4), dpi=200,
                             facecolor="white")
    for ax, (p, label) in zip(axes, params.items()):
        by_pid = {}
        for r in rows:
            if r["group"] not in groups_order:
                continue
            by_pid.setdefault((r["group"], r["pid"]), []).append(r[f"imu_{p}"])

        x = np.arange(len(groups_order))
        group_means, group_sds, group_ns = [], [], []
        for gi, g in enumerate(groups_order):
            vals = [v for (gg, pid), vs in by_pid.items() if gg == g for v in vs]
            group_means.append(np.mean(vals) if vals else np.nan)
            group_sds.append(np.std(vals, ddof=1) if len(vals) > 1 else 0)
            group_ns.append(len(set(pid for (gg, pid) in by_pid if gg == g)))
        ax.bar(x, group_means, yerr=group_sds, capsize=4,
               color=[colors[g] for g in groups_order], edgecolor="white",
               linewidth=0.6, width=0.55, zorder=2, alpha=0.55,
               error_kw={"ecolor": INK, "elinewidth": 1.2, "capthick": 1.2})

        # Overlay individual-participant means as dots, jittered -- makes
        # the n=1-control-arm limitation visible rather than hidden by an
        # error bar computed over pooled trials.
        rng = np.random.default_rng(0)
        for gi, g in enumerate(groups_order):
            pids = sorted(set(pid for (gg, pid) in by_pid if gg == g))
            for pid in pids:
                vals = by_pid[(g, pid)]
                jitter = rng.uniform(-0.12, 0.12)
                ax.scatter([gi + jitter], [np.mean(vals)], s=34, facecolor="white",
                          edgecolor=INK, linewidth=1.1, zorder=4)

        ax.set_xticks(x)
        ax.set_xticklabels([f"{g}\n(n={n} participants)" for g, n in zip(groups_order, group_ns)],
                           fontsize=8, color=INK)
        ax.set_title(label, fontsize=9, color=INK)
        ax.set_ylabel("IMU-derived value\n(group mean ± SD; dots = per-participant mean)",
                      fontsize=7.5, color=INK)
        _style_axis(ax)
    fig.suptitle("Figure 4. IMU-derived pendulum-test metrics: Control vs. MS\n"
                "(dots show every participant individually -- control arm is n=1)",
                fontsize=10.5, color=INK, y=1.06)
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "fig4_metrics_by_group.png")
    fig.savefig(out, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")


def figure5_pre_post(rows):
    ms_rows = [r for r in rows if r["group"] == "MS" and r["condition"] in ("pre", "post")]
    by_pid_cond = {}
    for r in ms_rows:
        by_pid_cond.setdefault((r["pid"], r["condition"]), []).append(r["imu_R2n"])

    pids_with_both = sorted({pid for (pid, cond) in by_pid_cond if
                             (pid, "pre") in by_pid_cond and (pid, "post") in by_pid_cond})
    pids_pre_only = sorted({pid for (pid, cond) in by_pid_cond if cond == "pre"} - set(pids_with_both))
    pids_post_only = sorted({pid for (pid, cond) in by_pid_cond if cond == "post"} - set(pids_with_both))

    fig, ax = plt.subplots(figsize=(5, 4.4), dpi=200, facecolor="white")
    x_pre, x_post = 0, 1
    for pid in pids_with_both:
        pre_mean = np.mean(by_pid_cond[(pid, "pre")])
        post_mean = np.mean(by_pid_cond[(pid, "post")])
        ax.plot([x_pre, x_post], [pre_mean, post_mean], color=MUTED, linewidth=1.2,
               marker="o", markersize=6, markerfacecolor=ORANGE, markeredgecolor="white",
               zorder=3, label=f"P{pid}")
        ax.annotate(f"P{pid}", (x_post, post_mean), textcoords="offset points",
                   xytext=(6, 0), fontsize=8, color=INK, va="center")
    for pid in pids_pre_only:
        ax.scatter([x_pre], [np.mean(by_pid_cond[(pid, "pre")])], s=40, facecolor=BLUE,
                  edgecolor="white", zorder=3, marker="s")
        ax.annotate(f"P{pid} (pre only)", (x_pre, np.mean(by_pid_cond[(pid, "pre")])),
                   textcoords="offset points", xytext=(-8, 0), fontsize=7, color=MUTED,
                   ha="right", va="center")
    for pid in pids_post_only:
        ax.scatter([x_post], [np.mean(by_pid_cond[(pid, "post")])], s=40, facecolor=BLUE,
                  edgecolor="white", zorder=3, marker="s")
        ax.annotate(f"P{pid} (post only)", (x_post, np.mean(by_pid_cond[(pid, "post")])),
                   textcoords="offset points", xytext=(8, 0), fontsize=7, color=MUTED,
                   va="center")

    ax.set_xticks([x_pre, x_post])
    ax.set_xticklabels(["Pre", "Post"], fontsize=9, color=INK)
    ax.set_xlim(-0.4, 1.6)
    ax.set_ylabel("IMU-derived relaxation index (R2n)", fontsize=9, color=INK)
    ax.set_title(f"Figure 5. Pre vs. post, MS participants\n"
                f"({len(pids_with_both)} with both timepoints, "
                f"{len(pids_pre_only) + len(pids_post_only)} with only one)",
                fontsize=10, color=INK)
    _style_axis(ax)
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "fig5_pre_post.png")
    fig.savefig(out, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}  (paired: {pids_with_both}, pre-only: {pids_pre_only}, "
          f"post-only: {pids_post_only})")


def main():
    rows = build_rows()
    print(f"{len(rows)} trials collected")
    conditions_seen = sorted(set(r["raw_condition"] for r in rows))
    print(f"raw conditions seen: {conditions_seen}\n")
    figure4_metrics_by_group(rows)
    figure5_pre_post(rows)


if __name__ == "__main__":
    main()
