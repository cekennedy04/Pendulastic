"""
generate_longitudinal_and_score_figures.py
=============================================
Follow-up figure pass (2026-08-20): per-participant longitudinal change (pre ->
post -> post_1week -> post_1month), Control-vs-MS boxplots for ALL 7 PT
parameters across ALL THREE modalities (not just IMU), and a session-computed
composite PT score compared across OptiTrack / IMU / MediaPipe.

Reuses generate_multimetric_analysis.py's trial-matching + windowed_pt_params
extraction (same 49 three-way-matched trials), but additionally retains
leg/condition/trial_number metadata (dropped by that script) so trials can be
placed on a timeline per participant+leg.

IMPORTANT on the composite score here: this is NOT pendulastic_pt_score's
production compute_pt_score() (that's IMU-only and already covered in fig8).
This is a session-computed, per-modality score -- equal-weighted mean of
|value - control_median| / (|control_median| + eps) over the 7 parameters,
with control_median computed SEPARATELY for each modality from this same
49-trial set -- so OptiTrack, IMU, and MediaPipe are each scored against their
own control distribution (apples-to-apples across modalities, not a
replication of the production formula). Labeled as such on every figure.

Usage:
    .venv\\Scripts\\python.exe generate_longitudinal_and_score_figures.py
"""
from __future__ import annotations

import os
import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import workbench_engine as engine
import rmse_pipeline_common as rpc
import sweep_mediapipe_config as mp_sweep
from generate_paper_figures import BLUE, ORANGE, INK, MUTED, GRID, OUT_DIR, _style_axis
from generate_paper_results_analysis import PARAMS, load_group_and_demo, load_mas_scores

MODEL_VARIANT = "full"
VIS_THRESH = 0.5
MODALITIES = ["opti", "imu", "mp"]
MODALITY_LABELS = {"opti": "OptiTrack", "imu": "IMU", "mp": "MediaPipe"}
MODALITY_COLOR = {"opti": BLUE, "imu": ORANGE, "mp": "#1baf7a"}
PARAM_LABELS = {
    "R2n": "R2n (relaxation)", "N": "N (swings)", "phi_max_ratio": "phi_max ratio",
    "omega_max_n": "omega_max,n", "f": "f (Hz)", "area_ratio": "area ratio",
    "omega_min_n": "omega_min,n",
}


def normalize_condition(raw):
    c = raw.lower()
    if "control" in c:
        return "control"
    if c.startswith("pre"):
        return "pre"
    if "1month" in c or "1_month" in c:
        return "post_1mo"
    if "1week" in c or "1_week" in c or "week_1" in c:
        return "post_1wk"
    if "post" in c:
        return "post"
    return "other"


TIMEPOINT_ORDER = {"pre": 0, "post": 1, "post_1wk": 2, "post_1mo": 3}


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
        leg = imu_t["leg"]
        cond = normalize_condition(imu_t["condition"])
        row = {"pid": pid, "leg": leg, "condition": cond, "raw_condition": imu_t["condition"],
              "trial_number": imu_t["trial_number"], "trial_key": key,
              "group": groups.get(pid, {}).get("group"), "mas": mas.get(pid)}
        row["opti_pt"] = engine.windowed_pt_params(ref_t, ref_angle)
        row["imu_pt"] = engine.windowed_pt_params(t_imu, ang_imu)
        row["mp_pt"] = engine.windowed_pt_params(t_mp, ang_mp)
        rows.append(row)
        print(f"  [{i+1}/{len(common_keys)}] P{pid} {leg} {cond} -> ok")
    return rows


def mas_for(row, mas_rows_by_pid_leg):
    """Look up the MAS grade for this row's participant+leg from mas_scores.csv."""
    key = (row["pid"], row["leg"])
    return mas_rows_by_pid_leg.get(key)


