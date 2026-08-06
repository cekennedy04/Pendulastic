"""
mas_validation.py
==================
Cohort-level validation of the computed PT score against real clinician MAS
grades (mas_scores.csv), kept explicitly separate from the existing
device-vs-OptiTrack concurrent-validity analysis (control_validation_stats.csv,
P5_concurrent_validity.csv) -- per the literature convention that device-vs-
reference-device validity (continuous, ICC/Pearson) and device-vs-clinician-
MAS validity (ordinal, Spearman/weighted-kappa) are two different validity
claims and shouldn't be blended into one number.

Reuses pt_report_common.collect_participant() for PT scores -- the same
source of truth every other figure in Model_Analysis_Outputs/PT_Scores is
built from -- so this stays numerically consistent with the rest of the
project. See docs/superpowers/specs/2026-08-06-mas-pt-score-validation-design.md.

Each mas_scores.csv row is one clinician MAS assessment per participant per
leg (not per session -- that's how these were actually collected), so a
grade is matched against the mean PT score across EVERY recorded
condition/session for that leg. `condition` in the CSV is free-text context
only (e.g. a diagnosis note) and is not used for matching.

Run once you've added rows to mas_scores.csv:
    .venv\\Scripts\\python.exe mas_validation.py
"""
from __future__ import annotations

import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import spearmanr
from sklearn.metrics import cohen_kappa_score, roc_auc_score, roc_curve

import pendulastic_pt_score as pt
import pt_report_common as common

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAS_CSV = os.path.join(BASE_DIR, "mas_scores.csv")
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "MAS_Validation")
STATS_CSV = os.path.join(OUT_DIR, "mas_validation_stats.csv")
FIGURE_PNG = os.path.join(OUT_DIR, "mas_validation_figure.png")

# Single source of truth for ordinal MAS coding -- see pendulastic_pt_score.py.
MAS_ORDER = pt.MAS_ORDER
MAS_RANK = pt.MAS_RANK

_MIN_N_FOR_CONFIDENCE = 5
_MIN_CLASS_N_FOR_ROC = 3


# ══════════════════════════════════════════════════════════════════════════
# Pure functions (unit-testable, no I/O)
# ══════════════════════════════════════════════════════════════════════════

def _valid_grade(grade: str) -> bool:
    return grade in MAS_RANK


def pair_pt_and_mas(mas_rows, pt_lookup):
    """mas_rows: list of {participant, leg, condition, mas_grade, ...} dicts,
    as loaded from mas_scores.csv. pt_lookup: callable(participant, leg,
    condition) -> float|None, the mean PT score for that participant/leg
    (see _pt_lookup_factory -- condition is not used for matching), or None
    if that participant/leg has no recorded trials at all.

    Returns one record per input row. Rows that pass get pt_score/
    predicted_mas added. Rows that fail (bad grade, or no PT match) get a
    "_skip_reason" key instead -- this function does no printing itself;
    the caller decides how to surface skips."""
    out = []
    for row in mas_rows:
        grade = row["mas_grade"]
        if not _valid_grade(grade):
            out.append(dict(row, _skip_reason=f"invalid mas_grade {grade!r} (must be one of {MAS_ORDER})"))
            continue
        pt_score = pt_lookup(row["participant"], row["leg"], row["condition"])
        if pt_score is None:
            out.append(dict(row, _skip_reason="no matching trial data for this participant/leg/condition"))
            continue
        paired = dict(row)
        paired["pt_score"] = pt_score
        paired["predicted_mas"] = pt.pt_to_mas(pt_score)
        out.append(paired)
    return out


