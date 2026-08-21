"""
generate_pt_score_critique.py
================================
Critical evaluation of the PRODUCTION PT score (pendulastic_pt_score.py's
compute_pt_params/compute_pt_score -- NOT workbench_engine.windowed_pt_params,
the simpler function generate_multimetric_analysis.py used; see Figure 8's
note on why that distinction matters) computed for IMU, MediaPipe, and
OptiTrack on the unified 49-trial set.

  Figure 8 (fig8_score_naive_vs_logocv.png): the composite PT score's
    Control-vs-MS separation, naive (pooled, no CV -- matches how the
    codebase's own HEALTHY_REF/PT_HEALTHY_MAX validation comment describes
    its methodology) vs leave-one-participant-out AUC. Tests whether the
    codebase's own "p=0.0001" separation claim survives an honest
    generalization test.
  Figure 9 (fig9_healthy_ref_sensitivity.png): HEALTHY_REF is derived from
    n=4 controls (per its own code comment). Leave-one-control-out
    resampling shows how much PT_HEALTHY_MAX would shift depending on
    which 3 of 4 controls anchor the reference.
  Figure 10 (fig10_param_correlation.png): correlation matrix across the 7
    PT parameters -- tests whether "7 parameters" is really 7 independent
    pieces of information or a few collinear ones inflated by count.

Usage:
    .venv\\Scripts\\python.exe generate_pt_score_critique.py
"""
from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.preprocessing import StandardScaler

import rmse_pipeline_common as rpc
import workbench_engine as engine
import sweep_mediapipe_config as mp_sweep
import imu_calibration_tuner as tuner
import pendulastic_pt_score as pts
from generate_paper_figures import BLUE, ORANGE, INK, MUTED, GRID, OUT_DIR, _style_axis
from generate_paper_results_analysis import load_group_and_demo, load_mas_scores

PARAMS = pts._PARAM_KEYS
MODEL_VARIANT = "full"
VIS_THRESH = 0.5


def build_rows():
    imu_trials = rpc.discover_imu_trials()
    video_trials = rpc.discover_video_trials()
    imu_by_key = {t["trial_key"]: t for t in imu_trials if t["optitrack_path"] is not None}
    video_by_key = {t["trial_key"]: t for t in video_trials}
    common_keys = sorted(set(imu_by_key) & set(video_by_key))

    groups = load_group_and_demo()
    mas = load_mas_scores()
    model_path = os.path.join(rpc.BASE_DIR, "models", "mediapipe",
                              f"pose_landmarker_{MODEL_VARIANT}.task")

    rows = []
    for key in common_keys:
        imu_t = imu_by_key[key]
        vid_t = video_by_key[key]
        comp = imu_t["imu_component_paths"]
        try:
            samples = rpc.reconstruct_trial(comp["accel"], comp["gyro"], comp["mag"])
        except Exception:
            samples = None
        if not samples:
            continue
        t_imu, ang_imu = tuner.replay_trial(samples, {"beta": 0.041, "ema_alpha": 0.5,
                                                       "flex_axis_capture": True,
                                                       "gravity_seed": True, "method": "relative"})
        if len(t_imu) < 10:
            continue
        try:
            ref_t, ref_angle, _m = engine.load_optitrack_trial(imu_t["optitrack_path"])
        except Exception:
            continue
        try:
            frames = rpc.extract_landmarks_cached(vid_t, MODEL_VARIANT, model_path)
            t_mp, ang_mp = mp_sweep.angles_from_raw(frames, VIS_THRESH)
        except Exception:
            continue

        pt_imu = pts.compute_pt_params(t_imu, ang_imu)
        pt_opti = pts.compute_pt_params(ref_t, ref_angle)
        pt_mp = pts.compute_pt_params(t_mp, ang_mp)
        if pt_imu is None or pt_opti is None or pt_mp is None:
            continue

        pid = imu_t["participant"].replace("Participant_", "")
        row = {"pid": pid, "group": groups.get(pid, {}).get("group"), "mas": mas.get(pid),
              "imu_pt": pt_imu, "opti_pt": pt_opti, "mp_pt": pt_mp,
              "imu_score": pts.compute_pt_score(pt_imu),
              "opti_score": pts.compute_pt_score(pt_opti),
              "mp_score": pts.compute_pt_score(pt_mp)}
        rows.append(row)
    return rows