def load_mas_by_pid_leg():
    out = {}
    with open("mas_scores.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r.get("participant"):
                continue
            grade = r["mas_grade"].strip()
            if grade in ("", "-1"):
                continue
            leg = r["leg"].strip().lower()
            key = (r["participant"], leg)
            # keep the last (most recent) grade seen for this participant+leg
            out[key] = grade
    return out


def savefig(fig, name):
    out = os.path.join(OUT_DIR, name)
    fig.savefig(out, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")


# ---------------------------------------------------------------- fig24/25: all-modality small multiples
def fig_modality_small_multiples(rows, modality, fig_num):
    scored = [r for r in rows if r["group"] in ("Control", "MS")]
    fig, axes = plt.subplots(2, 4, figsize=(13.5, 6.2), dpi=200, facecolor="white")
    axes = axes.flatten()
    for i, p in enumerate(PARAMS):
        ax = axes[i]
        ctrl = [r[f"{modality}_pt"][p] for r in scored if r["group"] == "Control"]
        ms = [r[f"{modality}_pt"][p] for r in scored if r["group"] == "MS"]
        bp = ax.boxplot([ctrl, ms], tick_labels=["Control", "MS"], patch_artist=True, widths=0.5,
                        medianprops=dict(color=INK, linewidth=1.3))
        bp["boxes"][0].set_facecolor(BLUE); bp["boxes"][0].set_alpha(0.55)
        bp["boxes"][1].set_facecolor(ORANGE); bp["boxes"][1].set_alpha(0.55)
        ax.set_title(PARAM_LABELS[p], fontsize=9.5, color=INK)
        _style_axis(ax)
    axes[-1].axis("off")
    fig.suptitle(f"Figure {fig_num}. All 7 {MODALITY_LABELS[modality]} PT parameters, "
                f"Control vs. MS (small multiples)", fontsize=12, color=INK, y=1.02)
    fig.tight_layout()
    savefig(fig, f"fig{fig_num}_pt_params_small_multiples_{modality}.png")


# ---------------------------------------------------------------- fig26: per-participant longitudinal, all params
def fig26_longitudinal_all_params(rows, mas_by_pid_leg):
    ms_rows = [r for r in rows if r["group"] == "MS" and r["condition"] in TIMEPOINT_ORDER]
    traces = {}
    for r in ms_rows:
        traces.setdefault((r["pid"], r["leg"]), []).append(r)
    traces = {k: v for k, v in traces.items() if len({r["condition"] for r in v}) >= 2}

    fig, axes = plt.subplots(2, 4, figsize=(15.5, 7.0), dpi=200, facecolor="white")
    axes = axes.flatten()
    colors = plt.cm.tab10(np.linspace(0, 1, max(len(traces), 1)))
    for i, p in enumerate(PARAMS):
        ax = axes[i]
        for ci, (key, trial_list) in enumerate(traces.items()):
            by_cond = {}
            for r in trial_list:
                by_cond.setdefault(r["condition"], []).append(r["imu_pt"][p])
            conds = sorted(by_cond, key=lambda c: TIMEPOINT_ORDER[c])
            xs = [TIMEPOINT_ORDER[c] for c in conds]
            ys = [np.mean(by_cond[c]) for c in conds]
            pid, leg = key
            mas_grade = mas_by_pid_leg.get((pid, leg), "?")
            label = f"P{pid} {leg[0].upper()} (MAS {mas_grade})"
            ax.plot(xs, ys, marker="o", markersize=4, linewidth=1.6, color=colors[ci],
                    label=label if i == 0 else None, zorder=3)
        ax.set_xticks(range(4))
        ax.set_xticklabels(["Pre", "Post", "1wk", "1mo"], fontsize=8, color=INK)
        ax.set_title(PARAM_LABELS[p], fontsize=9.5, color=INK)
        _style_axis(ax)
    axes[-1].axis("off")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower right", bbox_to_anchor=(0.98, 0.05), fontsize=9, frameon=True)
    fig.suptitle("Figure 26. Per-participant change over time, all 7 IMU parameters\n"
                "(each line = one participant+leg with >=2 timepoints; MAS grade in the legend "
                "is that leg's most recent clinician-assessed grade)", fontsize=12, color=INK, y=1.04)
    fig.tight_layout()
    savefig(fig, "fig26_longitudinal_all_params.png")


# ---------------------------------------------------------------- fig27: R2n longitudinal detail (MAS-labeled)
def fig27_r2n_longitudinal_detail(rows, mas_by_pid_leg):
    ms_rows = [r for r in rows if r["group"] == "MS" and r["condition"] in TIMEPOINT_ORDER]
    traces = {}
    for r in ms_rows:
        traces.setdefault((r["pid"], r["leg"]), []).append(r)
    traces = {k: v for k, v in traces.items() if len({r["condition"] for r in v}) >= 2}

    fig, ax = plt.subplots(figsize=(9.0, 6.0), dpi=200, facecolor="white")
    colors = plt.cm.tab10(np.linspace(0, 1, max(len(traces), 1)))
    for ci, (key, trial_list) in enumerate(traces.items()):
        by_cond = {}
        for r in trial_list:
            by_cond.setdefault(r["condition"], []).append(r["imu_pt"]["R2n"])
        conds = sorted(by_cond, key=lambda c: TIMEPOINT_ORDER[c])
        xs = [TIMEPOINT_ORDER[c] for c in conds]
        ys = [np.mean(by_cond[c]) for c in conds]
        pid, leg = key
        mas_grade = mas_by_pid_leg.get((pid, leg), "?")
        ax.plot(xs, ys, marker="o", markersize=7, linewidth=2.2, color=colors[ci], zorder=3)
        ax.annotate(f"P{pid} {leg[0].upper()}\nMAS {mas_grade}", (xs[-1], ys[-1]),
                    textcoords="offset points", xytext=(8, 0), fontsize=9, color=colors[ci])
    ax.set_xticks(range(4))
    ax.set_xticklabels(["Pre", "Post", "Week 1 Post", "Month 1 Post"], fontsize=10, color=INK)
    ax.set_ylabel("R2n (relaxation index)", fontsize=11, color=INK)
    ax.set_title("Figure 27. R2n over time, per participant+leg\n"
                "(labeled with each leg's clinician-assessed MAS grade -- "
                "the primary spasticity indicator's actual trajectory)", fontsize=11, color=INK)
    _style_axis(ax)
    fig.tight_layout()
    savefig(fig, "fig27_r2n_longitudinal_detail.png")


# ---------------------------------------------------------------- composite score helper
def compute_control_refs(rows):
    refs = {}
    for m in MODALITIES:
        refs[m] = {}
        for p in PARAMS:
            vals = [r[f"{m}_pt"][p] for r in rows if r["group"] == "Control"]
            refs[m][p] = np.median(vals) if vals else 0.0
    return refs


def composite_score(row, modality, refs):
    total = 0.0
    for p in PARAMS:
        ref = refs[modality][p]
        val = row[f"{modality}_pt"][p]
        eps = max(abs(ref), 0.05)
        total += abs(val - ref) / (abs(ref) + eps)
    return total / len(PARAMS)


# ---------------------------------------------------------------- fig28: score by modality, grouped bar
def fig28_score_by_modality(rows, refs):
    scored = [r for r in rows if r["group"] in ("Control", "MS")]
    for r in scored:
        r["_scores"] = {m: composite_score(r, m, refs) for m in MODALITIES}

    fig, ax = plt.subplots(figsize=(6.6, 5.0), dpi=200, facecolor="white")
    x = np.arange(2)
    width = 0.25
    for mi, m in enumerate(MODALITIES):
        ctrl_scores = [r["_scores"][m] for r in scored if r["group"] == "Control"]
        ms_scores = [r["_scores"][m] for r in scored if r["group"] == "MS"]
        means = [np.mean(ctrl_scores), np.mean(ms_scores)]
        ax.bar(x + (mi - 1) * width, means, width=width, color=MODALITY_COLOR[m],
              label=MODALITY_LABELS[m], edgecolor="white", linewidth=0.6, zorder=3)
    ax.set_xticks(x)
    ax.set_xticklabels(["Control", "MS"], fontsize=11, color=INK)
    ax.set_ylabel("Session-computed composite PT score\n(mean |deviation| from each modality's own control median)",
                 fontsize=9.5, color=INK)
    ax.set_title("Figure 28. Composite score by modality\n"
                "(each modality scored against its OWN control distribution -- "
                "not the production compute_pt_score(); see fig8 for that)", fontsize=10.5, color=INK)
    ax.legend(frameon=False, fontsize=9)
    _style_axis(ax)
    savefig(fig, "fig28_score_by_modality.png")
    return scored


# ---------------------------------------------------------------- fig29: cross-modality score correlation
def fig29_score_correlation(scored):
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 5.2), dpi=200, facecolor="white")
    pairs = [("opti", "imu"), ("opti", "mp")]
    for ax, (a, b) in zip(axes, pairs):
        xa = [r["_scores"][a] for r in scored]
        yb = [r["_scores"][b] for r in scored]
        colors = [BLUE if r["group"] == "Control" else ORANGE for r in scored]
        ax.scatter(xa, yb, s=30, c=colors, edgecolor="white", linewidth=0.4, zorder=3)
        if len(xa) > 2 and np.std(xa) > 0 and np.std(yb) > 0:
            r = np.corrcoef(xa, yb)[0, 1]
        else:
            r = float("nan")
        lims = [0, max(max(xa), max(yb)) * 1.05]
        ax.plot(lims, lims, color=MUTED, linestyle="--", linewidth=1.0, zorder=2)
        ax.set_xlabel(f"{MODALITY_LABELS[a]} composite score", fontsize=10, color=INK)
        ax.set_ylabel(f"{MODALITY_LABELS[b]} composite score", fontsize=10, color=INK)
        ax.set_title(f"{MODALITY_LABELS[a]} vs. {MODALITY_LABELS[b]}  (r={r:.2f})", fontsize=10, color=INK)
        _style_axis(ax)
    fig.suptitle("Figure 29. Does the composite score agree across modalities?\n"
                "(blue=Control, orange=MS; points on the diagonal = modalities agree on this trial's severity)",
                fontsize=11, color=INK, y=1.06)
    fig.tight_layout()
    savefig(fig, "fig29_score_correlation.png")


