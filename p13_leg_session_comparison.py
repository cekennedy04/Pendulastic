"""
p13_leg_session_comparison.py
==============================
Participant 13: left-vs-right leg, longitudinal (Pre -> Post -> Week 1 Post)
PT score comparison, plus OptiTrack-vs-MediaPipe and OptiTrack-vs-IMU(Viewer)
RMSE agreement.

Reuses pendulastic_pt_score.py's OptiTrack discovery, PT-scoring, and HPE/IMU
curve-alignment code directly, so figures stay consistent with the per-trial
plots in Model_Analysis_Outputs/PT_Scores/.

right_post's ORIGINAL CSV export was a byte-identical duplicate of left_post's
(Motive export error); that bad export is quarantined at OptiTrack_Recordings/
Participant_13_right_post/Session_post/INVALID_duplicate_of_left_post/ so it
can never be picked up by discover_optitrack(). A correct re-export has since
been uploaded (verified via checksum to be genuinely distinct from left_post).

week_1_post is a later timepoint uploaded separately, laid out in a folder
convention discover_optitrack() doesn't recognize (Participant_13/{Left,Right}/
week_1_post/, no Position_N level). It's collected directly via
load_optitrack()/compute_pt_params(), with MediaPipe/IMU RMSE computed by
calling load_hpe_model_curves()'s csv_files override (added for this) so the
same alignment algorithm is reused rather than duplicated. Right/week_1_post
Trial 2 has video+IMU but no OptiTrack export -- excluded like any other
trial with no ground truth.

Other known gaps (see caveat box on the figure):
  - Every valid trial carries an area-ratio quality warning (duo-session
    marker tracking may not fully represent true knee motion).
  - MediaPipe detection rate varies sharply by condition/trial.
  - IMU-vs-OptiTrack RMSE only exists for post-treatment conditions
    (single-sensor "shank pitch" recordings); pre-treatment sessions were
    not recorded with IMU at all.

Run:
    .venv\\Scripts\\python.exe p13_leg_session_comparison.py
"""
from __future__ import annotations

import glob
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import pendulastic_pt_score as pt

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "PT_Scores")
os.makedirs(OUT_DIR, exist_ok=True)

_BG = "#12172a"
_PANEL = "#1c2340"
_GRID = "#3A4A6A"
_TEXT = "#D0DEFF"
_TEXT_MUT = "#90A8CC"

_C_MEDIAPIPE = "#AA44FF"
_C_IMU = "#FF9000"

# Ordered timepoints per leg -- extend here if more timepoints are added later.
_GROUPS = [
    ("Left leg", [("13_left_pre", "Pre"), ("13_left_post", "Post"),
                  ("13_left_week1post", "Week 1\nPost")]),
    ("Right leg", [("13_right_pre", "Pre"), ("13_right_post", "Post"),
                   ("13_right_week1post", "Week 1\nPost")]),
]
_CONDITIONS = [pid for _, tps in _GROUPS for pid, _ in tps]
_COND_LABEL = {pid: label for _, tps in _GROUPS for pid, label in tps}
_COND_COLOR = {
    "13_left_pre": "#00C8FF", "13_left_post": "#0090C8", "13_left_week1post": "#005C82",
    "13_right_pre": "#00E87A", "13_right_post": "#00A860", "13_right_week1post": "#006B3C",
}
_SHORT = {"13_left_pre": "L/Pre", "13_left_post": "L/Post",
          "13_left_week1post": "L/Wk1",
          "13_right_pre": "R/Pre", "13_right_post": "R/Post",
          "13_right_week1post": "R/Wk1"}


def _score_trial(pid, trial, t, angle, curves_csv_files, pos="1"):
    """Shared scoring path: compute_pt_params + load_hpe_model_curves."""
    params = pt.compute_pt_params(t, angle)
    rec = {"pid": pid, "trial": trial, "pt4": None, "mas4": None,
           "mediapipe_rmse": None, "imu_rmse": None}
    if params is None:
        return rec
    pt4 = pt.compute_pt_score_simple(params)
    rec["pt4"] = pt4
    rec["mas4"] = pt.pt_to_mas(pt4)
    curves = pt.load_hpe_model_curves(
        pid, pos, trial, t, angle, params["neutral_deg_raw"],
        csv_files=curves_csv_files)
    for c in curves:
        if c["name"].startswith("mediapipe"):
            rec["mediapipe_rmse"] = c.get("rmse")
        elif c["name"] == "imu_viewer":
            rec["imu_rmse"] = c.get("rmse")
    return rec


def collect_optitrack_trials():
    """The three conditions discover_optitrack() natively recognizes."""
    pids = {"13_left_pre", "13_left_post", "13_right_pre", "13_right_post"}
    records = []
    for entry in pt.discover_optitrack():
        if entry["pid"] not in pids:
            continue
        try:
            t, angle = pt.load_optitrack(entry["path"])
        except Exception:
            continue
        records.append(_score_trial(entry["pid"], entry["trial"], t, angle,
                                    curves_csv_files=None, pos=entry["pos"]))
    return records