def compute_validation_stats(pairs):
    """pairs: already-valid paired records (no _skip_reason, has pt_score/
    predicted_mas). Returns n, preliminary, spearman_rho/p, weighted_kappa,
    per_grade median/IQR/n, and roc_auc (None if class balance is too thin)."""
    n = len(pairs)
    result = {"n": n, "preliminary": n < _MIN_N_FOR_CONFIDENCE}

    if n < 2:
        result.update(spearman_rho=None, spearman_p=None, weighted_kappa=None,
                      per_grade={}, roc_auc=None)
        return result

    pt_scores = np.array([p["pt_score"] for p in pairs], dtype=float)
    actual_ranks = np.array([MAS_RANK[p["mas_grade"]] for p in pairs], dtype=int)
    predicted_ranks = np.array([MAS_RANK[p["predicted_mas"]] for p in pairs], dtype=int)

    rho, p_val = spearmanr(pt_scores, actual_ranks)
    kappa = cohen_kappa_score(actual_ranks, predicted_ranks,
                              labels=list(range(len(MAS_ORDER))), weights="linear")

    per_grade = {}
    for grade in MAS_ORDER:
        vals = [p["pt_score"] for p in pairs if p["mas_grade"] == grade]
        if vals:
            arr = np.array(vals, dtype=float)
            q1, q3 = np.percentile(arr, [25, 75])
            per_grade[grade] = {"median": float(np.median(arr)), "iqr": float(q3 - q1), "n": len(vals)}

    roc_auc = None
    binary = np.array([0 if p["mas_grade"] == "0" else 1 for p in pairs])
    if (binary == 0).sum() >= _MIN_CLASS_N_FOR_ROC and (binary == 1).sum() >= _MIN_CLASS_N_FOR_ROC:
        roc_auc = float(roc_auc_score(binary, pt_scores))

    result.update(spearman_rho=float(rho), spearman_p=float(p_val),
                  weighted_kappa=float(kappa), per_grade=per_grade, roc_auc=roc_auc)
    return result


# ══════════════════════════════════════════════════════════════════════════
# I/O
# ══════════════════════════════════════════════════════════════════════════

def load_mas_scores(csv_path):
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            row = {k: (v or "").strip() for k, v in raw.items()}
            if row.get("participant"):
                rows.append(row)
    return rows


def _pt_lookup_factory():
    """pt_lookup(participant, leg, condition) -> float|None, backed by
    pt_report_common.collect_participant(), cached per participant so a
    mas_scores.csv with many rows for one participant only scans their
    trials once.

    `condition` is accepted for signature/record-keeping only and is NOT
    used to filter trials: real-world collection here is one clinician MAS
    assessment per participant per leg (not one per session), so a grade is
    treated as applying to every recorded condition for that leg -- the
    mean PT score is taken across ALL of that leg's trials, regardless of
    which session/condition each one came from."""
    cache = {}

    def lookup(participant, leg, condition):
        if participant not in cache:
            cache[participant] = common.collect_participant(participant)[0]
        trials = [r for (leg_key, _cond), recs in cache[participant].items()
                 if leg_key == leg for r in recs]
        if not trials:
            return None
        return float(np.mean([r["pt7"] for r in trials]))

    return lookup


# ══════════════════════════════════════════════════════════════════════════
# Plotting / output
# ══════════════════════════════════════════════════════════════════════════