# ---------------------------------------------------------------- fig30: MAS scorecard table (per participant)
def fig30_mas_scorecard(rows, mas_by_pid_leg, refs):
    by_pid_leg = {}
    for r in rows:
        by_pid_leg.setdefault((r["pid"], r["leg"]), []).append(r)

    entries = []
    for (pid, leg), trial_list in sorted(by_pid_leg.items(), key=lambda kv: (int(kv[0][0]), kv[0][1])):
        group = trial_list[0]["group"]
        mas_grade = mas_by_pid_leg.get((pid, leg), "-")
        opti_s = np.mean([composite_score(r, "opti", refs) for r in trial_list])
        imu_s = np.mean([composite_score(r, "imu", refs) for r in trial_list])
        mp_s = np.mean([composite_score(r, "mp", refs) for r in trial_list])
        entries.append((f"P{pid}", leg, group, mas_grade, len(trial_list), opti_s, imu_s, mp_s))

    fig, ax = plt.subplots(figsize=(11.5, 0.42 * len(entries) + 1.2), dpi=200, facecolor="white")
    ax.axis("off")
    col_labels = ["Participant", "Leg", "Group", "MAS", "n trials",
                 "OptiTrack score", "IMU score", "MediaPipe score"]
    cell_text = [[e[0], e[1].capitalize(), e[2], e[3], e[4],
                 f"{e[5]:.2f}", f"{e[6]:.2f}", f"{e[7]:.2f}"] for e in entries]
    tab = ax.table(cellText=cell_text, colLabels=col_labels, loc="center", cellLoc="center")
    tab.auto_set_font_size(False)
    tab.set_fontsize(10)
    tab.scale(1, 1.6)
    for (row_i, col_i), cell in tab.get_celld().items():
        if row_i == 0:
            cell.set_facecolor(INK)
            cell.set_text_props(color="white", weight="bold")
        else:
            cell.set_facecolor("#f7f6f2" if row_i % 2 == 0 else "white")
    ax.set_title("Figure 30. Per-participant, per-leg scorecard\n"
                "(MAS grade alongside the session-computed composite score from each modality)",
                fontsize=12, color=INK, pad=14)
    fig.tight_layout()
    savefig(fig, "fig30_mas_scorecard.png")


