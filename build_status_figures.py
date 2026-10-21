"""Figures for the 'where we are' section of the progress deck.

Generated fresh rather than reused: everything under Model_Analysis_Outputs/
Figures/ predates the 2026-08-28 drift fix and the 2026-09-01 OptiTrack
coverage fix, and fig20_data_availability.png carries a subtitle ("only 5 of
22 participants have it") that contradicts its own contents and the verified
count of 13.

Palette and typeface match the deck so the figures sit on the slide without
looking imported.
"""
from __future__ import annotations

import glob
import os
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
OUT = HERE / "Model_Analysis_Outputs" / "status_figures"
OUT.mkdir(parents=True, exist_ok=True)

# deck palette
BG = "#F0F5FA"
NAVY = "#0C1E34"
SLATE = "#3A5870"
BAR = "#47566E"
TRACK = "#E6E9EC"
MICRO = "#6B7686"
BLUE = "#2a78d6"    # reference / OptiTrack -- project convention
ORANGE = "#eb6834"  # test modality / IMU   -- project convention
GOOD = "#2F6D3B"
WARN = "#9C6410"
BAD = "#A33327"

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "figure.facecolor": BG,
    "axes.facecolor": BG,
    "savefig.facecolor": BG,
    "axes.edgecolor": "#C9D4D1",
    "axes.labelcolor": NAVY,
    "text.color": NAVY,
    "xtick.color": SLATE,
    "ytick.color": SLATE,
    "axes.titlesize": 13,
    "axes.labelsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "axes.grid": True,
    "grid.color": "#D8E0E6",
    "grid.linewidth": 0.7,
})


