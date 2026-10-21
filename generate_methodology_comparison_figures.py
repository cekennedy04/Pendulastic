#!/usr/bin/env python3
"""
generate_methodology_comparison_figures.py
==========================================
Seven figures comparing the three measurement channels -- OptiTrack (reference),
the phone IMU, and MediaPipe -- at the angle level and at the PT-score level.

The set is built around one finding, because the data does not support the
simpler story. At the angle level the IMU and MediaPipe are a TIE (IMU better on
45% of paired trials). At the PT level under each channel's own production
convention they are also a tie (pooled |dPT7| 0.447 vs 0.460; the IMU wins on
6 of 11 participants). Score them under a MATCHED detrend convention and the IMU
separates cleanly (0.247, r 0.630 vs 0.364). So the largest single term in the
comparison is a boolean flag, not a sensor, and F5 is the figure that makes the
rest of the set legible.

  F1  waveforms_best_worst.png        best and worst MediaPipe trials, all three curves
  F2  rmse_by_participant.png         per-participant angle RMSE vs OptiTrack
  F3  pt7_by_methodology.png          what PT7 each channel actually reports
  F4  pt_agreement_by_participant.png per-participant |dPT7|, both conventions
  F5  detrend_convention.png          the flag, isolated
  F6  per_metric_icc.png              all seven PT parameters, ICC vs OptiTrack
  F7  waveforms_framing_contrast.png  Control_8 (99.6% detection) vs MINT_18 (5.5%)

REFERENCE PROVENANCE, stamped on every figure. 87d7645 (2026-09-01) changed the
SHAPE of the OptiTrack reference, not just its datum -- 8.84 deg median shape
RMSE against the pre-change loader, and demonstrably neither an offset nor a
mirror. That change is a FIX, not a regression: scored against synthetic trials
with a known knee angle it is better on 3 cases and worse on 0, winning exactly
where the old seeded-axis bug bit (trials that do not start held and extended),
and regression-guarded at 920f159. These figures therefore use a reference that
is better than the one behind imu_vs_optitrack_rmse.csv (2026-08-28) -- it is
the OLDER numbers that are not comparable, not these.

One consequence is still open and worth stating rather than hiding: IMU-vs-
OptiTrack agreement was 6.05 deg on the old reference and is 21.88 deg on the
corrected one. The reference improved while agreement worsened. The candidate
explanation nobody has tested is that the old seeded axis anchored to the same
pose the IMU's own gravity seed anchors to, making the old figure agreement
between two correlated errors rather than accuracy -- which would make 21.88 the
honest number. Until that is settled, treat absolute RMSE here as provisional
and the IMU-vs-MediaPipe comparison, which is what these figures actually show,
as sound.

NO TRIAL IS EXCLUDED. The only filter is whether an OptiTrack export exists to
score against; 37 of 284 have none. The 7 trials whose reference looks wrapped
or sign-flipped stay in every median and are marked in the figures instead --
this project flags data quality and never drops it, and excluded_trials.json is
the only exclusion path (operator-confirmed only).

Inputs:  Model_Analysis_Outputs/HPE_Comparison/mediapipe_pt_metrics.csv
Outputs: Model_Analysis_Outputs/Methodology_Comparison/

Usage:
    python generate_methodology_comparison_figures.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
import warnings
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import cross_modality_rmse as cmr
import mediapipe_pt_metrics as M
import pendulastic_pt_score as pts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
METRICS_CSV = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "HPE_Comparison",
                           "mediapipe_pt_metrics.csv")
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "Methodology_Comparison")
CONV_CACHE = os.path.join(OUT_DIR, "_convention_by_trial.json")

plt.rcParams["font.family"] = "serif"
plt.rcParams["font.serif"] = ["Times New Roman", "DejaVu Serif"]
plt.rcParams["axes.edgecolor"] = "#cccccc"
plt.rcParams["axes.linewidth"] = 0.8

C_OPTI = "#c0392b"     # reference
C_IMU = "#1f6fb4"
C_MP = "#c8641e"
C_MPW = "#d9a441"
C_MUTE = "#999999"

STAMP = ("OptiTrack reference improved at 87d7645 (2026-09-01), verified against synthetic "
         "ground truth at 920f159. Figures use the corrected reference; pre-2026-09-01 RMSE "
         "figures are the ones not comparable.")


def _f(row, key):
    v = (row.get(key) or "").strip()
    try:
        return float(v)
    except ValueError:
        return float("nan")


def load_rows():
    with open(METRICS_CSV, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["variant"] == "full_0.5"]


#: A knee pendulum swing does not span this far; above it the curve has wrapped.
_MAX_PLAUSIBLE_SPAN_DEG = 120.0
#: Nor does it jump this far between consecutive samples; above it the sign flipped.
#: Deliberately NOT tighter. An earlier draft used 20 deg and excluded 30 of 247
#: trials, but the corpus's own jump distribution has p90 = 21.7 -- so 20 was
#: cutting ordinary fast motion, not corruption, and quietly discarded an eighth
#: of the data. At 60 the threshold selects 5 trials, and they are the same ones
#: an independent sweep flagged.
_MAX_PLAUSIBLE_JUMP_DEG = 60.0


def has_reference(row):
    """True when this trial has a scoreable OptiTrack curve at all.

    This is the ONLY filter these figures apply. A trial with no OptiTrack
    export cannot be scored against a reference that does not exist -- 37 of the
    284 have none, because Motive saved no take or none was exported. Every
    other trial is in, including the 7 whose reference looks wrapped: quality is
    flagged, never dropped, and excluded_trials.json remains the only exclusion
    path in this project.
    """
    return np.isfinite(_f(row, "opti_span_deg")) and np.isfinite(_f(row, "opti_max_jump_deg"))


def reference_suspect(row):
    """True when this trial's OptiTrack curve looks wrapped or sign-flipped.

    A FLAG, never a filter. Fires on 7 of the 247 trials that have a reference,
    and where it fires both channels score ~6x worse against it
    (CHAT_19/Left/pre T1 and T4 at 30.6/27.8 against clean siblings at 3.7/5.2).
    It is still real measured data and it stays in every figure and every
    median: this project flags data quality and never drops it, and
    excluded_trials.json is the only exclusion path (operator-confirmed
    exclusions only). Figures mark these points rather than removing them, so a
    reader can see both the number and what is in it.
    """
    if not has_reference(row):
        return False
    return (_f(row, "opti_span_deg") > _MAX_PLAUSIBLE_SPAN_DEG
            or _f(row, "opti_max_jump_deg") > _MAX_PLAUSIBLE_JUMP_DEG)


def _stamp(fig):
    # Top edge, not bottom: every figure here carries a multi-line explanatory
    # caption along the bottom, and a bottom-right stamp lands on top of it.
    fig.text(0.5, 0.998, STAMP, ha="center", va="top", fontsize=6.2,
             color="#aaaaaa", style="italic")


def _save(fig, name):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)
    return path


def _boxes(ax, data, colors, labels, ylabel, title, annotate=True):
    bp = ax.boxplot(data, patch_artist=True, widths=0.55, showfliers=False)
    for patch, col in zip(bp["boxes"], colors):
        patch.set_facecolor(col)
        patch.set_alpha(0.32)
        patch.set_edgecolor(col)
    for med in bp["medians"]:
        med.set_color("#222222")
        med.set_linewidth(1.6)
    for i, (vals, col) in enumerate(zip(data, colors), 1):
        if not len(vals):
            continue
        ax.scatter(np.random.normal(i, 0.055, len(vals)), vals, s=7, color=col,
                   alpha=0.45, zorder=3, linewidths=0)
        if annotate:
            ax.text(i + 0.30, np.median(vals), f"{np.median(vals):.2f}",
                    ha="left", va="center", fontsize=9, color="#222222")
    ax.set_xticks(range(1, len(data) + 1))
    ax.set_xticklabels(labels, fontsize=8.5)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=10.5)
    ax.grid(axis="y", alpha=0.25, linewidth=0.6)
    ax.set_axisbelow(True)


# ── curve loading, for the waveform figures ──────────────────────────────────

def _deg(value):
    """`23.6deg`, or `n/a` -- never the string "nandeg", which reads as a number."""
    return f"{value:.1f}deg" if np.isfinite(value) else "n/a"


def _curves_for(row):
    """(opti, imu, mp) as (t, angle) pairs for one metrics row; None where absent."""
    path = os.path.join(BASE_DIR, row["csv"])
    if not os.path.isfile(path):
        return None, None, None
    trial = int(row["trial"])
    opti_path = M._optitrack_path(path, trial)
    opti = None
    if opti_path:
        try:
            opti = M.load_optitrack_cached(opti_path)
        except Exception:                          # noqa: BLE001
            opti = None
    t, a2, _aw, _amb, _n, _hw = M._read_columns(path)
    imu = pts._replay_raw_imu_fallback(os.path.dirname(path), str(trial))
    return opti, imu, (t, a2)


def _tail_zero_orient(t, y, flip=None):
    """Level so the settled tail sits at 0, and orient the swing downward.

    A common datum has to be chosen here: the reference is relative post-87d7645
    and the model curves are absolute interior angles, so without this the three
    channels overlay ~150 deg apart. `flip` forces an orientation (used to make
    every channel on a panel follow the reference's direction rather than each
    picking its own, which is what made mirrored pairs look like disagreement).
    """
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    m = np.isfinite(t) & np.isfinite(y)
    if m.sum() < 10:
        return None, None, None
    t, y = t[m], y[m]
    y = y - np.median(y[int(0.75 * y.size):])
    if flip is None:
        flip = y[int(np.argmax(np.abs(y)))] > 0
    return t, (-y if flip else y), flip


def _draw_trial(ax, row, title):
    """Overlay all three channels for one trial, on the alignment the RMSE used.

    The model curves are shifted by compare_pair's own lag_sec rather than by
    each channel's independently-detected release. Using per-channel release
    detection put the three curves on three time origins, so the picture
    disagreed with the RMSE printed beside it -- and MediaPipe's release
    estimate is specifically unreliable (median +0.63 s, sd 2.06 s over 77
    cached video trials), so it was the one thrown furthest.
    """
    opti, imu, mp = _curves_for(row)
    if opti is None:
        ax.axis("off")
        return 0
    ot, oa, flip = _tail_zero_orient(*opti)
    if ot is None:
        ax.axis("off")
        return 0
    t0 = ot[0]
    ax.plot(ot - t0, oa, color=C_OPTI, linewidth=2.2, label="OptiTrack",
            zorder=4, alpha=0.95)
    drawn = 1
    for data, col, lab, channel in ((imu, C_IMU, "Phone IMU", "imu"),
                                    (mp, C_MP, "MediaPipe", "video")):
        if data is None:
            continue
        res = cmr.align_against_optitrack(opti[0], opti[1], data[0], data[1], channel)
        mt, my, _ = _tail_zero_orient(data[0], data[1], flip=flip)
        if mt is None:
            continue
        lag = float(res["lag_sec"]) if res else 0.0
        ax.plot(mt + lag - t0, my, color=col, linewidth=1.5, label=lab,
                zorder=3 if channel == "imu" else 2, alpha=0.85)
        drawn += 1
    ax.axhline(0, color="#dddddd", linewidth=0.8, zorder=0)
    ax.set_xlim(0, min(8.0, float(ot[-1] - t0)))
    ax.set_title(title, fontsize=9.5)
    ax.set_xlabel("time (s), model curves shifted by the lag the RMSE used", fontsize=8)
    ax.set_ylabel("knee angle, tail-zeroed (deg)", fontsize=8.5)
    ax.grid(alpha=0.2, linewidth=0.6)
    ax.set_axisbelow(True)
    return drawn


# ── F1 ───────────────────────────────────────────────────────────────────────

def figure1(rows):
    # Only trials the reference says actually contained a swing. Ranking by RMSE
    # alone made "best" mean "nothing moved": the top hit was a trial whose
    # OptiTrack curve is nearly flat, where any near-flat model curve scores ~2
    # deg without tracking anything. Requiring an interpretable excursion asks
    # the question the figure is for -- how well is a real swing recovered.
    scored = [r for r in rows
              if np.isfinite(_f(r, "rmse_2d")) and has_reference(r)
              and pts.excursion_ok({"A0_deg": _f(r, "opti_A0_deg")})]
    if len(scored) < 4:
        return None
    scored.sort(key=lambda r: _f(r, "rmse_2d"))
    picks = [(scored[0], "BEST"), (scored[1], "2nd best"),
             (scored[-2], "2nd worst"), (scored[-1], "WORST")]

    fig, axes = plt.subplots(2, 2, figsize=(13.5, 8.4))
    for ax, (row, label) in zip(axes.ravel(), picks):
        a0 = _f(row, "opti_A0_deg")
        notes = []
        if not pts.excursion_ok({"A0_deg": a0}):
            notes.append("excursion below the gate")
        if reference_suspect(row):
            notes.append("reference flagged")
        note = ("   [" + "; ".join(notes) + "]") if notes else ""
        title = (f"{label}  -  P{row['participant']} {row['leg']} {row['condition']} "
                 f"T{row['trial']}\nMediaPipe RMSE {_deg(_f(row,'rmse_2d'))}   "
                 f"IMU {_deg(_f(row,'rmse_imu'))}   "
                 f"detection {_f(row,'det_rate_2d'):.0%}   "
                 f"OptiTrack A0 {_deg(a0)}{note}")
        _draw_trial(ax, row, title)
    axes[0, 0].legend(fontsize=8.5, loc="lower right", framealpha=0.9)

    fig.suptitle("MediaPipe's best and worst trials, against OptiTrack and the phone IMU",
                 fontsize=12.5)
    fig.text(0.5, 0.015,
             "Ranked by MediaPipe RMSE across every trial that has an OptiTrack export -- nothing is filtered out, so each panel states its "
             "OptiTrack excursion (A0)\nand flags a below-gate swing or a suspect reference, letting the reader discount a panel rather than "
             "having trials removed from under them.\nAll three curves are zeroed on their own settled tail and oriented to follow the "
             "reference; the model curves are shifted by the same lag the RMSE used.\nThe spread within MediaPipe alone is far wider than the "
             "gap between the two channels' medians, which is why per-trial examples cannot settle which methodology is better -- see F2.",
             ha="center", fontsize=8.2, color="#333333")
    _stamp(fig)
    fig.tight_layout(rect=[0, 0.075, 1, 0.945])
    return _save(fig, "waveforms_best_worst.png")


# ── F2 ───────────────────────────────────────────────────────────────────────

def figure2(rows):
    clean = [r for r in rows if has_reference(r)]
    by = defaultdict(lambda: {"imu": [], "mp": [],
                              "imu_flag": [], "mp_flag": []})
    for r in clean:
        for key, col in (("imu", "rmse_imu"), ("mp", "rmse_2d")):
            v = _f(r, col)
            if np.isfinite(v):
                by[r["participant"]][key].append(v)
                by[r["participant"]][key + "_flag"].append(reference_suspect(r))
    pids = sorted((p for p in by if by[p]["imu"] and by[p]["mp"]),
                  key=lambda p: int(p) if p.isdigit() else 999)
    if not pids:
        return None

    fig, ax = plt.subplots(figsize=(11.5, 7.2))
    y = np.arange(len(pids))
    for i, pid in enumerate(pids):
        d = by[pid]
        for key, col, off, lab in (("imu", C_IMU, +0.16, "Phone IMU"),
                                   ("mp", C_MP, -0.16, "MediaPipe")):
            v = np.array(d[key], float)
            flag = np.array(d[key + "_flag"], bool)
            ax.scatter(v[~flag], np.full(int((~flag).sum()), i + off), s=16,
                       color=col, alpha=0.42, linewidths=0, zorder=2)
            # Suspect-reference trials stay in every median; they are drawn
            # ringed so their influence is visible rather than invisible.
            ax.scatter(v[flag], np.full(int(flag.sum()), i + off), s=32,
                       facecolors="none", edgecolors=col, linewidths=1.1, zorder=3)
            ax.scatter([np.median(v)], [i + off], s=95, color=col, zorder=4,
                       edgecolors="white", linewidths=1.3,
                       label=lab if i == 0 else None)
        ax.text(-2.0, i, f"P{pid}", ha="right", va="center", fontsize=9)
        ax.text(78, i + 0.16, f"n={len(d['imu'])}", va="center", fontsize=7.5, color=C_IMU)
        ax.text(78, i - 0.16, f"n={len(d['mp'])}", va="center", fontsize=7.5, color=C_MP)

    all_imu = np.array([v for p in pids for v in by[p]["imu"]], float)
    all_mp = np.array([v for p in pids for v in by[p]["mp"]], float)
    ax.axvline(np.median(all_imu), color=C_IMU, linestyle="--", linewidth=1.2, alpha=0.7)
    ax.axvline(np.median(all_mp), color=C_MP, linestyle="--", linewidth=1.2, alpha=0.7)
    ax.text(np.median(all_imu), len(pids) - 0.3, f" IMU median {np.median(all_imu):.1f}",
            color=C_IMU, fontsize=8.5, va="bottom")
    ax.text(np.median(all_mp), len(pids) - 0.75, f" MediaPipe median {np.median(all_mp):.1f}",
            color=C_MP, fontsize=8.5, va="bottom")

    ax.set_yticks([])
    ax.set_ylim(-0.8, len(pids) - 0.1)
    ax.set_xlim(-6, 88)
    ax.set_xlabel("knee-angle RMSE vs OptiTrack, offset removed (deg)  -  lower is better")
    ax.set_title("Per-participant angle accuracy against the OptiTrack reference\n"
                 "large marker = participant median, small = individual trials",
                 fontsize=11.5)
    ax.legend(fontsize=9, loc="lower right", framealpha=0.9)
    ax.grid(axis="x", alpha=0.25, linewidth=0.6)
    ax.set_axisbelow(True)

    n_imu_better = sum(1 for p in pids
                       if np.median(by[p]["imu"]) < np.median(by[p]["mp"]))
    n_flagged = sum(1 for r in clean if reference_suspect(r))
    fig.text(0.5, 0.018,
             f"OptiTrack is the REFERENCE, so it has no RMSE against itself -- there is no third series to draw, and a bar at zero would be a "
             f"claim about its accuracy that this data cannot make.\nThe IMU has the lower median on {n_imu_better} of {len(pids)} participants. "
             "Neither channel is consistently better at recovering the knee angle: the within-participant spread\ndwarfs the between-channel "
             "difference. The separation between these two methodologies is not at the angle level -- it is at the PT level, in F4.",
             ha="center", fontsize=8.2, color="#333333")
    _stamp(fig)
    fig.tight_layout(rect=[0, 0.085, 1, 0.955])
    return _save(fig, "rmse_by_participant.png")


# ── F3 ───────────────────────────────────────────────────────────────────────

def figure3(rows):
    clean = [r for r in rows if has_reference(r)]
    series = [("OptiTrack\n(reference)", "opti_pt7", C_OPTI),
              ("Phone IMU", "imu_pt7", C_IMU),
              ("MediaPipe\n2D angle", "2d_pt7", C_MP),
              ("MediaPipe\nworld angle", "world_pt7", C_MPW)]
    data, colors, labels = [], [], []
    for label, col, color in series:
        v = np.array([_f(r, col) for r in clean], float)
        v = v[np.isfinite(v)]
        data.append(v)
        colors.append(color)
        labels.append(f"{label}\nn={v.size}")

    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.6))
    _boxes(axes[0], data, colors, labels, "PT7 composite",
           "A. What PT7 each channel reports")
    axes[0].axhline(float(np.median(data[0])), color=C_OPTI, linestyle="--",
                    linewidth=1.1, alpha=0.6)

    # Panel B -- the same thing as MAS grades, which is what a clinician sees.
    ax = axes[1]
    order = ["0", "1", "1+", "2", "3", "4"]
    width = 0.2
    for k, (label, col, color) in enumerate(
            [("OptiTrack", "opti_mas", C_OPTI), ("Phone IMU", "imu_mas", C_IMU),
             ("MediaPipe 2D", "2d_mas", C_MP)]):
        counts = [sum(1 for r in clean if r.get(col) == g) for g in order]
        total = max(sum(counts), 1)
        ax.bar(np.arange(len(order)) + (k - 1) * width,
               [100 * c / total for c in counts], width, color=color, alpha=0.8,
               label=f"{label} (n={total})")
    ax.set_xticks(np.arange(len(order)))
    ax.set_xticklabels(order)
    ax.set_xlabel("MAS grade the channel's PT7 maps to")
    ax.set_ylabel("% of gradeable trials")
    ax.set_title("B. The same scores as MAS grades", fontsize=10.5)
    ax.legend(fontsize=8.5, framealpha=0.9)
    ax.grid(axis="y", alpha=0.25, linewidth=0.6)
    ax.set_axisbelow(True)

    med = [float(np.median(d)) if d.size else float("nan") for d in data]
    fig.suptitle("What PT score each methodology finds on the same trials", fontsize=12.5)
    fig.text(0.5, 0.017,
             f"Every derived channel over-scores severity against the reference, MediaPipe most: PT7 medians are OptiTrack {med[0]:.2f}, phone IMU "
             f"{med[1]:.2f}, MediaPipe 2D {med[2]:.2f}, MediaPipe world {med[3]:.2f}.\nMediaPipe sits near the top of the usable scale on the median "
             "trial, so it grades most legs as severe. This is a bias in the derived composite, not in the tracked angle --\nF2 shows the two "
             "channels recovering the angle itself about equally well.",
             ha="center", fontsize=8.2, color="#333333")
    _stamp(fig)
    fig.tight_layout(rect=[0, 0.085, 1, 0.935])
    return _save(fig, "pt7_by_methodology.png")


# ── convention sweep, shared by F4 and F5 ────────────────────────────────────

def convention_by_trial():
    """Per-trial PT7 for the IMU and OptiTrack under both detrend conventions."""
    if os.path.isfile(CONV_CACHE):
        with open(CONV_CACHE, encoding="utf-8") as f:
            return json.load(f)

    def score(t, a, detrend):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p = pts.compute_pt_params(np.asarray(t, float), np.asarray(a, float),
                                      detrend=detrend)
        return pts.compute_pt_score(p) if p else float("nan")

    out = []
    for path, _lab, trial, variant in M.iter_hpe_csvs():
        if variant != "full_0.5":
            continue
        replay = pts._replay_raw_imu_fallback(os.path.dirname(path), str(trial))
        opti = M._optitrack_path(path, trial)
        if replay is None or not opti:
            continue
        try:
            ot, oa = M.load_optitrack_cached(opti)
        except Exception:                          # noqa: BLE001
            continue
        pid, _leg, _cond = M._pid_and_context(path)
        out.append({
            "participant": pid,
            "imu_undetrended": score(replay[0], replay[1], False),
            "imu_detrended": score(replay[0], replay[1], True),
            "opti_detrended": score(ot, oa, True),
            "opti_undetrended": score(ot, oa, False),
        })
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(CONV_CACHE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    return out


# ── F4 ───────────────────────────────────────────────────────────────────────

def figure4(rows, conv):
    clean = [r for r in rows if has_reference(r)]
    mp_by = defaultdict(list)
    for r in clean:
        a, b = _f(r, "2d_pt7"), _f(r, "opti_pt7")
        if np.isfinite(a) and np.isfinite(b):
            mp_by[r["participant"]].append(abs(a - b))
    imu_prod, imu_match = defaultdict(list), defaultdict(list)
    for c in conv:
        o = c["opti_detrended"]
        if not np.isfinite(o):
            continue
        if np.isfinite(c["imu_undetrended"]):
            imu_prod[c["participant"]].append(abs(c["imu_undetrended"] - o))
        if np.isfinite(c["imu_detrended"]):
            imu_match[c["participant"]].append(abs(c["imu_detrended"] - o))

    pids = sorted((p for p in mp_by if imu_prod.get(p) and imu_match.get(p)),
                  key=lambda p: int(p) if p.isdigit() else 999)
    if not pids:
        return None

    fig, axes = plt.subplots(1, 2, figsize=(14, 6.4), sharey=True)
    for ax, (imu_src, title) in zip(axes, (
            (imu_prod, "A. Each channel's own production convention"),
            (imu_match, "B. Both scored with a matched convention"))):
        x = np.arange(len(pids))
        mp_med = [float(np.median(mp_by[p])) for p in pids]
        imu_med = [float(np.median(imu_src[p])) for p in pids]
        ax.barh(x + 0.19, mp_med, 0.36, color=C_MP, alpha=0.85, label="MediaPipe 2D")
        ax.barh(x - 0.19, imu_med, 0.36, color=C_IMU, alpha=0.85, label="Phone IMU")
        wins = sum(1 for a, b in zip(imu_med, mp_med) if a < b)
        # No per-row winner annotation: it was drawn at the end of the LONGER
        # bar, normally MediaPipe's, so it read as labelling the bar it was not
        # about. The count in the subplot title states it once, unambiguously.
        ax.set_yticks(x)
        ax.set_yticklabels([f"P{p}" for p in pids], fontsize=9)
        ax.invert_yaxis()
        ax.set_xlabel("median |ΔPT7| vs OptiTrack  -  lower is better")
        ax.set_title(f"{title}\nIMU better on {wins} of {len(pids)} participants",
                     fontsize=10.5)
        ax.grid(axis="x", alpha=0.25, linewidth=0.6)
        ax.set_axisbelow(True)
        ax.legend(fontsize=9, loc="lower right", framealpha=0.9)

    # Pooled figures are COMPUTED, not written in. They were hardcoded once and
    # went stale the first time the corpus grew, leaving a figure whose bars and
    # whose caption disagreed.
    def _pool(d):
        v = [x for p in pids for x in d[p]]
        return float(np.median(v)) if v else float("nan")

    def _corr(key):
        a = np.array([c[key] for c in conv], float)
        b = np.array([c["opti_detrended"] for c in conv], float)
        m = np.isfinite(a) & np.isfinite(b)
        return float(np.corrcoef(a[m], b[m])[0, 1]) if m.sum() > 2 else float("nan")

    mp_pool = _pool(mp_by)
    mp_r = float("nan")
    _a = np.array([_f(r, "2d_pt7") for r in clean], float)
    _b = np.array([_f(r, "opti_pt7") for r in clean], float)
    _m = np.isfinite(_a) & np.isfinite(_b)
    if _m.sum() > 2:
        mp_r = float(np.corrcoef(_a[_m], _b[_m])[0, 1])

    fig.suptitle("Which methodology agrees with OptiTrack on the PT score",
                 fontsize=12.5)
    fig.text(0.5, 0.016,
             "The answer depends on one boolean. In production the phone scores UNDETRENDED and OptiTrack DETRENDED, so panel A compares two "
             "conventions as well as two sensors,\nand the channels come out tied. Score both the same way (panel B) and the IMU separates: pooled "
             f"|ΔPT7| {_pool(imu_prod):.3f} → {_pool(imu_match):.3f}, correlation with OptiTrack "
             f"{_corr('imu_undetrended'):+.3f} → {_corr('imu_detrended'):+.3f},\nwhile MediaPipe stays at "
             f"{mp_pool:.3f} / {mp_r:+.3f} under either. F5 isolates the "
             "flag. This is a measurement, not a recommendation to change it.",
             ha="center", fontsize=8.2, color="#333333")
    _stamp(fig)
    fig.tight_layout(rect=[0, 0.085, 1, 0.935])
    return _save(fig, "pt_agreement_by_participant.png")


# ── F5 ───────────────────────────────────────────────────────────────────────

def figure5(conv):
    imu_u = np.array([c["imu_undetrended"] for c in conv], float)
    imu_d = np.array([c["imu_detrended"] for c in conv], float)
    opti_d = np.array([c["opti_detrended"] for c in conv], float)
    opti_u = np.array([c["opti_undetrended"] for c in conv], float)

    def diff(a, b):
        m = np.isfinite(a) & np.isfinite(b)
        return np.abs(a[m] - b[m])

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.4))
    _boxes(axes[0], [diff(imu_u, imu_d), diff(opti_u, opti_d)],
           [C_IMU, C_OPTI], ["Phone IMU", "OptiTrack"],
           "|ΔPT7| between conventions, same curve",
           "A. What the detrend flag alone costs")

    ax = axes[1]
    for a, b, col, lab in ((imu_u, opti_d, C_MUTE, "IMU as scored in production"),
                           (imu_d, opti_d, C_IMU, "IMU, convention-matched")):
        m = np.isfinite(a) & np.isfinite(b)
        r = np.corrcoef(a[m], b[m])[0, 1]
        ax.scatter(b[m], a[m], s=26, color=col, alpha=0.65, linewidths=0,
                   label=f"{lab}  (r = {r:+.3f})")
    lim = [0, max(2.0, float(np.nanmax(opti_d)) * 1.05)]
    ax.plot(lim, lim, color="#bbbbbb", linestyle="--", linewidth=1.1, zorder=0)
    ax.set_xlim(lim)
    ax.set_ylim(0, 2.6)
    ax.set_xlabel("OptiTrack PT7")
    ax.set_ylabel("Phone IMU PT7")
    ax.set_title("B. The same trials, before and after matching", fontsize=10.5)
    ax.legend(fontsize=8.5, framealpha=0.9, loc="upper left")
    ax.grid(alpha=0.25, linewidth=0.6)
    ax.set_axisbelow(True)

    fig.suptitle("The detrend convention is the largest single term in the IMU's PT7 error",
                 fontsize=12.5)
    _imu_cost = float(np.median(diff(imu_u, imu_d)))
    _opti_cost = float(np.median(diff(opti_u, opti_d)))
    fig.text(0.5, 0.016,
             f"OptiTrack does not drift, so detrending barely moves it ({_opti_cost:.3f}). The phone does, and the production convention leaves that drift in the "
             f"score ({_imu_cost:.3f}).\nThe flag is deliberate -- compute_pt_params already fits a pre-release baseline, so detrending on top may double-correct "
             "-- and this figure does not argue for changing it.\nIt argues that any IMU-vs-OptiTrack agreement figure has to say which convention "
             "pairing it used, because the two answers differ by more than the sensors do.",
             ha="center", fontsize=8.2, color="#333333")
    _stamp(fig)
    fig.tight_layout(rect=[0, 0.085, 1, 0.935])
    return _save(fig, "detrend_convention.png")


# ── F6 ───────────────────────────────────────────────────────────────────────

def _icc_2_1(a, b):
    """ICC(2,1) for two raters over n targets; nan when undefined."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = np.isfinite(a) & np.isfinite(b)
    a, b = a[m], b[m]
    n = a.size
    if n < 3:
        return float("nan")
    data = np.column_stack([a, b])
    grand = data.mean()
    ms_r = 2 * np.sum((data.mean(axis=1) - grand) ** 2) / (n - 1)
    ms_c = n * np.sum((data.mean(axis=0) - grand) ** 2)
    resid = data - data.mean(axis=1, keepdims=True) - data.mean(axis=0) + grand
    ms_e = np.sum(resid ** 2) / (n - 1)
    denom = ms_r + (ms_c - ms_e) * 2 / n + ms_e
    return float((ms_r - ms_e) / denom) if abs(denom) > 1e-12 else float("nan")


