"""
generate_multimetric_analysis.py
===================================
The "need all metrics combined" story: computes all 7 Popovic PT parameters
for IMU, MediaPipe, AND OptiTrack on the same trials, then:

  Figure 6 (fig6_metric_effect_heatmap.png): heatmap of standardized effect
    size (Cohen's d, MAS=0 vs MAS>0) per metric x modality -- shows which
    metrics carry discriminating signal in which modality (the case for why
    no single metric is reliable across modalities).
  Figure 7 (fig7_single_vs_combined_auc.png): ROC-AUC for classifying
    MAS=0 vs MAS>0 using each IMU metric alone vs all 7 combined (logistic
    regression), leave-one-participant-out cross-validated -- the
    quantitative version of "we need these combined."

Trial matching uses rmse_pipeline_common's shared trial_key namespace
(discover_imu_trials() + discover_video_trials()) so IMU/MediaPipe/OptiTrack
values line up on the same physical trial. MediaPipe reuses the landmark
cache from prior runs -- no new pose inference for already-cached trials.

Usage:
    .venv\\Scripts\\python.exe generate_multimetric_analysis.py
"""
from __future__ import annotations

import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.preprocessing import StandardScaler

import workbench_engine as engine
import rmse_pipeline_common as rpc
import sweep_mediapipe_config as mp_sweep
from generate_paper_figures import BLUE, ORANGE, INK, MUTED, GRID, OUT_DIR, _style_axis
from generate_paper_results_analysis import PARAMS, load_group_and_demo, load_mas_scores

MODEL_VARIANT = "full"
VIS_THRESH = 0.5
MODALITIES = ["opti", "imu", "mp"]
MODALITY_LABELS = {"opti": "OptiTrack", "imu": "IMU", "mp": "MediaPipe"}


def build_unified_rows():
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
    for i, key in enumerate(common_keys):
        imu_t = imu_by_key[key]
        vid_t = video_by_key[key]
        comp = imu_t["imu_component_paths"]
        try:
            samples = rpc.reconstruct_trial(comp["accel"], comp["gyro"], comp["mag"])
        except Exception:
            samples = None
        if not samples:
            continue
        import imu_calibration_tuner as tuner
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
        if np.count_nonzero(np.isfinite(ang_mp)) < 10:
            continue

        pid = imu_t["participant"].replace("Participant_", "")
        row = {"pid": pid, "trial_key": key, "group": groups.get(pid, {}).get("group"),
              "mas": mas.get(pid)}
        row["opti_pt"] = engine.windowed_pt_params(ref_t, ref_angle)
        row["imu_pt"] = engine.windowed_pt_params(t_imu, ang_imu)
        row["mp_pt"] = engine.windowed_pt_params(t_mp, ang_mp)
        rows.append(row)
        print(f"  [{i+1}/{len(common_keys)}] {pid} {key} -> ok")
    return rows


def cohens_d(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return np.nan
    pooled_sd = np.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1))
                        / (len(a) + len(b) - 2))
    if pooled_sd < 1e-9:
        return np.nan
    return (a.mean() - b.mean()) / pooled_sd


