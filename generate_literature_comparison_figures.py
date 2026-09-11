"""
generate_literature_comparison_figures.py
=========================================
Figures comparing our cohort against the published pendulum-test literature.

Three panels, each answering a question that came up while recalibrating
HEALTHY_REF and testing whether PT7 discriminates:

  fig_lit1  Where our cohort sits on the MAS scale, against Whelan 2018.
            Ours tops out at 1+ with a single leg there; theirs spans 0-3.
            This is the context every other comparison has to be read in.

  fig_lit2  Discrimination (AUC, MAS>0 vs MAS 0) -- ours against Whelan's
            Table 6. The comparison they made, on the metrics that map.

  fig_lit3  Our measured parameter values against the published healthy
            ranges from Popovic-Maneski 2017, for the two parameters whose
            definitions actually match ours.

Data comes from evaluate_pt7_discrimination.py's cache, so run that first.

Sources:
  Whelan A et al. (2018) J NeuroEng Rehabil, 131 knees, Table 6 AUCs.
  Popovic-Maneski L et al. (2017) IEEE TNSRE, healthy ranges (their group H).
Both summarised in docs/reference/2026-08-24-pendulum-test-literature-benchmarks.md

Usage:
    miniconda3/python.exe generate_literature_comparison_figures.py
"""
import csv
import itertools
import json
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = "C:/Users/cladi/Pendulastic"
CACHE = ("C:/Users/cladi/AppData/Local/Temp/claude/"
         "C--Users-cladi/2037fe88-f614-4162-8a4b-47072e640659/scratchpad/cohort_all.json")
OUT_DIR = os.path.join(ROOT, "Model_Analysis_Outputs", "paper_figures")

BLUE, ORANGE, INK, MUTED, GRID = "#2a78d6", "#eb6834", "#0b0b0b", "#898781", "#e1e0d9"


def _style(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=INK, labelsize=9)
    ax.grid(True, color=GRID, linewidth=0.8, axis="y")
    ax.set_axisbelow(True)


def load():
    recs = [r for r in json.load(open(CACHE)) if r["status"] == "ok"]
    mas = {}
    for row in csv.DictReader(open(os.path.join(ROOT, "mas_scores.csv"))):
        mas[(row["participant"].strip(), row["leg"].strip().lower())] = row["mas_grade"].strip()
    for r in recs:
        r["mas"] = mas.get((r["pid"], r["side"]))
    return recs


def auc(pos, neg):
    pos = [p for p in pos if p is not None and np.isfinite(p)]
    neg = [n for n in neg if n is not None and np.isfinite(n)]
    if not pos or not neg:
        return float("nan")
    w = sum(1 for a, b in itertools.product(pos, neg) if a > b)
    t = sum(1 for a, b in itertools.product(pos, neg) if a == b)
    return (w + 0.5 * t) / (len(pos) * len(neg))


# ── fig 1: MAS coverage, ours vs the benchmark cohort ───────────────────────
def fig_mas_coverage():
    ours = {"0": 32, "1": 8, "1+": 1, "2": 0, "3": 0}
    whelan = {"0": 53, "1": 33, "1+": 14, "2": 16, "3": 11}
    grades = ["0", "1", "1+", "2", "3"]
    x = np.arange(len(grades))

    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    ax.bar(x - 0.2, [ours[g] for g in grades], 0.4, color=ORANGE,
           label="This cohort (41 legs)")
    ax.bar(x + 0.2, [whelan[g] for g in grades], 0.4, color=BLUE,
           label="Whelan 2018 (131 knees)")
    for i, g in enumerate(grades):
        if ours[g] == 0:
            ax.text(i - 0.2, 1.2, "none", ha="center", fontsize=8,
                    color=ORANGE, rotation=90)
    _style(ax)
    ax.set_xticks(x)
    ax.set_xticklabels(["MAS " + g for g in grades])
    ax.set_ylabel("legs / knees")
    ax.set_title("We only sample the mild end of the scale",
                 fontsize=11, color=INK, loc="left")
    ax.legend(frameon=False, fontsize=9)
    fig.text(0.01, -0.06,
             "Our most spastic leg is a single MAS 1+. Whelan has 41 knees at "
             "1+ or above.\nEvery comparison below is therefore a comparison "
             "within mild spasticity only.",
             fontsize=8, color=MUTED)
    out = os.path.join(OUT_DIR, "fig_lit1_mas_coverage.png")
    fig.savefig(out, facecolor="white", bbox_inches="tight", dpi=160)
    plt.close(fig)
    return out