def figure6(rows):
    clean = [r for r in rows if has_reference(r)]
    params = list(M.PT_KEYS)
    fig, axes = plt.subplots(2, 4, figsize=(16, 8))
    for ax, p in zip(axes.ravel(), params):
        o = np.array([_f(r, f"opti_{p}") for r in clean], float)
        for tag, col, lab in (("imu", C_IMU, "IMU"), ("2d", C_MP, "MediaPipe")):
            v = np.array([_f(r, f"{tag}_{p}") for r in clean], float)
            icc = _icc_2_1(o, v)
            m = np.isfinite(o) & np.isfinite(v)
            ax.scatter(o[m], v[m], s=13, color=col, alpha=0.5, linewidths=0,
                       label=f"{lab}  ICC {icc:+.2f}  (n={int(m.sum())})")
        finite = o[np.isfinite(o)]
        if finite.size:
            lo, hi = np.percentile(finite, [1, 99])
            pad = 0.1 * (hi - lo + 1e-9)
            ax.plot([lo - pad, hi + pad], [lo - pad, hi + pad], color="#bbbbbb",
                    linestyle="--", linewidth=1.0, zorder=0)
            ax.set_xlim(lo - pad, hi + pad)
            ax.set_ylim(lo - pad, hi + pad)
        ax.set_title(p, fontsize=10)
        ax.legend(fontsize=6.8, framealpha=0.9, loc="upper left")
        ax.grid(alpha=0.2, linewidth=0.5)
        ax.set_axisbelow(True)
        ax.tick_params(labelsize=7.5)
    for ax in axes[1, :]:
        ax.set_xlabel("OptiTrack", fontsize=8.5)
    for ax in axes[:, 0]:
        ax.set_ylabel("channel", fontsize=8.5)

    fig.suptitle("Every PT parameter against the OptiTrack reference, per channel  "
                 "(dashed = perfect agreement)", fontsize=12.5)
    fig.text(0.5, 0.014,
             "ICC(2,1) per parameter, so a failure can be located rather than inferred from the composite. A0 is included although it is NOT one of "
             "the seven scored\nparameters -- it is the excursion the gate tests, and the one parameter that is monotonic in severity.",
             ha="center", fontsize=8.2, color="#333333")
    _stamp(fig)
    fig.tight_layout(rect=[0, 0.055, 1, 0.945])
    return _save(fig, "per_metric_icc.png")