def figure8_naive_vs_logocv(rows):
    scored = [r for r in rows if r["group"] in ("Control", "MS")]
    control_scores = [r["imu_score"] for r in scored if r["group"] == "Control"]
    ms_scores = [r["imu_score"] for r in scored if r["group"] == "MS"]

    # Naive comparison -- pooled trials, no participant structure, exactly
    # matching how HEALTHY_REF/PT_HEALTHY_MAX's own code comment describes
    # its validation ("Control-vs-MS separation... Mann-Whitney p=0.0001").
    u_stat, naive_p = stats.mannwhitneyu(ms_scores, control_scores, alternative="two-sided")

    # Honest test -- leave-one-participant-out AUC.
    y = np.array([1 if r["group"] == "MS" else 0 for r in scored])
    groups_arr = np.array([r["pid"] for r in scored])
    X = np.array([[r["imu_score"]] for r in scored])
    logo = LeaveOneGroupOut()
    preds, truths = [], []
    for train_idx, test_idx in logo.split(X, y, groups_arr):
        if len(set(y[train_idx])) < 2:
            continue
        scaler = StandardScaler().fit(X[train_idx])
        clf = LogisticRegression(max_iter=1000)
        clf.fit(scaler.transform(X[train_idx]), y[train_idx])
        proba = clf.predict_proba(scaler.transform(X[test_idx]))[:, 1]
        preds.extend(proba)
        truths.extend(y[test_idx])
    logo_auc = roc_auc_score(truths, preds) if len(set(truths)) > 1 else float("nan")

    fig, axes = plt.subplots(1, 2, figsize=(9.5, 4.4), dpi=200, facecolor="white")
    ax = axes[0]
    rng = np.random.default_rng(0)
    for label, vals, color, xpos in [("Control", control_scores, BLUE, 0), ("MS", ms_scores, ORANGE, 1)]:
        jitter = rng.uniform(-0.08, 0.08, size=len(vals))
        ax.scatter(np.full(len(vals), xpos) + jitter, vals, s=28, color=color,
                  edgecolor="white", linewidth=0.5, alpha=0.8, zorder=3)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["Control", "MS"], fontsize=9, color=INK)
    ax.set_ylabel("Composite PT score (compute_pt_score)", fontsize=9, color=INK)
    ax.set_title(f"Naive (pooled trials)\nMann-Whitney p={naive_p:.4f}", fontsize=9.5, color=INK)
    _style_axis(ax)

    ax = axes[1]
    ax.axhline(0.5, color=MUTED, linewidth=1.0, linestyle="--", zorder=2)
    ax.text(0.35, 0.52, "chance", fontsize=8, color=MUTED)
    if np.isfinite(logo_auc):
        ax.bar([0], [logo_auc], color=ORANGE, width=0.5, zorder=3)
        ax.text(0, logo_auc + 0.02, f"{logo_auc:.2f}", ha="center", fontsize=10, color=INK)
    else:
        ax.text(0, 0.5, "COULD NOT COMPUTE\n\nOnly 1 control participant in this\nmatched set -- holding it out for\ntesting leaves 0 controls to train\non. The test literally cannot run.",
               ha="center", va="center", fontsize=8.5, color=INK,
               bbox=dict(boxstyle="round", facecolor="#fdecea", edgecolor=ORANGE, linewidth=1.2))
    ax.set_xticks([0])
    ax.set_xticklabels(["Composite PT score"], fontsize=9, color=INK)
    ax.set_ylabel("Leave-one-participant-out AUC\n(Control vs. MS)", fontsize=9, color=INK)
    ax.set_ylim(0, 1.05)
    ax.set_title("Honest test:\ndoes it generalize to a new person?", fontsize=9.5, color=INK)
    _style_axis(ax)

    fig.suptitle("Figure 8. The production PT score's own validation methodology,\n"
                "tested honestly (IMU, n=%d trials, %d participants)"
                % (len(scored), len(set(groups_arr))), fontsize=10.5, color=INK, y=1.08)
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "fig8_score_naive_vs_logocv.png")
    fig.savefig(out, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}  (naive Mann-Whitney p={naive_p:.5f}, LOGO-CV AUC={logo_auc:.3f})")


