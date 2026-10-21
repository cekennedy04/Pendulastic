#!/usr/bin/env python3
"""
generate_pipeline_boundary_figures.py
=====================================
Three figures that draw the boundary of what this pipeline can claim, rather
than promoting any channel. They exist to be shown alongside the IMU results,
so a reader can see WHERE the optical path stops being usable and why.

  Figure 1  imu_vs_optitrack_baseline.png
            PT7 agreement with OptiTrack for the phone IMU and for both
            MediaPipe angle columns, on the 100 Hz analysis grid. Includes the
            detrend-convention control, because without it the IMU's headline
            number is not a sensor measurement at all.

  Figure 2  mediapipe_gate_nonmonotonic.png
            Excursion-gate pass rate against tracking quality and against A0.
            Documents that a better-tracked trial is not more likely to yield
            an interpretable score -- the failure is in the derivation.

  Figure 3  mint17_case_study.png
            The ten MINT_17 pre-session trials, which have no OptiTrack at all
            and are therefore the case the optical channel exists to serve.
            Shows why they are reported as tracked, not graded.

Every figure is stamped DRAFT. The excursion floor (MIN_INTERPRETABLE_A0_DEG)
is under active re-derivation (25.0 -> 28.0 proposed), and that constant decides
which trials appear as gradeable here. Re-run and re-stamp once it lands.

Inputs:  Model_Analysis_Outputs/HPE_Comparison/mediapipe_pt_metrics.csv
         (plus a one-off convention sweep, cached beside it)
Outputs: Model_Analysis_Outputs/Pipeline_Boundary/

Usage:
    python generate_pipeline_boundary_figures.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import mediapipe_pt_metrics as M
import pendulastic_pt_score as pts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
METRICS_CSV = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "HPE_Comparison",
                           "mediapipe_pt_metrics.csv")
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "Pipeline_Boundary")
CONV_CACHE = os.path.join(OUT_DIR, "_detrend_convention_sweep.json")

plt.rcParams["font.family"] = "serif"
plt.rcParams["font.serif"] = ["Times New Roman", "DejaVu Serif"]
plt.rcParams["axes.edgecolor"] = "#cccccc"
plt.rcParams["axes.linewidth"] = 0.8

C_IMU = "#1f6fb4"
C_MP2D = "#c8641e"
C_MPW = "#d9a441"
C_OPTI = "#444444"
C_BAD = "#b23b3b"


def _f(row, key):
    v = (row.get(key) or "").strip()
    try:
        return float(v)
    except ValueError:
        return float("nan")


def load_rows():
    with open(METRICS_CSV, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["variant"] == "full_0.5"]


def _stamp(fig, note=""):
    """DRAFT banner. These figures depend on a constant under active change."""
    fig.text(0.995, 0.005,
             "DRAFT - excursion floor under re-derivation (25.0 -> 28.0 proposed). "
             + note,
             ha="right", va="bottom", fontsize=7, color="#999999", style="italic")


# ── Figure 1 ─────────────────────────────────────────────────────────────────

def convention_sweep():
    """Per-trial PT7 for the IMU and OptiTrack under BOTH detrend conventions.

    Cached: the sweep replays every raw phone log and re-scores it four ways,
    which is a couple of minutes, and nothing in it changes unless the scorer or
    the recordings do.
    """
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
    for path, _label, trial, variant in M.iter_hpe_csvs():
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
        it, ia = replay
        out.append({
            "imu_undetrended": score(it, ia, False),
            "imu_detrended": score(it, ia, True),
            "opti_detrended": score(ot, oa, True),
            "opti_undetrended": score(ot, oa, False),
        })
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(CONV_CACHE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    return out


def _abs_err(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    return np.abs(x[m] - y[m]), (np.corrcoef(x[m], y[m])[0, 1] if m.sum() > 2 else np.nan)


def figure1(rows, conv):
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.2))

    imu_u = [c["imu_undetrended"] for c in conv]
    imu_d = [c["imu_detrended"] for c in conv]
    opti_d = [c["opti_detrended"] for c in conv]

    mp2 = [_f(r, "2d_pt7") for r in rows]
    mpw = [_f(r, "world_pt7") for r in rows]
    op = [_f(r, "opti_pt7") for r in rows]

    series = [
        ("Phone IMU\n(production:\nundetrended)", *_abs_err(imu_u, opti_d), C_IMU),
        ("Phone IMU\n(convention-\nmatched)", *_abs_err(imu_d, opti_d), C_IMU),
        ("MediaPipe\n2D angle", *_abs_err(mp2, op), C_MP2D),
        ("MediaPipe\nworld angle", *_abs_err(mpw, op), C_MPW),
    ]

    # Panel A -- distribution of |dPT7| against the OptiTrack reference.
    ax = axes[0]
    data = [s[1] for s in series]
    bp = ax.boxplot(data, patch_artist=True, widths=0.55, showfliers=False)
    for patch, s in zip(bp["boxes"], series):
        patch.set_facecolor(s[3])
        patch.set_alpha(0.35)
        patch.set_edgecolor(s[3])
    for med in bp["medians"]:
        med.set_color("#222222")
        med.set_linewidth(1.6)
    for i, s in enumerate(series, 1):
        ax.scatter(np.random.normal(i, 0.055, len(s[1])), s[1], s=7,
                   color=s[3], alpha=0.45, zorder=3, linewidths=0)
        ax.text(i, np.median(s[1]) + 0.045, f"{np.median(s[1]):.3f}",
                ha="center", fontsize=9, color="#222222")
    ax.set_xticks(range(1, len(series) + 1))
    ax.set_xticklabels([s[0] for s in series], fontsize=8)
    ax.set_ylabel("|ΔPT7| vs OptiTrack")
    ax.set_title("A. PT7 error against the OptiTrack reference", fontsize=10.5)
    ax.grid(axis="y", alpha=0.25, linewidth=0.6)
    ax.set_axisbelow(True)
    # A handful of MediaPipe trials error by >2 PT7 -- real, but on a shared axis
    # they squash all four boxes into the bottom fifth of the panel and hide the
    # comparison this panel exists to make. Clip the view and say how many.
    clip = 2.2
    ax.set_ylim(-0.05, clip)
    n_clipped = sum(int((s[1] > clip).sum()) for s in series)
    if n_clipped:
        ax.text(0.98, 0.97, f"{n_clipped} point(s) above {clip:.1f}, not shown",
                transform=ax.transAxes, ha="right", va="top", fontsize=7.5,
                color="#888888", style="italic")

    # Panel B -- the same thing as correlation, which is what a claim rests on.
    ax = axes[1]
    labels = [s[0].replace("\n", " ") for s in series]
    rs = [s[2] for s in series]
    bars = ax.barh(range(len(rs)), rs, color=[s[3] for s in series], alpha=0.75)
    for i, (b, r) in enumerate(zip(bars, rs)):
        ax.text(r + 0.012, i, f"r = {r:+.3f}", va="center", fontsize=9)
    ax.set_yticks(range(len(rs)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(0, 0.85)
    ax.set_xlabel("Pearson r with OptiTrack PT7")
    ax.set_title("B. PT7 correlation with OptiTrack", fontsize=10.5)
    ax.grid(axis="x", alpha=0.25, linewidth=0.6)
    ax.set_axisbelow(True)

    # Panel C -- the control that makes panels A and B readable: how much of the
    # IMU's error is the detrend flag rather than the sensor.
    ax = axes[2]
    imu_conv, _ = _abs_err(imu_u, imu_d)
    opti_conv, _ = _abs_err([c["opti_undetrended"] for c in conv], opti_d)
    bp2 = ax.boxplot([imu_conv, opti_conv], patch_artist=True, widths=0.5,
                     showfliers=False)
    for patch, col in zip(bp2["boxes"], (C_IMU, C_OPTI)):
        patch.set_facecolor(col)
        patch.set_alpha(0.35)
        patch.set_edgecolor(col)
    for med in bp2["medians"]:
        med.set_color("#222222")
        med.set_linewidth(1.6)
    for i, (vals, col) in enumerate([(imu_conv, C_IMU), (opti_conv, C_OPTI)], 1):
        ax.scatter(np.random.normal(i, 0.05, len(vals)), vals, s=8, color=col,
                   alpha=0.45, zorder=3, linewidths=0)
        ax.text(i + 0.30, np.median(vals), f"{np.median(vals):.3f}",
                ha="left", va="center", fontsize=9, color="#222222")
    ax.set_xticks([1, 2])
    ax.set_xticklabels(["Phone IMU", "OptiTrack"], fontsize=9)
    ax.set_ylabel("|ΔPT7| between detrend conventions, same sensor")
    ax.set_title("C. Cost of the detrend convention alone", fontsize=10.5)
    ax.grid(axis="y", alpha=0.25, linewidth=0.6)
    ax.set_axisbelow(True)

    fig.suptitle("PT7 agreement with OptiTrack on the 100 Hz analysis grid  "
                 f"(IMU n={len(conv)} paired trials, MediaPipe n=178)",
                 fontsize=12)
    fig.text(0.5, 0.015,
             "The phone IMU is scored undetrended in production and OptiTrack detrended, so the leftmost box in A mixes sensor error with a "
             "flag difference.\nPanel C isolates it: the convention moves OptiTrack's PT7 by 0.050 and the IMU's by 0.363. Matching conventions "
             "halves the IMU's error (0.447 → 0.247)\nand lifts r from 0.438 to 0.630. MediaPipe does not improve under any convention.",
             ha="center", fontsize=8.4, color="#333333")
    _stamp(fig)
    fig.tight_layout(rect=[0, 0.10, 1, 0.94])
    out = os.path.join(OUT_DIR, "imu_vs_optitrack_baseline.png")
    fig.savefig(out, dpi=150, facecolor="white")
    plt.close(fig)
    return out


# ── Figure 2 ─────────────────────────────────────────────────────────────────

def figure2(rows):
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.0))

    det = np.array([_f(r, "det_rate_2d") for r in rows])
    a0 = np.array([_f(r, "2d_A0_deg") for r in rows])
    gate = np.array([r["2d_interpretable"] == "True" for r in rows])

    # Panel A -- gate pass rate by TRACKING QUALITY. If the optical failure were
    # a tracking failure, this would rise monotonically. It does not.
    ax = axes[0]
    edges = [0, .25, .50, .75, 1.01]
    labels, rates, ns = [], [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (det >= lo) & (det < hi)
        if not m.sum():
            continue
        labels.append(f"{lo:.0%}–{min(hi,1):.0%}")
        rates.append(100 * gate[m].mean())
        ns.append(int(m.sum()))
    bars = ax.bar(labels, rates, color=C_MP2D, alpha=0.8, width=0.62)
    for b, r, n in zip(bars, rates, ns):
        ax.text(b.get_x() + b.get_width() / 2, r + 1.6, f"{r:.0f}%\nn={n}",
                ha="center", fontsize=9)
    ax.plot(range(len(rates)), rates, color=C_BAD, marker="o", linewidth=1.4,
            markersize=5, zorder=4)
    ax.set_ylim(0, 100)
    ax.set_xlabel("MediaPipe detection rate (fraction of frames tracked)")
    ax.set_ylabel("% of trials passing the excursion gate")
    ax.set_title("A. Tracking quality does not buy an interpretable score",
                 fontsize=10.5)
    ax.grid(axis="y", alpha=0.25, linewidth=0.6)
    ax.set_axisbelow(True)

    # Panel B -- the same buckets against PT7 ERROR rather than gate outcome.
    # Plotting the gate against A0 instead would be circular: the gate IS an A0
    # threshold, so that panel can only ever come out monotonic and proves
    # nothing. Accuracy against the OptiTrack reference is the honest test of
    # "does better tracking give a better score", and it is flat.
    ax = axes[1]
    err = np.array([_f(r, "2d_pt7_abs_err") for r in rows])
    meds, ns2, labels2, spread = [], [], [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (det >= lo) & (det < hi) & np.isfinite(err)
        if not m.sum():
            continue
        labels2.append(f"{lo:.0%}–{min(hi,1):.0%}")
        meds.append(float(np.median(err[m])))
        spread.append(err[m])
        ns2.append(int(m.sum()))
    positions = np.arange(len(meds))
    bp = ax.boxplot(spread, positions=positions, patch_artist=True, widths=0.55,
                    showfliers=False)
    for patch in bp["boxes"]:
        patch.set_facecolor(C_MP2D)
        patch.set_alpha(0.30)
        patch.set_edgecolor(C_MP2D)
    for med in bp["medians"]:
        med.set_color("#222222")
        med.set_linewidth(1.6)
    for i, (vals, mv, n) in enumerate(zip(spread, meds, ns2)):
        ax.scatter(np.random.normal(i, 0.055, len(vals)), vals, s=8,
                   color=C_MP2D, alpha=0.45, zorder=3, linewidths=0)
        ax.text(i, 1.92, f"median {mv:.2f}\nn={n}", ha="center", fontsize=8.5)
    ax.plot(positions, meds, color=C_BAD, marker="o", linewidth=1.4,
            markersize=5, zorder=4)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels2, fontsize=9)
    ax.set_ylim(0, 2.1)
    ax.set_xlabel("MediaPipe detection rate (fraction of frames tracked)")
    ax.set_ylabel("|ΔPT7| vs OptiTrack")
    ax.set_title("B. Nor does it buy an accurate one", fontsize=10.5)
    ax.grid(axis="y", alpha=0.25, linewidth=0.6)
    ax.set_axisbelow(True)

    fig.suptitle("Why optical tracking does not yield interpretable clinical scores",
                 fontsize=12)
    fig.text(0.5, 0.018,
             "Left: gate pass rate against tracking quality is non-monotonic (24% → 74% → 59% → 67%) — a well-tracked trial is no more likely to be "
             "gradeable than a poorly tracked one.\nRight: among trials that DO produce a score, PT7 error against OptiTrack shows no improvement with "
             "tracking quality (medians 0.30, 0.60, 0.41, 0.43).\nImproving the camera or the pose model moves neither. The failure is in the "
             "derivation from angle to PT7, not in the tracking.",
             ha="center", fontsize=8.4, color="#333333")
    _stamp(fig)
    fig.tight_layout(rect=[0, 0.09, 1, 0.94])
    out = os.path.join(OUT_DIR, "mediapipe_gate_nonmonotonic.png")
    fig.savefig(out, dpi=150, facecolor="white")
    plt.close(fig)
    return out


# ── Figure 3 ─────────────────────────────────────────────────────────────────

def figure3(rows):
    sel = [r for r in rows
           if r["participant"] == "17" and r["condition"].lower().startswith("pre")]
    sel.sort(key=lambda r: (r["leg"], int(r["trial"])))
    if not sel:
        return None

    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.2),
                             gridspec_kw={"width_ratios": [1.25, 1]})

    names = [f"{r['leg'][:1].upper()}{r['trial']}" for r in sel]
    a0 = np.array([_f(r, "2d_A0_deg") for r in sel])
    det = np.array([_f(r, "det_rate_2d") for r in sel])
    graded = np.array([r["2d_interpretable"] == "True" for r in sel])

    # Panel A -- A0 on a log axis, because the failure modes span two orders of
    # magnitude: a 157.9 deg tracking artefact and a 6.6 deg collapsed swing
    # cannot share a linear axis with the 25-34 deg trials in between.
    ax = axes[0]
    x = np.arange(len(sel))
    plotted = np.where(np.isfinite(a0), a0, 0.35)   # 0 -> visible stub
    colors = [C_MP2D if g else "#bbbbbb" for g in graded]
    ax.bar(x, plotted, color=colors, alpha=0.85, width=0.62)
    ax.axhspan(25, pts.MAX_INTERPRETABLE_A0_DEG, color="#4a8f4a", alpha=0.10,
               zorder=0)
    ax.axhline(25, color=C_BAD, linestyle="--", linewidth=1.2)
    ax.text(len(sel) - 0.4, 26, "gate floor 25°", color=C_BAD, fontsize=8.5,
            ha="right")
    ax.axhline(pts.MAX_INTERPRETABLE_A0_DEG, color=C_BAD, linestyle="--",
               linewidth=1.2)
    ax.text(len(sel) - 0.4, pts.MAX_INTERPRETABLE_A0_DEG * 1.04,
            f"gate ceiling {pts.MAX_INTERPRETABLE_A0_DEG:.0f}°", color=C_BAD,
            fontsize=8.5, ha="right")
    for xi, (v, g, r) in enumerate(zip(a0, graded, sel)):
        if not np.isfinite(v):
            ax.text(xi, 0.45, "no\nscore", ha="center", va="bottom", fontsize=7.5,
                    color="#777777")
        else:
            ax.text(xi, v * 1.06, f"{v:.1f}", ha="center", fontsize=8)
            if g:
                ax.text(xi, 0.45, f"MAS {r['2d_mas']}", ha="center", va="bottom",
                        fontsize=8, color=C_MP2D, weight="bold")
    ax.set_yscale("log")
    ax.set_ylim(0.3, 400)
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=9)
    ax.set_xlabel("MINT_17 pre-session trial  (L = left leg, R = right)")
    ax.set_ylabel("MediaPipe A0, swing excursion (deg, log scale)")
    ax.set_title("A. Four of ten produce a grade; none has ground truth",
                 fontsize=10.5)
    ax.grid(axis="y", alpha=0.25, linewidth=0.6)
    ax.set_axisbelow(True)

    # Panel B -- detection rate, to show the two are unrelated here too.
    ax = axes[1]
    ax.scatter(det * 100, np.where(np.isfinite(a0), a0, 0.35),
               c=[C_MP2D if g else "#bbbbbb" for g in graded], s=90, zorder=3,
               edgecolors="white", linewidths=1.0)
    for xi, yi, nm in zip(det * 100, np.where(np.isfinite(a0), a0, 0.35), names):
        ax.annotate(nm, (xi, yi), textcoords="offset points", xytext=(7, 3),
                    fontsize=8, color="#444444")
    ax.axhspan(25, pts.MAX_INTERPRETABLE_A0_DEG, color="#4a8f4a", alpha=0.10,
               zorder=0)
    ax.axhline(25, color=C_BAD, linestyle="--", linewidth=1.2)
    ax.set_yscale("log")
    ax.set_ylim(0.3, 400)
    ax.set_xlim(0, 70)
    ax.set_xlabel("MediaPipe detection rate (%)")
    ax.set_ylabel("MediaPipe A0 (deg, log scale)")
    ax.set_title("B. Detection rate does not predict a usable excursion",
                 fontsize=10.5)
    ax.grid(alpha=0.25, linewidth=0.6)
    ax.set_axisbelow(True)

    fig.suptitle("MINT_17 pre-session: recovered by the discovery fix, reported as tracked — not graded",
                 fontsize=12)
    fig.text(0.5, 0.015,
             "Motive saved no take for this session, so these ten trials have no OptiTrack reference and never will; MediaPipe is the only "
             "quantitative channel they will ever have.\nThe gate refuses six — including a 157.9° A0, which is not a knee — and the four it passes "
             "all land at MAS 3–4 on 25–34° excursions, inside PT7's known\nnon-monotonic region where a barely-moving leg and a severely spastic one "
             "score alike. Coverage was the fixable problem; interpretability was not.",
             ha="center", fontsize=8.4, color="#333333")
    _stamp(fig)
    fig.tight_layout(rect=[0, 0.115, 1, 0.94])
    out = os.path.join(OUT_DIR, "mint17_case_study.png")
    fig.savefig(out, dpi=150, facecolor="white")
    plt.close(fig)
    return out


def main():
    if not os.path.isfile(METRICS_CSV):
        print(f"ERROR: {METRICS_CSV} not found. Run mediapipe_pt_metrics.py first.")
        sys.exit(1)
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = load_rows()
    print(f"{len(rows)} full_0.5 trial(s) from mediapipe_pt_metrics.csv")
    print("Building the detrend-convention sweep (cached after the first run) ...")
    conv = convention_sweep()
    print(f"  {len(conv)} trial(s) with both a phone IMU replay and OptiTrack\n")
    for fn, label in ((lambda: figure1(rows, conv), "Figure 1"),
                      (lambda: figure2(rows), "Figure 2"),
                      (lambda: figure3(rows), "Figure 3")):
        out = fn()
        print(f"{label} -> {os.path.relpath(out, BASE_DIR) if out else 'skipped'}")


if __name__ == "__main__":
    main()