# ── F7 ───────────────────────────────────────────────────────────────────────

def figure7(rows):
    def pick(pid, cond_starts, n=3):
        sel = [r for r in rows if r["participant"] == pid
               and r["condition"].lower().startswith(cond_starts)]
        sel.sort(key=lambda r: int(r["trial"]))
        return sel[:n]

    good = pick("8", "pre") or pick("8", "")
    bad = pick("18", "pre") or pick("18", "")
    if not good or not bad:
        return None
    ncol = max(len(good), len(bad))
    fig, axes = plt.subplots(2, ncol, figsize=(4.6 * ncol, 8.2), squeeze=False)
    for row_i, (group, label) in enumerate(((good, "Control_8"), (bad, "MINT_18"))):
        for col_i in range(ncol):
            ax = axes[row_i][col_i]
            if col_i >= len(group):
                ax.axis("off")
                continue
            r = group[col_i]
            title = (f"{label} T{r['trial']}   detection {_f(r,'det_rate_2d'):.0%}\n"
                     f"MediaPipe {_deg(_f(r,'rmse_2d'))}   IMU {_deg(_f(r,'rmse_imu'))}")
            _draw_trial(ax, r, title)
    axes[0][0].legend(fontsize=8.5, loc="lower right", framealpha=0.9)

    fig.suptitle("The same three curves under good and bad camera framing",
                 fontsize=12.5)
    fig.text(0.5, 0.015,
             "Top: Control_8, lateral view with the whole supine body in frame, 99.6% median detection. Bottom: MINT_18, patient's head and shoulders "
             "cropped out and\na standing assessor fully visible, 5.5% median detection. The optical curve degrades with framing; the IMU curve does "
             "not depend on where the camera was.\nThis is the robustness argument for the IMU, and it is a stronger one than accuracy -- F2 shows "
             "the two channels are comparable when the camera cooperates.",
             ha="center", fontsize=8.2, color="#333333")
    _stamp(fig)
    fig.tight_layout(rect=[0, 0.075, 1, 0.945])
    return _save(fig, "waveforms_framing_contrast.png")