def figure9_healthy_ref_sensitivity(rows):
    control_rows = [r for r in rows if r["group"] == "Control"]
    control_pids = sorted(set(r["pid"] for r in control_rows))
    print(f"Controls contributing to HEALTHY_REF sensitivity check: {control_pids} "
          f"(n={len(control_pids)} -- HEALTHY_REF's own comment says n=4 controls, "
          f"not necessarily the same 4 as this trial-matched set)")

    if len(control_pids) < 2:
        print("Fewer than 2 controls in the matched set -- cannot test leave-one-out "
              "sensitivity meaningfully. Skipping Figure 9.")
        return

    thresholds = []
    labels = []
    for held_out in control_pids:
        remaining = [r for r in control_rows if r["pid"] != held_out]
        if not remaining:
            continue
        ref_vals = {}
        for p in PARAMS:
            vals = [r["imu_pt"][p] for r in remaining if r["imu_pt"].get(p) is not None]
            ref_vals[p] = float(np.median(vals)) if vals else 0.0
        rescored = [pts.compute_pt_score(r["imu_pt"], ref=ref_vals) for r in control_rows]
        pct75 = float(np.percentile(rescored, 75))
        thresholds.append(pct75)
        labels.append(f"excl. P{held_out}")

    fig, ax = plt.subplots(figsize=(5.5, 4), dpi=200, facecolor="white")
    x = np.arange(len(thresholds))
    ax.bar(x, thresholds, color=BLUE, width=0.55, zorder=3)
    ax.axhline(pts.PT_HEALTHY_MAX, color=INK, linewidth=1.4, linestyle="-", zorder=4)
    ax.text(len(x) - 0.4, pts.PT_HEALTHY_MAX + 0.005,
           f"current PT_HEALTHY_MAX={pts.PT_HEALTHY_MAX}", fontsize=7, color=INK)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8, color=INK, rotation=20, ha="right")
    ax.set_ylabel("Recomputed 75th-pct control score\n(would-be PT_HEALTHY_MAX)", fontsize=8.5, color=INK)
    ax.set_title("Figure 9. How much would the \"healthy\" threshold move\n"
                "depending on which control anchors HEALTHY_REF?", fontsize=10, color=INK)
    _style_axis(ax)
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "fig9_healthy_ref_sensitivity.png")
    fig.savefig(out, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    spread = max(thresholds) - min(thresholds)
    print(f"Saved {out}  (threshold range across leave-one-out: "
          f"{min(thresholds):.3f}-{max(thresholds):.3f}, spread={spread:.3f}, "
          f"vs current fixed PT_HEALTHY_MAX={pts.PT_HEALTHY_MAX})")


def figure10_param_correlation(rows):
    X = np.array([[r["imu_pt"][p] for p in PARAMS] for r in rows])
    corr = np.corrcoef(X, rowvar=False)

    fig, ax = plt.subplots(figsize=(5.6, 5.2), dpi=200, facecolor="white")
    im = ax.imshow(corr, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(PARAMS)))
    ax.set_xticklabels(PARAMS, fontsize=8, color=INK, rotation=35, ha="right")
    ax.set_yticks(range(len(PARAMS)))
    ax.set_yticklabels(PARAMS, fontsize=8, color=INK)
    for i in range(len(PARAMS)):
        for j in range(len(PARAMS)):
            v = corr[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7,
                   color="white" if abs(v) > 0.6 else INK)
    fig.colorbar(im, ax=ax, shrink=0.8, label="Pearson r")
    n_strong = int(np.sum((np.abs(corr) > 0.7) & (np.abs(corr) < 0.999)) / 2)
    ax.set_title(f"Figure 10. IMU parameter correlation matrix\n"
                f"({n_strong} pair(s) with |r|>0.7 -- redundant, not independent, information)",
                fontsize=9.5, color=INK)
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "fig10_param_correlation.png")
    fig.savefig(out, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}  ({n_strong} strongly-correlated pairs among {len(PARAMS)} 'independent' parameters)")


def main():
    rows = build_rows()
    print(f"{len(rows)} trials with production PT scores computed (all 3 modalities)\n")
    if not rows:
        return
    figure8_naive_vs_logocv(rows)
    figure9_healthy_ref_sensitivity(rows)
    figure10_param_correlation(rows)


if __name__ == "__main__":
    main()