# ── fig 2: discrimination against Whelan Table 6 ────────────────────────────
def fig_auc_comparison(recs):
    mas0 = [r for r in recs if r["mas"] == "0"]
    masp = [r for r in recs if r["mas"] in ("1", "1+")]

    # ours, direction-aware: several separate in the opposite polarity
    def a(k):
        v = auc([r.get(k) for r in masp], [r.get(k) for r in mas0])
        return max(v, 1 - v)

    pairs = [
        ("R2n\n(relaxation index)", a("R2n"), 0.784, "RI"),
        ("N\n(swing count)", a("N"), 0.665, "Ncyc"),
        ("f\n(frequency)", a("f"), None, None),
        ("PT7\n(composite)", a("PT7"), None, None),
    ]
    labels = [p[0] for p in pairs]
    x = np.arange(len(pairs))

    fig, ax = plt.subplots(figsize=(7.6, 4.0))
    ax.bar(x - 0.2, [p[1] for p in pairs], 0.4, color=ORANGE, label="This cohort")
    theirs = [p[2] if p[2] else np.nan for p in pairs]
    ax.bar(x + 0.2, theirs, 0.4, color=BLUE, label="Whelan 2018")
    for i, p in enumerate(pairs):
        ax.text(i - 0.2, p[1] + 0.015, "%.3f" % p[1], ha="center", fontsize=8, color=INK)
        if p[2]:
            ax.text(i + 0.2, p[2] + 0.015, "%.3f" % p[2], ha="center", fontsize=8, color=INK)
        else:
            ax.text(i + 0.2, 0.52, "no\npublished\ncounterpart", ha="center",
                    fontsize=7, color=MUTED)
    ax.axhline(0.7, color=INK, linewidth=1, linestyle="--")
    # Axes-fraction coords, not data coords: at data x the caption landed
    # outside the plot area entirely and matplotlib grew the canvas around it.
    ax.text(0.985, 0.7, "acceptability bar (0.7)", fontsize=8, color=INK,
            ha="right", va="bottom", transform=ax.get_yaxis_transform())
    ax.axhline(0.5, color=MUTED, linewidth=1)
    # Right-aligned like the 0.7 caption: at the left edge it sat behind the
    # first bar, which is where the eye goes first.
    ax.text(0.985, 0.5, "chance (0.5)", fontsize=8, color=MUTED,
            ha="right", va="bottom", transform=ax.get_yaxis_transform())
    _style(ax)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0.45, 0.90)
    ax.set_ylabel("AUC, MAS>0 vs MAS 0")
    ax.set_title("Same comparison, same ballpark \u2014 on 23 spastic trials against their 78",
                 fontsize=11, color=INK, loc="left")
    ax.legend(frameon=False, fontsize=9, loc="upper right")
    fig.text(0.01, -0.10,
             "Ours: 151 MAS-0 trials vs 23 MAS>0 trials. Whelan: 53 vs 78 knees.\n"
             "Both find the relaxation index strong and the swing count weak, which "
             "is the ranking that matters.\nf and PT7 have no counterpart in their "
             "table; PT7 is Popovi\u0107's composite, not one of their metrics.",
             fontsize=8, color=MUTED)
    out = os.path.join(OUT_DIR, "fig_lit2_auc_vs_whelan.png")
    fig.savefig(out, facecolor="white", bbox_inches="tight", dpi=160)
    plt.close(fig)
    return out