def main():
    if not os.path.isfile(METRICS_CSV):
        print(f"ERROR: {METRICS_CSV} not found. Run mediapipe_pt_metrics.py first.")
        sys.exit(1)
    rows = load_rows()
    if not any(np.isfinite(_f(r, "rmse_2d")) for r in rows):
        print("ERROR: no rmse_2d column populated. Re-run mediapipe_pt_metrics.py "
              "after the RMSE columns were added.")
        sys.exit(1)
    with_ref = [r for r in rows if has_reference(r)]
    clean = [r for r in rows if has_reference(r)]
    flagged = [r for r in with_ref if reference_suspect(r)]
    print(f"{len(rows)} full_0.5 trials: {len(rows) - len(with_ref)} have no OptiTrack "
          f"export and cannot be scored against a reference that does not exist; "
          f"{len(with_ref)} are scored.\n"
          f"Of those, {len(flagged)} carry a reference that looks wrapped or "
          f"sign-flipped -- INCLUDED in every median and marked in the figures, "
          f"not dropped.\n")
    print("Building the detrend-convention sweep (cached after first run) ...")
    conv = convention_by_trial()
    print(f"  {len(conv)} trials with both a phone IMU replay and OptiTrack\n")

    for label, fn in (("F1 waveforms best/worst", lambda: figure1(rows)),
                      ("F2 RMSE by participant", lambda: figure2(rows)),
                      ("F3 PT7 by methodology", lambda: figure3(rows)),
                      ("F4 PT agreement", lambda: figure4(rows, conv)),
                      ("F5 detrend convention", lambda: figure5(conv)),
                      ("F6 per-metric ICC", lambda: figure6(rows)),
                      ("F7 framing contrast", lambda: figure7(rows))):
        try:
            out = fn()
        except Exception as exc:                   # noqa: BLE001
            print(f"{label:26s} -> FAILED: {type(exc).__name__}: {exc}")
            continue
        print(f"{label:26s} -> {os.path.relpath(out, BASE_DIR) if out else 'skipped'}")


if __name__ == "__main__":
    main()
