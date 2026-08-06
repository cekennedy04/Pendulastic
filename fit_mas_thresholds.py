"""
fit_mas_thresholds.py
======================
Refits pt_to_mas()'s `_MAS` threshold boundaries (pendulastic_pt_score.py)
against real clinician MAS scores, once there's enough data to do so
responsibly. Companion to mas_validation.py -- reuses its exact
data-loading/pairing logic (mas_scores.csv, pt_report_common.collect_participant())
so the two scripts never disagree about which trials pair with which grade.

Why grid search over threshold candidates, not scipy.optimize.minimize:
weighted Cohen's kappa is a piecewise-constant (step) function of the
threshold values -- it only changes when a threshold crosses one of the
observed PT scores. Gradient-based optimizers see zero gradient almost
everywhere and never move. The only threshold VALUES that can possibly
change the result are the observed PT scores themselves, so this
exhaustively searches ordered combinations of candidate cut points drawn
from a fixed-size grid spanning the observed score range (GRID_POINTS,
independent of sample size -- runtime doesn't blow up as more participants
are added; resolution is the only thing traded off).

Why the objective is weighted kappa alone, not "kappa or Spearman": Spearman's
rho is a property of the raw continuous PT score vs. MAS rank -- it does NOT
depend on pt_to_mas()'s thresholds at all, so there is nothing to optimize
there. It's still worth checking (see mas_validation_stats.csv, unaffected by
this script), but only weighted kappa is a function of the thresholds being
fit here.

Guardrails against overfitting a threshold table to a handful of points:
  - refuses to fit unless there are >= MIN_TOTAL_N paired observations
  - refuses to fit unless >= MIN_GRADES_REPRESENTED distinct MAS grades each
    have >= MIN_PER_GRADE observations
  - reports leave-one-out cross-validated kappa for the fitted thresholds
    alongside the OLD (current, un-fit) thresholds' kappa on this same data
    -- that comparison is apples-to-apples-honest: the old thresholds were
    never fit on this data, so their kappa here is already an out-of-sample-
    style estimate, same spirit as the new thresholds' LOOCV kappa. A
    train/validation split isn't used because it would waste data this
    script will realistically run on for a long while at n in the dozens.

Run once mas_validation.py reports enough spread across grades:
    .venv\\Scripts\\python.exe fit_mas_thresholds.py
"""
from __future__ import annotations

import itertools
import os

import numpy as np
from sklearn.metrics import cohen_kappa_score

import pendulastic_pt_score as pt
import mas_validation as mv

MIN_TOTAL_N = 15
MIN_PER_GRADE = 3
MIN_GRADES_REPRESENTED = 4
GRID_POINTS = 20   # candidate cut points per fit; fixed, independent of n
N_THRESHOLDS = len(pt.MAS_ORDER) - 1   # 5 boundaries partition 6 grades


# ══════════════════════════════════════════════════════════════════════════
# Pure functions (unit-testable, no I/O)
# ══════════════════════════════════════════════════════════════════════════

def check_sample_sufficiency(pairs):
    """Returns (ok: bool, report: list[str]). Never raises -- the caller
    decides whether to proceed based on `ok`."""
    report = []
    n = len(pairs)
    report.append(f"n = {n} paired observations (need >= {MIN_TOTAL_N})")
    ok = n >= MIN_TOTAL_N

    counts = {g: 0 for g in pt.MAS_ORDER}
    for p in pairs:
        counts[p["mas_grade"]] += 1
    represented = [g for g in pt.MAS_ORDER if counts[g] >= MIN_PER_GRADE]
    report.append(f"grades with >= {MIN_PER_GRADE} observations: "
                  f"{represented or '(none)'} (need >= {MIN_GRADES_REPRESENTED} such grades)")
    ok = ok and len(represented) >= MIN_GRADES_REPRESENTED

    thin = [g for g in pt.MAS_ORDER if 0 < counts[g] < MIN_PER_GRADE]
    if thin:
        report.append(f"grades with some data but below the per-grade minimum: {thin}")
    missing = [g for g in pt.MAS_ORDER if counts[g] == 0]
    if missing:
        report.append(f"grades with zero observations so far: {missing}")

    return ok, report


def _predict_rank(pt_score, thresholds):
    """Mirrors pt_to_mas()'s own boundary semantics exactly (`<=` -> lower
    grade) so a fitted threshold set is a true drop-in replacement."""
    for i, t in enumerate(thresholds):
        if pt_score <= t:
            return i
    return len(thresholds)