# ── fig 3: measured values against the published healthy ranges ─────────────
def fig_published_ranges(recs):
    mas0 = [r for r in recs if r["mas"] == "0"]
    masp = [r for r in recs if r["mas"] in ("1", "1+")]

    fig, axes = plt.subplots(1, 2, figsize=(8.2, 3.9))

    # R2n: published healthy is "> 1"
    ax = axes[0]
    d0 = [r["R2n"] for r in mas0 if r.get("R2n") is not None]
    dp = [r["R2n"] for r in masp if r.get("R2n") is not None]
    # Clipped at 2.5. A few trials reach 8.5, and at full range every box
    # collapses to a line -- the point is where the medians sit relative to
    # 1.0, which is invisible if the outliers set the scale.
    ax.axhspan(1.0, 2.5, color=BLUE, alpha=0.10)
    ax.axhline(1.0, color=BLUE, linewidth=1.4)
    bp = ax.boxplot([d0, dp], positions=[1, 2], widths=0.5, patch_artist=True,
                    medianprops=dict(color=INK), showfliers=False)
    ax.set_ylim(0, 2.5)
    ax.text(0.985, 1.02, "published healthy: R2n > 1", fontsize=8, color=BLUE,
            ha="right", va="bottom", transform=ax.get_yaxis_transform())
    _clipped = sum(1 for v in d0 + dp if v > 2.5)
    ax.text(0.5, 0.02, "%d trials above 2.5 not shown" % _clipped,
            fontsize=7, color=MUTED, ha="center", transform=ax.transAxes)
    for patch, c in zip(bp["boxes"], [MUTED, ORANGE]):
        patch.set_facecolor(c)
        patch.set_alpha(0.55)
    _style(ax)
    ax.set_xticks([1, 2])
    ax.set_xticklabels(["MAS 0\n(n=%d)" % len(d0), "MAS>0\n(n=%d)" % len(dp)])
    ax.set_ylabel("R2n")
    ax.set_title("R2n against its published bound", fontsize=10, color=INK, loc="left")

    # N: published healthy 6 to 7
    ax = axes[1]
    d0 = [r["N"] for r in mas0 if r.get("N") is not None]
    dp = [r["N"] for r in masp if r.get("N") is not None]
    ax.axhspan(6, 7, color=BLUE, alpha=0.14)
    ax.axhline(3.5, color="#b03030", linewidth=1.2, linestyle=":")
    bp = ax.boxplot([d0, dp], positions=[1, 2], widths=0.5, patch_artist=True,
                    medianprops=dict(color=INK), showfliers=False)
    ax.set_ylim(0, 10.5)
    # Right-aligned and clear of the boxes, which sit left of centre.
    ax.text(0.985, 7.05, "published healthy: N = 6 to 7", fontsize=8, color=BLUE,
            ha="right", va="bottom", transform=ax.get_yaxis_transform())
    ax.text(0.985, 3.55, "old reference (3.5) — was the 4 s cap",
            fontsize=8, color="#b03030", ha="right", va="bottom",
            transform=ax.get_yaxis_transform())
    for patch, c in zip(bp["boxes"], [MUTED, ORANGE]):
        patch.set_facecolor(c)
        patch.set_alpha(0.55)
    _style(ax)
    ax.set_xticks([1, 2])
    ax.set_xticklabels(["MAS 0\n(n=%d)" % len(d0), "MAS>0\n(n=%d)" % len(dp)])
    ax.set_ylabel("N (swings)")
    ax.set_title("N against its published range", fontsize=10, color=INK, loc="left")

    fig.suptitle("The two parameters whose definitions match the published ones",
                 fontsize=11, color=INK, x=0.01, ha="left")
    fig.text(0.01, -0.08,
             "The other five cannot be plotted this way: Popovi\u0107's phi-max is an "
             "angle in radians and omega-max unnormalised rad/s against our\n"
             "normalised forms, and f and area_ratio were introduced with no healthy "
             "value published at all.",
             fontsize=8, color=MUTED)
    out = os.path.join(OUT_DIR, "fig_lit3_published_ranges.png")
    fig.savefig(out, facecolor="white", bbox_inches="tight", dpi=160)
    plt.close(fig)
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    recs = load()
    matched = [r for r in recs if r["mas"] is not None]
    print("scored trials: %d   matched to a MAS grade: %d" % (len(recs), len(matched)))
    for fn in (fig_mas_coverage(), fig_auc_comparison(recs),
               fig_published_ranges(recs), fig_healthy_ranges(recs)):
        print("wrote %s" % fn)