def collect_week1post_trials():
    """week_1_post: different folder layout, collected directly rather than
    through discover_optitrack()."""
    records = []
    for leg, pid in (("Left", "13_left_week1post"), ("Right", "13_right_week1post")):
        opti_dir = os.path.join(BASE_DIR, "OptiTrack_Recordings", "Participant_13", leg, "week_1_post")
        rec_dir = os.path.join(BASE_DIR, "Recordings", "Participant_13", leg, "week_1_post")
        for csv_path in sorted(glob.glob(os.path.join(opti_dir, "trial_*_optitrack.csv"))):
            trial = os.path.basename(csv_path).split("_")[1]
            try:
                t, angle = pt.load_optitrack(csv_path)
            except Exception:
                continue
            hpe_files = sorted(glob.glob(
                os.path.join(rec_dir, f"Participant_13_T_{trial}_*.csv")))
            records.append(_score_trial(pid, trial, t, angle, curves_csv_files=hpe_files))
    return records


def collect_trials():
    return collect_optitrack_trials() + collect_week1post_trials()


def make_figure(records):
    fig = plt.figure(figsize=(20, 11), facecolor=_BG)
    gs = fig.add_gridspec(2, 1, height_ratios=[3, 1.3], hspace=0.6,
                          top=0.90, bottom=0.17, left=0.08, right=0.97)
    ax_pt = fig.add_subplot(gs[0])
    ax_rmse = fig.add_subplot(gs[1])

    fig.suptitle("Participant 13 — Left vs Right Leg, Pre → Post → Week 1 Post PT Score,\n"
                 "and OptiTrack Agreement with MediaPipe / IMU",
                 color=_TEXT, fontsize=15, fontweight="bold", y=0.985)

    # ── Panel A: PT score (4-param, primary), per-leg timepoint sequence ────
    ax_pt.set_facecolor(_PANEL)
    for spine in ax_pt.spines.values():
        spine.set_color(_GRID)

    GROUP_GAP = 1.6
    x_positions = {}
    group_span = {}
    x_cursor = 0.0
    for group_name, tps in _GROUPS:
        start = x_cursor
        for i, (pid, _) in enumerate(tps):
            x_positions[pid] = start + i
        x_cursor = start + len(tps) - 1 + 1 + GROUP_GAP
        group_span[group_name] = (start, start + len(tps) - 1)

    rng = np.random.RandomState(13)
    medians = {}
    for group_name, tps in _GROUPS:
        for pid, _ in tps:
            cx = x_positions[pid]
            trials = [r for r in records if r["pid"] == pid]
            vals = [r["pt4"] for r in trials if r["pt4"] is not None]
            n_failed = sum(1 for r in trials if r["pt4"] is None)
            if vals:
                jitter = rng.uniform(-0.10, 0.10, size=len(vals))
                ax_pt.scatter(np.full(len(vals), cx) + jitter, vals,
                             s=100, marker="o", color=_COND_COLOR[pid],
                             edgecolor="#0A0E1A", linewidth=1.2, zorder=3)
                med = float(np.median(vals))
                ax_pt.scatter([cx], [med], marker="_", s=900,
                             color="#FFFFFF", linewidth=2.5, zorder=4)
                medians[pid] = med
            if n_failed:
                ax_pt.text(cx, -0.05,
                           f"({n_failed} trial{'s' if n_failed != 1 else ''}\nno release)",
                           color="#667788", fontsize=7.5, ha="center", va="top")

        # Delta connector between each consecutive pair of timepoints
        for (pid_a, _), (pid_b, _) in zip(tps, tps[1:]):
            xa, xb = x_positions[pid_a], x_positions[pid_b]
            if pid_a in medians and pid_b in medians:
                y0, y1 = medians[pid_a], medians[pid_b]
                ax_pt.annotate("", xy=(xb - 0.18, y1), xytext=(xa + 0.18, y0),
                              arrowprops=dict(arrowstyle="-|>", color="#FFFFFF",
                                              lw=1.6, alpha=0.85))
                delta = y1 - y0
                ax_pt.text((xa + xb) / 2, max(y0, y1) + 0.03,
                           f"Δ{delta:+.3f}", color="#FFFFFF", fontsize=9,
                           ha="center", fontweight="bold")

        lo, hi = group_span[group_name]
        ax_pt.text((lo + hi) / 2, -0.115, group_name, color=_TEXT,
                   fontsize=12, fontweight="bold", ha="center", va="top")

    last_pid = _GROUPS[-1][1][-1][0]
    ax_pt.set_xlim(-0.6, x_positions[last_pid] + 0.9)
    ax_pt.set_ylim(-0.16, 0.55)
    ax_pt.set_xticks(list(x_positions.values()))
    ax_pt.set_xticklabels([_COND_LABEL[c] for c in _CONDITIONS],
                          color=_TEXT, fontsize=10)
    ax_pt.set_ylabel("4-parameter PT score (primary)", color=_TEXT_MUT, fontsize=10.5)
    ax_pt.tick_params(colors=_TEXT_MUT, labelsize=9)
    ax_pt.grid(axis="y", color=_GRID, alpha=0.3, lw=0.6)
    ax_pt.set_title("PT Score Over Time by Leg  (dot = trial, white bar = median)",
                    color=_TEXT, fontsize=11.5, pad=10)

    # ── Panel B: RMSE agreement (degrees) by trial ──────────────────────────
    ax_rmse.set_facecolor(_PANEL)
    for spine in ax_rmse.spines.values():
        spine.set_color(_GRID)

    bar_records = [r for r in records
                   if r["mediapipe_rmse"] is not None or r["imu_rmse"] is not None]
    bar_records.sort(key=lambda r: (_CONDITIONS.index(r["pid"]), int(r["trial"])))

    labels = [f"{_SHORT[r['pid']]}\nT{r['trial']}" for r in bar_records]
    xpos = np.arange(len(bar_records))
    width = 0.36

    mp_vals = [r["mediapipe_rmse"] if r["mediapipe_rmse"] is not None else np.nan for r in bar_records]
    imu_vals = [r["imu_rmse"] if r["imu_rmse"] is not None else np.nan for r in bar_records]

    ax_rmse.bar(xpos - width / 2, mp_vals, width, color=_C_MEDIAPIPE,
               label="OptiTrack vs MediaPipe", zorder=3)
    ax_rmse.bar(xpos + width / 2, imu_vals, width, color=_C_IMU,
               label="OptiTrack vs IMU (Viewer)", zorder=3)

    ax_rmse.set_xticks(xpos)
    ax_rmse.set_xticklabels(labels, color=_TEXT_MUT, fontsize=7.5, rotation=0)
    ax_rmse.set_ylabel("RMSE (deg)", color=_TEXT_MUT, fontsize=10.5)
    ax_rmse.tick_params(colors=_TEXT_MUT, labelsize=8)
    ax_rmse.grid(axis="y", color=_GRID, alpha=0.3, lw=0.6)
    ax_rmse.set_title("Flexion-Angle RMSE Agreement vs OptiTrack Ground Truth  "
                      "(lower = better agreement)",
                      color=_TEXT, fontsize=11.5, pad=10)
    ax_rmse.legend(loc="upper left", facecolor=_PANEL, edgecolor=_GRID,
                   labelcolor=_TEXT, fontsize=9)

    caveat_lines = [
        "Caveats:  right_post's original CSV export was a byte-identical duplicate of left_post's (Motive "
        "export error); a corrected re-export (verified genuinely distinct via checksum) is used here.",
        "week_1_post is a separately-uploaded later timepoint; Right/week_1_post Trial 2 has video+IMU but "
        "no OptiTrack export and is excluded from the OptiTrack-referenced analysis.",
        "Every valid trial carries an area-ratio quality warning (duo-session marker tracking may not fully "
        "represent true knee motion — see per-trial plots).",
        "MediaPipe detection rate varies sharply by condition/trial; low-detection RMSE is less reliable.",
        "IMU-vs-OptiTrack RMSE exists only for post-treatment conditions (single-sensor shank-pitch "
        "recordings) — pre-treatment sessions were not recorded with IMU at all.",
    ]
    fig.text(0.5, 0.085, "\n".join(caveat_lines), color="#8899AA", fontsize=7.6,
             ha="center", va="top", linespacing=1.6)

    out_path = os.path.join(OUT_DIR, "P13_leg_session_comparison.png")
    fig.savefig(out_path, dpi=150, facecolor=_BG)
    plt.close(fig)
    print(f"-> {out_path}")


def print_summary(records):
    print("\nParticipant 13 summary\n" + "=" * 60)
    for cond in _CONDITIONS:
        trials = [r for r in records if r["pid"] == cond]
        vals = [r["pt4"] for r in trials if r["pt4"] is not None]
        print(f"\n{cond}: {len(trials)} trial(s) found, {len(vals)} with valid PT score")
        for r in trials:
            if r["pt4"] is None:
                print(f"  T{r['trial']}: no release detected")
                continue
            mp = f"{r['mediapipe_rmse']:.1f}" if r["mediapipe_rmse"] is not None else "--"
            imu = f"{r['imu_rmse']:.1f}" if r["imu_rmse"] is not None else "--"
            print(f"  T{r['trial']}: PT={r['pt4']:.4f} MAS={r['mas4']:<3}  "
                  f"MediaPipe RMSE={mp}deg  IMU RMSE={imu}deg")


if __name__ == "__main__":
    records = collect_trials()
    print_summary(records)
    make_figure(records)