def _kappa_for_thresholds(pairs, thresholds):
    actual_ranks = [mv.MAS_RANK[p["mas_grade"]] for p in pairs]
    predicted_ranks = [_predict_rank(p["pt_score"], thresholds) for p in pairs]
    return cohen_kappa_score(actual_ranks, predicted_ranks,
                             labels=list(range(len(pt.MAS_ORDER))), weights="linear")


def _candidate_cuts(pt_scores, grid_points=GRID_POINTS):
    lo, hi = float(np.min(pt_scores)), float(np.max(pt_scores))
    if lo == hi:
        return np.array([lo])
    return np.linspace(lo, hi, grid_points)


def fit_thresholds(pairs, grid_points=GRID_POINTS):
    """Exhaustive search over ordered combinations of candidate cut points
    for the best-within-grid-resolution weighted kappa. Returns
    (best_thresholds: tuple[float, ...] | None, best_kappa: float)."""
    pt_scores = np.array([p["pt_score"] for p in pairs])
    candidates = _candidate_cuts(pt_scores, grid_points)
    if len(candidates) < N_THRESHOLDS:
        return None, float("nan")

    best_thresholds, best_kappa = None, -1.0
    for combo in itertools.combinations(candidates, N_THRESHOLDS):
        k = _kappa_for_thresholds(pairs, combo)
        if k > best_kappa:
            best_kappa, best_thresholds = k, combo
    return best_thresholds, best_kappa


def loocv_kappa(pairs, grid_points=GRID_POINTS):
    """Leave-one-out cross-validated weighted kappa for the *fitting
    procedure* (not one fixed threshold set): refit on n-1 points, predict
    the held-out point, pool every fold's held-out prediction into one
    confusion matrix, and score that. More honest than in-sample kappa at
    these sample sizes, without sacrificing data to a held-out split."""
    held_out_actual, held_out_predicted = [], []
    for i in range(len(pairs)):
        train = pairs[:i] + pairs[i + 1:]
        thresholds, _ = fit_thresholds(train, grid_points)
        if thresholds is None:
            continue
        held_out = pairs[i]
        held_out_actual.append(mv.MAS_RANK[held_out["mas_grade"]])
        held_out_predicted.append(_predict_rank(held_out["pt_score"], thresholds))
    if len(held_out_actual) < 2:
        return float("nan")
    return cohen_kappa_score(held_out_actual, held_out_predicted,
                             labels=list(range(len(pt.MAS_ORDER))), weights="linear")


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════

def main():
    if not os.path.isfile(mv.MAS_CSV):
        print(f"{mv.MAS_CSV} not found -- run mas_validation.py first for setup instructions.")
        return

    raw_rows = mv.load_mas_scores(mv.MAS_CSV)
    paired = mv.pair_pt_and_mas(raw_rows, mv._pt_lookup_factory())
    valid = [p for p in paired if "_skip_reason" not in p]
    for row in paired:
        if "_skip_reason" in row:
            print(f"Skipping P{row.get('participant')} {row.get('leg')}: {row['_skip_reason']}")

    print("\n--- Sample sufficiency check ---")
    ok, report = check_sample_sufficiency(valid)
    for line in report:
        print(" ", line)
    if not ok:
        print("\nNot enough data to fit thresholds responsibly yet -- refusing to fit.")
        print("Add more mas_scores.csv rows covering the gaps above and re-run.")
        return

    old_thresholds = tuple(t for t, _ in pt._MAS)
    old_kappa = _kappa_for_thresholds(valid, old_thresholds)

    new_thresholds, new_kappa = fit_thresholds(valid)
    cv_kappa = loocv_kappa(valid)

    print("\n--- Threshold fit ---")
    print(f"Current _MAS thresholds: {[round(t, 4) for t in old_thresholds]}"
         f"  -> kappa on this data = {old_kappa:.3f}")
    print(f"Newly fitted thresholds: {[round(t, 4) for t in new_thresholds]}"
         f"  -> in-sample kappa = {new_kappa:.3f}, leave-one-out kappa = {cv_kappa:.3f}")
    print("(Spearman's rho does not depend on thresholds -- see mas_validation_stats.csv.)")

    print("\n--- Candidate _MAS block for pendulastic_pt_score.py ---")
    labels = pt.MAS_ORDER[:-1]
    print("_MAS = [" + ", ".join(f"({t:.4f},{l!r})" for t, l in zip(new_thresholds, labels)) + "]")

    if np.isnan(cv_kappa) or cv_kappa < old_kappa:
        print("\nNote: the fitted thresholds' leave-one-out kappa is not better than the current")
        print("thresholds' kappa on this data -- the fit likely doesn't generalize yet. Treat this")
        print("as informational, not a recommendation to replace _MAS, until more data comes in.")


if __name__ == "__main__":
    main()