def make_validation_figure(pairs, stats, out_path):
    n_panels = 3 if stats["roc_auc"] is not None else 2
    fig, axes = plt.subplots(1, n_panels, figsize=(6 * n_panels, 5.5), facecolor="white")

    # ── Panel 1: PT score distribution by MAS grade ─────────────────────────
    ax = axes[0]
    ax.set_facecolor("#f8f9fa")
    ax.grid(True, color=common.BG_GRID, linestyle="-", linewidth=0.8, axis="y")
    present = [(g, [p["pt_score"] for p in pairs if p["mas_grade"] == g]) for g in MAS_ORDER]
    present = [(g, d) for g, d in present if d]
    if present:
        labels = [g for g, _ in present]
        values = [d for _, d in present]
        bp = ax.boxplot(values, tick_labels=labels, patch_artist=True, showfliers=False)
        for patch, g in zip(bp["boxes"], labels):
            patch.set_facecolor(pt._MAS_COLOR.get(g, "#999999"))
            patch.set_alpha(0.6)
        rng = np.random.RandomState(13)
        for i, (g, d) in enumerate(present, start=1):
            xs = i + rng.uniform(-0.08, 0.08, size=len(d))
            ax.scatter(xs, d, color="#333333", s=18, alpha=0.6, zorder=3)
    ax.set_xlabel("Clinician MAS grade", fontsize=9)
    ax.set_ylabel("PT score (7-parameter)", fontsize=9)
    caveat = " (preliminary -- small n)" if stats["preliminary"] else ""
    rho_txt = (f"rho={stats['spearman_rho']:.2f}, p={stats['spearman_p']:.3f}"
              if stats["spearman_rho"] is not None else "rho=n/a")
    ax.set_title(f"PT score vs MAS grade (n={stats['n']}{caveat})\n{rho_txt}",
                fontsize=10, fontweight="bold")

    # ── Panel 2: agreement heatmap (actual x predicted) ─────────────────────
    ax = axes[1]
    mat = np.zeros((len(MAS_ORDER), len(MAS_ORDER)), dtype=int)
    for p in pairs:
        mat[MAS_RANK[p["mas_grade"]], MAS_RANK[p["predicted_mas"]]] += 1
    ax.imshow(mat, cmap="Blues")
    ax.set_xticks(range(len(MAS_ORDER))); ax.set_xticklabels(MAS_ORDER, fontsize=9)
    ax.set_yticks(range(len(MAS_ORDER))); ax.set_yticklabels(MAS_ORDER, fontsize=9)
    ax.set_xlabel("Predicted MAS (from PT score)", fontsize=9)
    ax.set_ylabel("Actual (clinician) MAS", fontsize=9)
    peak = mat.max() if mat.size else 0
    for i in range(len(MAS_ORDER)):
        for j in range(len(MAS_ORDER)):
            if mat[i, j]:
                ax.text(j, i, str(mat[i, j]), ha="center", va="center",
                       color="white" if peak and mat[i, j] > peak / 2 else "black", fontsize=9)
    kappa_txt = f"weighted kappa={stats['weighted_kappa']:.2f}" if stats["weighted_kappa"] is not None else "kappa=n/a"
    ax.set_title(f"Agreement: predicted vs actual MAS\n{kappa_txt}", fontsize=10, fontweight="bold")

    # ── Panel 3 (optional): ROC, MAS>=1 ("spastic") vs MAS==0 ───────────────
    if stats["roc_auc"] is not None:
        ax = axes[2]
        binary = np.array([0 if p["mas_grade"] == "0" else 1 for p in pairs])
        pt_scores = np.array([p["pt_score"] for p in pairs])
        fpr, tpr, _ = roc_curve(binary, pt_scores)
        ax.plot(fpr, tpr, color=common.COLORS["blue"], linewidth=2)
        ax.plot([0, 1], [0, 1], color="#cccccc", linestyle="--", linewidth=1)
        ax.set_xlabel("False positive rate", fontsize=9)
        ax.set_ylabel("True positive rate", fontsize=9)
        ax.set_title(f"Spastic (MAS>=1) vs not\nAUC={stats['roc_auc']:.2f}", fontsize=10, fontweight="bold")

    fig.suptitle("PT Score vs Clinician MAS -- Concurrent Validity", fontsize=12, y=1.03, color="#333333")
    plt.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"-> {out_path}")


def write_stats_csv(stats, out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        w.writerow(["n", stats["n"]])
        w.writerow(["preliminary_small_n", stats["preliminary"]])
        w.writerow(["spearman_rho", stats["spearman_rho"]])
        w.writerow(["spearman_p", stats["spearman_p"]])
        w.writerow(["weighted_kappa", stats["weighted_kappa"]])
        w.writerow(["roc_auc", stats["roc_auc"]])
        w.writerow([])
        w.writerow(["mas_grade", "median_pt_score", "iqr_pt_score", "n"])
        for grade in MAS_ORDER:
            pg = stats["per_grade"].get(grade)
            if pg:
                w.writerow([grade, pg["median"], pg["iqr"], pg["n"]])
    print(f"-> {out_path}")


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════

def main():
    if not os.path.isfile(MAS_CSV):
        print(f"{MAS_CSV} not found.")
        print("Create it with columns: participant,leg,condition,mas_grade,assessed_by,assessed_date,notes")
        print("One row per participant per leg (not per session) -- 'condition' is free-text")
        print("context only and isn't used for matching. Example row: 13,right,MS,2,Dr. Smith,2026-07-20,")
        return

    raw_rows = load_mas_scores(MAS_CSV)
    if not raw_rows:
        print("0 MAS-scored trials found in mas_scores.csv.")
        return

    paired = pair_pt_and_mas(raw_rows, _pt_lookup_factory())
    valid = [p for p in paired if "_skip_reason" not in p]
    for row in paired:
        if "_skip_reason" in row:
            print(f"Skipping P{row.get('participant')} {row.get('leg')}/{row.get('condition')}: "
                 f"{row['_skip_reason']}")

    if not valid:
        print("0 MAS-scored trials found with matching PT-score data.")
        return

    stats = compute_validation_stats(valid)
    write_stats_csv(stats, STATS_CSV)
    make_validation_figure(valid, stats, FIGURE_PNG)
    print(f"n={stats['n']}" + (" (preliminary -- small n)" if stats["preliminary"] else ""))


if __name__ == "__main__":
    main()
