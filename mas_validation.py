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

Each mas_scores.csv row is one clinician MAS assessment for a specific
participant/leg/session -- matched against the mean PT score for that exact
session. `condition` is matched against the real condition strings
pt_report_common derives from folder names using bag-of-tokens comparison
(see _tokenize_condition), not an exact string match: mas_scores.csv is
free-text ("1 week post") and doesn't necessarily share word order/
separators with the folder-derived condition ("week_1_post").

(Revision note: an earlier version of this script ignored `condition`
entirely and averaged across every session for a leg, when the only data on
hand was one baseline assessment per leg. Once per-visit grades showed up in
mas_scores.csv -- confirmed with the user -- that stopped being correct: it
was pairing one leg's pre-treatment PT score against a post-treatment MAS
grade. Matching reverted to per-condition on 2026-08-06.)

Run once you've added rows to mas_scores.csv:
    .venv\\Scripts\\python.exe mas_validation.py
"""
from __future__ import annotations

import csv
import os
import re

import matplotlib
if __name__ == "__main__":
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
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

# Unlike condition/diagnosis/assessed_by (free text, never validated),
# stronger_leg is a closed enum like mas_grade -- "" means not assessed.
STRONGER_LEG_OPTIONS = ["", "left", "right", "equal"]

# Header written when append_mas_score() has to create mas_scores.csv from
# scratch (the file is gitignored, so it's simply absent on a fresh checkout).
# This is the LIVE schema, including stronger_leg/notes -- what the app's
# MAS entry form targets. main()'s "file not found" message still describes
# the original 2026-08-06 column set; that's the CLI's own separate UX and
# is left alone deliberately.
DEFAULT_MAS_FIELDS = ["participant", "leg", "condition", "diagnosis",
                      "mas_grade", "assessed_by", "assessed_date",
                      "stronger_leg", "notes"]

# append_mas_score() only ever widens mas_scores.csv's header for these two
# fields -- an explicit allowlist, not "any key in row the header lacks".
# Widening on any unrecognized key would let a future typo'd dict key
# permanently become a CSV column; an unrelated stray key still falls
# through to the existing extrasaction="ignore" append behavior instead.
WIDENABLE_MAS_FIELDS = ["stronger_leg", "notes"]

_MIN_N_FOR_CONFIDENCE = 5
_MIN_CLASS_N_FOR_ROC = 3


# ══════════════════════════════════════════════════════════════════════════
# Pure functions (unit-testable, no I/O)
# ══════════════════════════════════════════════════════════════════════════

def _valid_grade(grade: str) -> bool:
    return grade in MAS_RANK


def _valid_stronger_leg(value: str) -> bool:
    return value in STRONGER_LEG_OPTIONS


def pair_pt_and_mas(mas_rows, pt_lookup):
    """mas_rows: list of {participant, leg, condition, mas_grade, ...} dicts,
    as loaded from mas_scores.csv. pt_lookup: callable(participant, leg,
    condition) -> float|None, the mean PT score for that specific
    participant/leg/condition (see _pt_lookup_factory -- condition is
    matched via bag-of-tokens, not an exact string), or None if no trial
    data matches.

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


def append_mas_score(row: dict, csv_path=MAS_CSV) -> None:
    """Appends one clinician MAS assessment to csv_path. Raises ValueError
    (no write attempted) if row["mas_grade"] isn't one of MAS_ORDER, or if
    row["stronger_leg"] is present and isn't one of STRONGER_LEG_OPTIONS.
    Reads the file's own current header rather than assuming a fixed column
    set, so this stays correct even if mas_scores.csv's schema drifts again
    the way it already has once (see module docstring).

    If csv_path doesn't exist yet it's created with the DEFAULT_MAS_FIELDS
    header -- mas_scores.csv is gitignored, so on a fresh checkout the very
    first save would otherwise die with FileNotFoundError."""
    grade = row.get("mas_grade", "")
    if not _valid_grade(grade):
        raise ValueError(f"invalid mas_grade {grade!r} (must be one of {MAS_ORDER})")
    stronger_leg = row.get("stronger_leg", "")
    if not _valid_stronger_leg(stronger_leg):
        raise ValueError(
            f"invalid stronger_leg {stronger_leg!r} (must be one of {STRONGER_LEG_OPTIONS})")
    if not os.path.exists(csv_path):
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=DEFAULT_MAS_FIELDS).writeheader()
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        existing_rows = list(reader) if fieldnames else []

    if not fieldnames:
        # Zero-byte file that exists but never got a header written -- e.g.
        # an earlier run crashed between creating the file and writing the
        # header. Nothing to preserve; start from the canonical schema.
        widened = list(DEFAULT_MAS_FIELDS)
        for k in WIDENABLE_MAS_FIELDS:
            if k in row and k not in widened:
                widened.append(k)
        _atomic_write_mas_csv(csv_path, widened, [], row)
        return

    new_fields = [k for k in WIDENABLE_MAS_FIELDS
                 if k in row and k not in fieldnames]
    if not new_fields:
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writerow(row)
        return

    if len(set(fieldnames)) != len(fieldnames):
        raise ValueError(
            f"{csv_path}: header has a duplicate column name -- fix this "
            f"file by hand before stronger_leg/notes can be added automatically")

    for i, existing in enumerate(existing_rows, start=2):  # row 1 is the header
        if None in existing:
            raise ValueError(
                f"{csv_path}: row {i} has more fields than the header "
                f"({len(fieldnames)} columns) -- fix this row by hand before "
                f"stronger_leg/notes can be added automatically")

    widened = list(fieldnames) + new_fields
    _atomic_write_mas_csv(csv_path, widened, existing_rows, row)