def figure6_effect_heatmap(rows):
    scored = [r for r in rows if r["mas"] is not None]
    d_matrix = np.full((len(PARAMS), len(MODALITIES)), np.nan)
    for pi, p in enumerate(PARAMS):
        for mi, mod in enumerate(MODALITIES):
            mas0 = [r[f"{mod}_pt"][p] for r in scored if r["mas"] == 0]
            mas_any = [r[f"{mod}_pt"][p] for r in scored if r["mas"] and r["mas"] > 0]
            d_matrix[pi, mi] = cohens_d(mas_any, mas0)

    fig, ax = plt.subplots(figsize=(5.2, 5.6), dpi=200, facecolor="white")
    vmax = np.nanmax(np.abs(d_matrix)) if np.any(np.isfinite(d_matrix)) else 1.0
    im = ax.imshow(d_matrix, cmap="RdBu_r", vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_xticks(range(len(MODALITIES)))
    ax.set_xticklabels([MODALITY_LABELS[m] for m in MODALITIES], fontsize=9, color=INK)
    ax.set_yticks(range(len(PARAMS)))
    ax.set_yticklabels(PARAMS, fontsize=9, color=INK)
    for pi in range(len(PARAMS)):
        for mi in range(len(MODALITIES)):
            v = d_matrix[pi, mi]
            if np.isfinite(v):
                ax.text(mi, pi, f"{v:.2f}", ha="center", va="center", fontsize=8,
                       color="white" if abs(v) > vmax * 0.5 else INK)
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("Cohen's d (MAS>0 vs MAS=0)", fontsize=8, color=INK)
    ax.set_title("Figure 6. Which metrics discriminate spasticity,\nby modality "
                "(no single metric lights up everywhere)", fontsize=10, color=INK)
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "fig6_metric_effect_heatmap.png")
    fig.savefig(out, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")
    return scored


def figure7_single_vs_combined_auc(scored):
    y = np.array([1 if r["mas"] and r["mas"] > 0 else 0 for r in scored])
    groups_arr = np.array([r["pid"] for r in scored])
    n_pos, n_neg = int(y.sum()), int((1 - y).sum())
    print(f"Classification set: n={len(y)}, MAS>0={n_pos}, MAS=0={n_neg}, "
          f"participants={sorted(set(groups_arr))}")

    X_all = np.array([[r["imu_pt"][p] for p in PARAMS] for r in scored])
    logo = LeaveOneGroupOut()

    def loo_auc(X):
        preds, truths = [], []
        for train_idx, test_idx in logo.split(X, y, groups_arr):
            if len(set(y[train_idx])) < 2:
                continue
            scaler = StandardScaler().fit(X[train_idx])
            clf = LogisticRegression(max_iter=1000, C=1.0)
            clf.fit(scaler.transform(X[train_idx]), y[train_idx])
            proba = clf.predict_proba(scaler.transform(X[test_idx]))[:, 1]
            preds.extend(proba)
            truths.extend(y[test_idx])
        if len(set(truths)) < 2:
            return np.nan
        return roc_auc_score(truths, preds)

    single_aucs = {}
    for pi, p in enumerate(PARAMS):
        single_aucs[p] = loo_auc(X_all[:, [pi]])
    combined_auc = loo_auc(X_all)

    labels = PARAMS + ["ALL 7\ncombined"]
    values = [single_aucs[p] for p in PARAMS] + [combined_auc]
    colors = [BLUE] * len(PARAMS) + [ORANGE]

    fig, ax = plt.subplots(figsize=(7.5, 4.4), dpi=200, facecolor="white")
    x = np.arange(len(labels))
    ax.bar(x, values, color=colors, edgecolor="white", linewidth=0.6, width=0.65, zorder=3)
    ax.axhline(0.5, color=MUTED, linewidth=1.0, linestyle="--", zorder=2)
    ax.text(len(labels) - 0.4, 0.52, "chance", fontsize=7, color=MUTED)
    for xi, v in zip(x, values):
        if np.isfinite(v):
            ax.text(xi, v + 0.015, f"{v:.2f}", ha="center", fontsize=8, color=INK)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8, color=INK, rotation=20, ha="right")
    ax.set_ylabel("Leave-one-participant-out AUC\n(MAS=0 vs MAS>0)", fontsize=9, color=INK)
    ax.set_ylim(0, 1.05)
    ax.set_title(f"Figure 7. Single-metric vs. combined-metric classification\n"
                f"(IMU, n={len(y)} trials, {len(set(groups_arr))} participants -- "
                f"read with the small-n caveat below)", fontsize=10, color=INK)
    _style_axis(ax)
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "fig7_single_vs_combined_auc.png")
    fig.savefig(out, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")
    print(f"Single-metric AUCs: {single_aucs}")
    print(f"Combined AUC: {combined_auc:.3f}")


def main():
    rows = build_unified_rows()
    print(f"\n{len(rows)} trials with all 3 modalities computed\n")
    if not rows:
        print("No trials -- aborting.")
        return
    scored = figure6_effect_heatmap(rows)
    figure7_single_vs_combined_auc(scored)

    out_path = "Model_Analysis_Outputs/multimetric_trials.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["pid", "trial_key", "group", "mas"] + \
            [f"{m}_{p}" for m in MODALITIES for p in PARAMS]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            flat = {"pid": r["pid"], "trial_key": r["trial_key"], "group": r["group"], "mas": r["mas"]}
            for m in MODALITIES:
                for p in PARAMS:
                    flat[f"{m}_{p}"] = r[f"{m}_pt"][p]
            writer.writerow(flat)
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