def save(fig, name):
    p = OUT / name
    fig.savefig(p, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {name}")
    return p


# ── 1. what has been collected ────────────────────────────────────────────────
def fig_cohort():
    man = pd.read_csv(HERE / "Model_Analysis_Outputs" / "data_purpose_manifest.csv")
    mas = pd.read_csv(HERE / "mas_scores.csv")
    examined = set(mas.loc[mas.assessed_by.notna()
                           & (mas.assessed_by != "ASSUMED"), "participant"])

    man = man.sort_values("participant")
    pids = man.participant.tolist()
    cols = ["Video", "OptiTrack", "IMU", "Clinician MAS"]
    grid = np.zeros((len(pids), 4))
    for i, r in enumerate(man.itertuples()):
        grid[i, 0] = 1
        grid[i, 1] = 1 if r.has_optitrack_csv == "yes" else 0
        grid[i, 2] = 1 if r.has_imu == "yes" else 0
        grid[i, 3] = 1 if r.participant in examined else 0

    fig, ax = plt.subplots(figsize=(6.0, 6.6))
    ax.imshow(grid, cmap=matplotlib.colors.ListedColormap([TRACK, ORANGE]),
              aspect="auto", vmin=0, vmax=1)
    ax.set_xticks(range(4))
    ax.set_xticklabels(cols, fontsize=11)
    ax.set_yticks(range(len(pids)))
    ax.set_yticklabels([f"P{p}" for p in pids], fontsize=9)
    ax.set_xticks(np.arange(-.5, 4, 1), minor=True)
    ax.set_yticks(np.arange(-.5, len(pids), 1), minor=True)
    ax.grid(which="minor", color=BG, linewidth=2)
    ax.grid(which="major", visible=False)
    ax.tick_params(which="minor", length=0)
    totals = grid.sum(axis=0).astype(int)
    ax.set_title(
        f"Data collected, by participant (n = {len(pids)})\n"
        f"Video {totals[0]}   ·   OptiTrack {totals[1]}   ·   "
        f"IMU {totals[2]}   ·   clinician MAS {totals[3]}",
        fontsize=12, color=NAVY, pad=12)
    return save(fig, "wa_cohort.png")


# ── 2. what a captured trial looks like ───────────────────────────────────────
def fig_trajectory():
    f = HERE / "Recordings" / "MINT_18" / "Right" / "pre" / "Trial_4_imu.csv"
    d = pd.read_csv(f, comment="#")
    t = d.phone_ts_ms.to_numpy(float) / 1000.0
    t -= t[0]
    a = d.prox_pitch.to_numpy(float)

    rest = np.median(a[int(0.9 * len(a)):])
    a = a - rest                       # zero on the settled resting angle
    rel = int(np.argmax(np.abs(np.gradient(a, t)) > 20))
    t = t - t[rel]
    keep = (t > -1.0) & (t < 11.0)
    t, a = t[keep], a[keep]

    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    ax.axhline(0, color=MICRO, lw=0.9, ls="--", zorder=1)
    ax.plot(t, a, color=ORANGE, lw=1.9, zorder=3)
    a0 = a[np.argmax(np.abs(a))]
    ax.annotate(f"A₀ = {abs(a0):.0f}°",
                xy=(t[np.argmax(np.abs(a))], a0),
                xytext=(1.6, a0 * 0.92), fontsize=11, color=NAVY,
                arrowprops=dict(arrowstyle="->", color=SLATE, lw=1.1))
    ax.annotate("resting angle", xy=(9.4, 0), xytext=(7.4, a0 * 0.30),
                fontsize=10, color=SLATE,
                arrowprops=dict(arrowstyle="->", color=SLATE, lw=1.0))
    ax.set_xlabel("Time from release (s)")
    ax.set_ylabel("Knee angle from rest (deg)")
    ax.set_title("A captured pendulum trial, phone IMU at 100 Hz\n"
                 "MINT_18 right leg, pre — one of 172 recordings",
                 fontsize=12, pad=10)
    ax.set_xlim(-1, 11)
    return save(fig, "wa_trajectory.png")


# ── 3. agreement with the optical reference ───────────────────────────────────
def _icc(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    n = len(x)
    if n < 3:
        return np.nan, n
    m = np.column_stack([x, y])
    gm = m.mean()
    msr = 2 * ((m.mean(axis=1) - gm) ** 2).sum() / (n - 1)
    msc = n * ((m.mean(axis=0) - gm) ** 2).sum()
    mse = ((m - m.mean(axis=1, keepdims=True)
            - m.mean(axis=0, keepdims=True) + gm) ** 2).sum() / (n - 1)
    den = msr + mse + 2 * (msc - mse) / n
    return ((msr - mse) / den if den > 0 else np.nan), n


def fig_agreement():
    """Rank agreement vs value agreement.

    Spearman sits far above ICC on every parameter, which is the actual
    finding: the phone orders trials roughly correctly but does not reproduce
    the reference's values. Plotting ICC alone would read as a flat failure
    and would hide that.
    """
    from scipy.stats import spearmanr

    d = pd.read_csv(HERE / "Model_Analysis_Outputs" / "multimetric_trials.csv")
    params = ["N", "area_ratio", "omega_min_n", "f", "R2n",
              "omega_max_n", "phi_max_ratio"]
    labels = ["N  oscillations", "area ratio", "ω min (norm)",
              "f  frequency", "R2n  relaxation index",
              "ω max (norm)", "φ max ratio"]
    iccs, sps, ratios = [], [], []
    for p in params:
        o, i = d[f"opti_{p}"], d[f"imu_{p}"]
        ok = o.notna() & i.notna()
        v, _ = _icc(o[ok], i[ok])
        iccs.append(v if np.isfinite(v) else 0.0)
        sps.append(spearmanr(o[ok], i[ok])[0])
        ratios.append(i[ok].median() / o[ok].median() if o[ok].median() else np.nan)

    y = np.arange(len(params))
    fig, ax = plt.subplots(figsize=(9.0, 4.8))
    h = 0.34
    ax.barh(y - h / 2, sps, height=h, color=BLUE, zorder=3,
            label="Spearman — does it rank trials correctly?")
    ax.barh(y + h / 2, iccs, height=h, color=ORANGE, zorder=3,
            label="ICC — does it reproduce the value?")
    ax.axvline(0, color="#C9D4D1", lw=1.0, zorder=2)
    ax.axvline(0.90, color=NAVY, lw=1.1, ls="--", zorder=4)
    ax.text(0.912, len(params) - 0.55, "target 0.90", fontsize=9.5, color=NAVY)
    for i, (s, c) in enumerate(zip(sps, iccs)):
        ax.text(s + 0.02 if s >= 0 else s - 0.02, i - h / 2, f"{s:.2f}",
                va="center", ha="left" if s >= 0 else "right",
                fontsize=9.5, color=NAVY, zorder=5)
        ax.text(c + 0.02 if c >= 0 else c - 0.02, i + h / 2, f"{c:.2f}",
                va="center", ha="left" if c >= 0 else "right",
                fontsize=9.5, color=NAVY, zorder=5)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlim(-0.35, 1.05)
    ax.set_xlabel("Agreement with the OptiTrack reference")
    ax.legend(loc="lower right", fontsize=9.5, frameon=True,
              facecolor=BG, edgecolor="#C9D4D1")
    ax.set_title(
        f"The phone ranks trials, but does not yet reproduce values\n"
        f"{len(d)} paired trials, {d.pid.nunique()} participants — the two "
        f"velocity terms read {ratios[2]:.2f}× and {ratios[5]:.2f}× "
        f"the reference",
        fontsize=12, pad=10)
    ax.grid(axis="y", visible=False)
    return save(fig, "wa_agreement.png")


# ── 4. repeatability, the result that is ready now ────────────────────────────
def fig_testretest():
    rows = []
    for f in glob.glob(str(HERE / "Recordings" / "**" / "*_imu.csv"),
                       recursive=True):
        parts = os.path.normpath(f).split(os.sep)
        i = parts.index("Recordings")
        if len(parts) >= i + 5:
            rows.append({"folder": parts[i + 1], "leg": parts[i + 2],
                         "cond": parts[i + 3]})
    d = pd.DataFrame(rows)
    g = d.groupby(["folder", "leg", "cond"]).size().rename("n").reset_index()
    g = g[g.n >= 3].copy()
    g["arm"] = g.folder.str.split("_").str[0]
    names = {"MINT": "MINT  (MS)", "CHAT": "CHAT  (stroke)",
             "Control": "Control"}
    summ = (g.groupby("arm")
              .agg(groups=("n", "size"), participants=("folder", "nunique"),
                   trials=("n", "sum"))
              .reindex(["MINT", "CHAT", "Control"]).dropna())

    fig, ax = plt.subplots(figsize=(8.6, 3.8))
    y = range(len(summ))
    ax.barh(y, summ.groups, color=BAR, height=0.55, zorder=3)
    ax.set_yticks(list(y))
    ax.set_yticklabels([names.get(i, i) for i in summ.index], fontsize=11)
    ax.invert_yaxis()
    for i, r in enumerate(summ.itertuples()):
        ax.text(r.groups + 0.5, i,
                f"{int(r.groups)} groups   ·   {int(r.participants)} "
                f"participants   ·   {int(r.trials)} trials",
                va="center", fontsize=10, color=NAVY, zorder=5)
    ax.set_xlim(0, summ.groups.max() * 1.85)
    ax.set_xlabel("Participant × leg × session groups with ≥ 3 repeat trials")
    ax.set_title(f"Repeatability is ready to run today\n"
                 f"{int(summ.groups.sum())} repeat groups across "
                 f"{g.folder.nunique()} participants — no reference "
                 f"instrument, no MAS grade, no bench required",
                 fontsize=12, pad=10)
    ax.grid(axis="y", visible=False)
    return save(fig, "wa_testretest.png")


# ── 5. the bottleneck ─────────────────────────────────────────────────────────
def fig_bottleneck():
    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    # Counted directly off mas_scores.csv on 2026-09-08: unique participant x
    # leg pairs whose assessed_by is a named clinician. The earlier 5 / 2 in
    # this figure did not reproduce -- it appears to have dropped P15 left for
    # its 1+ flexion sub-score, and it predates P4's legs gaining phone IMU.
    cats = ["MAS > 0 legs\n(spastic)", "MAS = 0 legs\n(examined control)"]
    have = [6, 4]
    need = [20, 20]
    y = np.arange(len(cats))
    ax.barh(y, need, color=TRACK, height=0.5, zorder=2)
    ax.barh(y, have, color=BAD, height=0.5, zorder=3)
    for i, (h, n) in enumerate(zip(have, need)):
        ax.text(h + 0.4, i, f"have {h}", va="center", fontsize=11,
                color=BAD, zorder=5)
        ax.text(n - 0.4, i, f"need {n}", va="center", ha="right",
                fontsize=11, color=SLATE, zorder=5)
    ax.barh([2.0], [14], color=GOOD, height=0.5, zorder=3)
    ax.text(14.4, 2.0, "14 legs recoverable at zero recording cost",
            va="center", fontsize=11, color=GOOD, zorder=5)
    ax.set_yticks(list(y) + [2.0])
    ax.set_yticklabels(cats + ["P18–P24\nIMU but no MAS row"], fontsize=10)
    ax.invert_yaxis()
    ax.set_xlim(0, 30)
    ax.set_xlabel("Legs (target sized on AUC 0.807 at 80% power, ICC 0.5)")
    ax.set_title("The bottleneck is clinical, not technical\n"
                 "Seven already-recorded participants carry no MAS at all — "
                 "four of them stroke",
                 fontsize=12, pad=10)
    ax.grid(axis="y", visible=False)
    return save(fig, "wa_bottleneck.png")


# ── 6. the mobile engine's shape, and where the tests are not ─────────────────
def fig_app_modules():
    core = HERE / "mobile-imu-core"
    rows = []
    for f in sorted(core.glob("src/*.rs")):
        name = f.stem
        src = sum(1 for _ in f.open(encoding="utf8", errors="ignore"))
        inline = f.read_text(encoding="utf8", errors="ignore").count("#[test]")
        tf = core / "tests" / f"{name}_test.rs"
        integ = (tf.read_text(encoding="utf8", errors="ignore").count("#[test]")
                 if tf.exists() else 0)
        rows.append((name, src, inline + integ))
    rows.sort(key=lambda r: r[1])

    names = [r[0] + ".rs" for r in rows]
    srcs = [r[1] for r in rows]
    tests = [r[2] for r in rows]
    colors = [GOOD if t > 0 else BAD for t in tests]

    fig, ax = plt.subplots(figsize=(8.4, 5.6))
    ax.barh(range(len(rows)), srcs, color=colors, height=0.66, zorder=3)
    for i, (s, t) in enumerate(zip(srcs, tests)):
        ax.text(s + 18, i, f"{t} tests" if t else "no tests",
                va="center", fontsize=9.5,
                color=NAVY if t else BAD, zorder=5)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(names, fontsize=9.5)
    ax.set_xlim(0, max(srcs) * 1.30)
    ax.set_xlabel("Source lines")
    untested = sum(s for s, t in zip(srcs, tests) if t == 0)
    ax.set_title(
        f"The scoring engine: {sum(srcs):,} lines of Rust across "
        f"{len(rows)} modules\n"
        f"{sum(tests)} module tests — but {untested:,} lines "
        f"({untested / sum(srcs):.0%}) sit in modules with none",
        fontsize=12, pad=10)
    ax.grid(axis="y", visible=False)
    return save(fig, "app_modules.png")


def main() -> int:
    print("generating status figures ->", OUT)
    fig_cohort()
    fig_trajectory()
    fig_agreement()
    fig_testretest()
    fig_bottleneck()
    fig_app_modules()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