def main():
    rows = build_rows()
    print(f"\n{len(rows)} trials with all 3 modalities + leg/condition metadata\n")
    if not rows:
        print("No trials -- aborting.")
        return

    mas_by_pid_leg = load_mas_by_pid_leg()

    fig_modality_small_multiples(rows, "opti", 24)
    fig_modality_small_multiples(rows, "mp", 25)
    fig26_longitudinal_all_params(rows, mas_by_pid_leg)
    fig27_r2n_longitudinal_detail(rows, mas_by_pid_leg)

    refs = compute_control_refs(rows)
    scored = fig28_score_by_modality(rows, refs)
    fig29_score_correlation(scored)
    fig30_mas_scorecard(rows, mas_by_pid_leg, refs)

    out_path = "Model_Analysis_Outputs/longitudinal_trials.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["pid", "leg", "condition", "raw_condition", "trial_number", "trial_key",
                     "group", "mas"] + [f"{m}_{p}" for m in MODALITIES for p in PARAMS]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            flat = {k: r[k] for k in ["pid", "leg", "condition", "raw_condition", "trial_number",
                                      "trial_key", "group", "mas"]}
            for m in MODALITIES:
                for p in PARAMS:
                    flat[f"{m}_{p}"] = r[f"{m}_pt"][p]
            writer.writerow(flat)
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