# ── fig 4: our healthy range against the published healthy range ────────────
def fig_healthy_ranges(recs):
    """Every metric with a published healthy value, in the PUBLISHED units.

    Our phi_max_ratio, omega_max_n and omega_min_n are the published
    quantities divided by A0, so multiplying A0 back in makes five of the
    seven directly comparable rather than two.
    """
    D = np.pi / 180.0
    h = [r for r in recs if r["mas"] == "0"]

    def col(fn):
        v = [fn(r) for r in h]
        return np.array([x for x in v if x is not None and np.isfinite(x)])

    def denorm(key):
        return col(lambda r: (r[key] * r["A0_deg"] * D)
                   if r.get(key) is not None and r.get("A0_deg") else None)

    panels = [
        ("R2n",  col(lambda r: r.get("R2n")), 1.0, 2.5, "", "R2n > 1"),
        ("N",    col(lambda r: r.get("N")),   6.0, 7.0, "swings", "N = 6 to 7"),
        ("phi-max", denorm("phi_max_ratio"),  0.34, 0.61, "rad", "0.34 to 0.61 rad"),
        ("omega-max", denorm("omega_max_n"),  11.0, 17.0, "rad/s", "11 to 17 rad/s"),
        ("omega-min", denorm("omega_min_n"), -12.0, -9.0, "rad/s", "-12 to -9 rad/s"),
    ]

    fig, axes = plt.subplots(1, 5, figsize=(13.0, 4.2))
    for ax, (name, v, lo, hi, unit, band) in zip(axes, panels):
        inside = ((v >= lo) & (v <= hi)).mean() * 100
        ax.axhspan(lo, hi, color=BLUE, alpha=0.16, zorder=0)
        bp = ax.boxplot([v], positions=[1], widths=0.42, patch_artist=True,
                        medianprops=dict(color=INK, linewidth=1.6),
                        showfliers=False, zorder=3)
        bp["boxes"][0].set_facecolor(ORANGE)
        bp["boxes"][0].set_alpha(0.6)
        # 5th-95th whisker of our own data, so the FULL spread is visible and
        # not just the box.
        p5, p95 = np.percentile(v, [5, 95])
        ax.plot([1, 1], [p5, p95], color=MUTED, linewidth=1, zorder=2)
        _style(ax)
        lo_ax = min(p5, lo) - 0.12 * (max(p95, hi) - min(p5, lo))
        hi_ax = max(p95, hi) + 0.12 * (max(p95, hi) - min(p5, lo))
        ax.set_ylim(lo_ax, hi_ax)
        ax.set_xticks([1])
        ax.set_xticklabels(["ours\n(n=%d)" % len(v)], fontsize=9)
        ax.set_title(name + ((" (" + unit + ")") if unit else ""),
                     fontsize=10, color=INK)
        ax.text(0.5, 0.965, "published: " + band, fontsize=7.5, color=BLUE,
                ha="center", va="top", transform=ax.transAxes)
        ax.text(0.5, 0.045, "%.0f%% of our trials inside" % inside, fontsize=8,
                color=INK if inside >= 25 else "#b03030",
                ha="center", va="bottom", transform=ax.transAxes)

    fig.suptitle("Our healthy (MAS 0) legs against the published healthy ranges",
                 fontsize=12, color=INK, x=0.01, ha="left")
    fig.subplots_adjust(bottom=0.10, wspace=0.42)
    fig.text(0.01, -0.16,
             "Blue band = Popović-Maneski 2017 group H. Orange box = our MAS-0 "
             "trials (IQR), grey line = 5th-95th percentile.\n"
             "phi-max, omega-max and omega-min are OUR normalised parameters "
             "multiplied back by A0, which is what makes them comparable at all.\n\n"
             "The two velocity panels should NOT be read as our participants "
             "swinging half as fast. Peak angular velocity has no asymptote as "
             "the smoothing\nwindow shrinks -- this codebase measures 186 deg/s at "
             "a 0.75 s window against 433 deg/s at 0.05 s on the same trials -- so "
             "a published value\nmeasured under different smoothing is not "
             "commensurable. f and area_ratio are absent because "
             "Popović-Maneski introduced them and published no healthy value.",
             fontsize=8, color=MUTED)
    out = os.path.join(OUT_DIR, "fig_lit4_healthy_ranges.png")
    fig.savefig(out, facecolor="white", bbox_inches="tight", dpi=160)
    plt.close(fig)
    return out


if __name__ == "__main__":
    main()