def _atomic_write_mas_csv(csv_path, fieldnames, existing_rows, new_row):
    """Writes header + existing_rows + new_row to csv_path via a temp file
    + os.replace -- matches pendulastic_storage.save_trial's pattern, so a
    crash mid-write can't corrupt csv_path (either the pre-write file or
    the fully-written new one is on disk, never a partial one). The temp
    file is always opened in "w" mode, so a stale .tmp left over from an
    earlier crashed run is overwritten from scratch, not appended to."""
    tmp_path = csv_path + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing_rows)
        writer.writerow(new_row)
    os.replace(tmp_path, csv_path)


def _tokenize_condition(text):
    """Bag-of-tokens normalization so mas_scores.csv's free-text condition
    values (e.g. "1 week post") match pt_report_common's folder-derived
    condition strings (e.g. "week_1_post") regardless of word order or
    separator style. Deliberately NOT a fuzzy/substring match -- "post" and
    "post_again" must stay distinct, so this only matches when the full
    token sets are equal."""
    return frozenset(t for t in re.split(r"[^a-z0-9]+", text.lower()) if t)


def _pt_lookup_factory():
    """pt_lookup(participant, leg, condition) -> float|None, backed by
    pt_report_common.collect_participant(), cached per participant so a
    mas_scores.csv with many rows for one participant only scans their
    trials once.

    `condition` is matched against the real condition(s) recorded for that
    participant/leg via _tokenize_condition -- every trial whose condition
    tokenizes to the same set as the requested one is pooled into the mean
    PT score. Returns None if nothing matches (wrong leg, or no condition
    with that token set recorded for this participant)."""
    cache = {}

    def lookup(participant, leg, condition):
        if participant not in cache:
            cache[participant] = common.collect_participant(participant)[0]
        wanted = _tokenize_condition(condition)
        trials = [r for (leg_key, cond_key), recs in cache[participant].items()
                 if leg_key == leg and _tokenize_condition(cond_key) == wanted
                 for r in recs]
        if not trials:
            return None
        return float(np.mean([r["pt7"] for r in trials]))

    return lookup


def available_conditions(participant, leg):
    """Real condition strings on record for this participant/leg -- used
    only to make an unmatched-condition warning actionable (see main())."""
    by_leg_tp = common.collect_participant(participant)[0]
    return sorted({cond for (leg_key, cond) in by_leg_tp if leg_key == leg})


# ══════════════════════════════════════════════════════════════════════════
# Plotting / output
# ══════════════════════════════════════════════════════════════════════════

def build_validation_figure(pairs, stats):
    # Object-oriented Figure API, deliberately NOT plt.subplots: under the
    # app's TkAgg backend pyplot would spin up a FigureManagerTk with its own
    # tk.Tk() root -- a second Tcl interpreter inside the running app on every
    # refresh(). Matches pendulastic_workbench.py's embedding pattern.
    n_panels = 3 if stats["roc_auc"] is not None else 2
    fig = Figure(figsize=(6 * n_panels, 5.5), facecolor="white")
    axes = fig.subplots(1, n_panels)

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
    fig.tight_layout()
    return fig


def save_validation_figure(pairs, stats, out_path):
    fig = build_validation_figure(pairs, stats)
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
        print("One row per participant/leg/session. 'condition' should describe which visit this")
        print("was (e.g. pre, post, 1 week post) -- it's matched loosely against the real folder-")
        print("derived condition, not required to be an exact string.")
        print("Example row: 13,right,pre,2,Dr. Smith,2026-07-20,")
        return

    raw_rows = load_mas_scores(MAS_CSV)
    if not raw_rows:
        print("0 MAS-scored trials found in mas_scores.csv.")
        return

    paired = pair_pt_and_mas(raw_rows, _pt_lookup_factory())
    valid = [p for p in paired if "_skip_reason" not in p]
    for row in paired:
        if "_skip_reason" in row:
            reason = row["_skip_reason"]
            if reason == "no matching trial data for this participant/leg/condition":
                available = available_conditions(row.get("participant", ""), row.get("leg", ""))
                reason = f"no trial data matching condition {row.get('condition')!r} -- available for this leg: {available or '(none recorded)'}"
            print(f"Skipping P{row.get('participant')} {row.get('leg')}/{row.get('condition')}: {reason}")

    if not valid:
        print("0 MAS-scored trials found with matching PT-score data.")
        return

    stats = compute_validation_stats(valid)
    write_stats_csv(stats, STATS_CSV)
    save_validation_figure(valid, stats, FIGURE_PNG)
    print(f"n={stats['n']}" + (" (preliminary -- small n)" if stats["preliminary"] else ""))


if __name__ == "__main__":
    main()
