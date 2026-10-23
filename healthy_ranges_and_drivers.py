"""Healthy range per metric, and which metric actually separates spasticity.

Answers three questions from the swings already on disk:

  1. What is the healthy range of each metric?
  2. Do spastic legs always fall outside it, and healthy legs never?
  3. Which metric separates best -- i.e. what should a severity screen key on?

Four decisions that make the answer honest, each forced by something this
repository already established:

LEGS, NOT TRIALS. The unit is one (participant, leg), the median across that
leg's trials. MAS is assessed per leg and several participants contribute two
legs with different tone (P4 is MAS 0 left, 1+ right), so pooling trials would
count one leg five times and call it five observations.

ONE CONDITION. Only `pre` trials, because KD13 settled that a grade assessed in
one condition is not evidence about another: carrying a label across pre/post is
what put a 6.76 deg month-post trial forward as the lowest spastic reading when
its MAS was assessed at pre.

PROVENANCE TIERS. spasticity_grouping.py records where every label came from,
and KD7/R17 restrict a clinical claim to clinician-examined legs. Three tiers
are reported so the reader can see how much the answer depends on weaker labels:

    strict      clinician examination only (SRC_CLINICAL,
                SRC_CLINICAL_COMPONENT) -- the only tier carrying a claim
    reference   strict + control-by-recruitment, because an unaffected control
                is non-spastic by design and a healthy range needs more than
                the handful of examined MAS-0 legs KD8 counted
    exploratory + assumed and operator-set grades, labelled as such

NO A0-DERIVED LABELS, EVER. spasticity_grouping can label a leg from its own
swing amplitude when no MAS exists. Testing whether A0 -- or any of the four
parameters that divide by A0 -- separates spasticity would then be circular:
the label came from the measurement. `a0_deg` is deliberately never passed, so
those legs come back `unknown` and are excluded rather than silently included.
The module's own comment makes the same point.

Reliability context, if a test-retest CSV is supplied, is joined per metric so
a separation smaller than the measurement error is reported as such. A healthy
band narrower than its own SEM cannot screen anything, however clean the
p-value looks.

    python healthy_ranges_and_drivers.py CACHE.json [--retest RETEST.csv]

CACHE.json is a per-trial parameter cache written by test_retest_reliability.py
(Model_Analysis_Outputs/_cache/test_retest_params_<fingerprint>.json).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

METRICS = ["R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n", "f",
           "area_ratio", "A0_deg", "omega_peak_deg_s",
           # Not among the seven scored parameters, and that is the point:
           # Whelan's best classifier is built from these two.
           "first_trough_depth", "neutral_deg", "A1_deg"]

# Published discrimination for MAS 0 vs MAS>0 (knee extensors), from
# Whelan A, Sexton A, Jones M, O'Connell C, McGibbon CA (2018), J NeuroEng
# Rehabil -- 131 knees from 93 patients across ABI/MS/CP/SCI, full clinician
# MAS, AUC with 95% CI. Transcribed from
# docs/reference/2026-08-24-pendulum-test-literature-benchmarks.md.
#
# The mapping is by METRIC FAMILY, not by formula, and that caveat is not
# decorative: their RI is F1amp/Plateau while our R2n is A1/(1.6*A0), so the
# AUCs are comparable as "how well does this family separate" and the absolute
# values are not comparable at all (that document's section 6 says so
# explicitly). Printed beside our own AUC so a disagreement is visible rather
# than averaged away.
PUBLISHED_AUC = {
    "first_trough_depth": (0.807, "0.729-0.885", "F1amp, 1st flexion amplitude"),
    "R2n":                (0.784, "0.698-0.869", "RI family (formula differs)"),
    "neutral_deg":        (0.691, "0.599-0.783", "plateau / resting angle"),
    "N":                  (0.665, "0.567-0.762", "Ncyc -- their WORST, ANOVA p=0.594"),
}

# Their Model 2 -- grading severity among spastic knees (MAS 1,1+ vs 2,3) --
# found every metric in 0.59-0.71 and none acceptable: "none of the metrics we
# analyzed were able to discriminate between knees with low/moderate and
# high/severe spasticity." So a binary screen is the supported shape and a
# graded severity index is not, which is what section 4d of that document
# concludes about our own three-zone model.
PUBLISHED_SEVERITY_CEILING = (0.59, 0.71)

# Which parameters are a ratio taken on the swing itself. Called out because
# "does the ratio rise in spastic legs" is the question this was built for, and
# because every one of them divides by A0, whose own repeatability is the worst
# in the set.
OVER_A0 = {"R2n", "phi_max_ratio", "omega_max_n", "omega_min_n"}

TIERS = ("strict", "reference", "exploratory")


def parse_key(key):
    """`pid_leg_condition_Tn` -> (pid, leg, condition). Condition may itself
    contain underscores (`post_1month`), so the split is from both ends."""
    parts = key.split("_")
    if len(parts) < 4 or not parts[-1].startswith("T"):
        return None
    return parts[0], parts[1], "_".join(parts[2:-1])


def median(vals):
    v = sorted(float(x) for x in vals if x is not None and math.isfinite(float(x)))
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def percentile(vals, q):
    v = sorted(vals)
    if not v:
        return None
    if len(v) == 1:
        return v[0]
    pos = (len(v) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def load_legs(cache_path, condition="pre"):
    """Per-leg median of each metric, from the per-trial cache."""
    with open(cache_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    raw.pop("__meta__", None)
    by_leg = {}
    skipped = 0
    for key, rec in raw.items():
        if not isinstance(rec, dict) or "error" in rec:
            skipped += 1
            continue
        parsed = parse_key(key)
        if not parsed:
            skipped += 1
            continue
        pid, leg, cond = parsed
        if cond != condition:
            continue
        by_leg.setdefault((pid, leg), []).append(rec)
    legs = {}
    for (pid, leg), recs in by_leg.items():
        row = {"pid": pid, "leg": leg, "n_trials": len(recs)}
        for m in METRICS:
            row[m] = median([r.get(m) for r in recs])
        legs[(pid, leg)] = row
    return legs, skipped


def label_legs(legs):
    """Attach a spasticity level and its provenance to every leg."""
    import pt_cohort_common as pcc
    import spasticity_grouping as sg

    mas_by_leg = sg.load_mas_by_leg(condition_prefix="pre")
    components = sg.load_mas_components_by_leg(condition_prefix="pre")
    assessors = sg.load_mas_assessors()
    registry = pcc.load_registry()
    registry_exists = bool(registry)

    for (pid, leg), row in legs.items():
        try:
            diagnosis = pcc.load_metadata_diagnosis(pid)
        except Exception:
            diagnosis = None
        arm, _ = pcc.classify_participant(pid, diagnosis, registry, registry_exists)
        # a0_deg is deliberately omitted: see the module docstring on circularity.
        lab = sg.classify_leg(pid, leg, arm=arm, mas_by_leg=mas_by_leg,
                              mas_components=components, mas_assessors=assessors)
        row["arm"] = arm
        row["level"] = lab.level
        row["source"] = lab.source
        row["detail"] = getattr(lab, "detail", "")
    return legs


def tier_members(legs, tier):
    """The legs admitted at a given provenance tier."""
    import spasticity_grouping as sg
    strict = set(sg.CLINICIAN_ASSESSED_SOURCES)
    allowed = dict(strict=strict,
                   reference=strict | {sg.SRC_CONTROL},
                   exploratory=strict | {sg.SRC_CONTROL, sg.SRC_MAS_ASSUMED,
                                         sg.SRC_MAS_OPERATOR})[tier]
    out = []
    for row in legs.values():
        if row["source"] in allowed and row["level"] in (sg.SPASTIC, sg.NON_SPASTIC):
            out.append(row)
    return out


def describe(rows, metric):
    vals = [r[metric] for r in rows if r.get(metric) is not None]
    if not vals:
        return None
    return {
        "n": len(vals), "median": median(vals),
        "p2_5": percentile(vals, 0.025), "p97_5": percentile(vals, 0.975),
        "lo": min(vals), "hi": max(vals),
    }


def screen_at(healthy, spastic, upper):
    """How a one-sided screen at `upper` performs. Direction is decided by the
    medians, not assumed: a metric that FALLS with spasticity is screened from
    below, and reported as such."""
    if upper is None:
        return None
    sens = sum(1 for v in spastic if v > upper) / len(spastic) if spastic else float("nan")
    spec = sum(1 for v in healthy if v <= upper) / len(healthy) if healthy else float("nan")
    return {"threshold": upper, "sensitivity": sens, "specificity": spec}


def lopo(rows, metric):
    """Leave-one-participant-out screen. The healthy band is rebuilt from the
    other participants every fold, so no leg is ever tested against a threshold
    its own participant helped set."""
    import spasticity_grouping as sg
    pids = sorted({r["pid"] for r in rows})
    tp = fp = tn = fn = 0
    folds = 0
    for held in pids:
        train = [r for r in rows if r["pid"] != held and r.get(metric) is not None]
        test = [r for r in rows if r["pid"] == held and r.get(metric) is not None]
        h = [r[metric] for r in train if r["level"] == sg.NON_SPASTIC]
        s = [r[metric] for r in train if r["level"] == sg.SPASTIC]
        if len(h) < 3 or not s or not test:
            continue
        rises = (median(s) or 0) > (median(h) or 0)
        bound = max(h) if rises else min(h)
        folds += 1
        for r in test:
            v = r[metric]
            flagged = v > bound if rises else v < bound
            if r["level"] == sg.SPASTIC:
                tp += flagged
                fn += not flagged
            else:
                fp += flagged
                tn += not flagged
    if not folds or (tp + fn) == 0 or (fp + tn) == 0:
        return None
    sens, spec = tp / (tp + fn), tn / (fp + tn)
    return {"folds": folds, "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "sensitivity": sens, "specificity": spec,
            "balanced_acc": 0.5 * (sens + spec)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cache")
    ap.add_argument("--retest", help="test_retest_reliability CSV, for SEM/MDC context")
    ap.add_argument("--condition", default="pre")
    args = ap.parse_args(argv)

    import pt_cohort_common as pcc
    import spasticity_grouping as sg

    legs, skipped = load_legs(args.cache, condition=args.condition)
    if not legs:
        print(f"no {args.condition} trials in {args.cache}")
        return 1
    label_legs(legs)
    print(f"{len(legs)} legs from condition={args.condition} "
          f"({skipped} cache entries skipped as unscored/unparseable)")

    retest = {}
    if args.retest:
        import csv
        with open(args.retest, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                try:
                    retest[r["parameter"]] = {"sem": float(r["sem"]),
                                              "mdc95": float(r["mdc95"])}
                except (KeyError, TypeError, ValueError):
                    continue
        print(f"reliability context for {len(retest)} parameters from {args.retest}")

    # composition
    print("\nlabel provenance (all legs)")
    counts = {}
    for row in legs.values():
        counts[(row["source"], row["level"])] = counts.get((row["source"], row["level"]), 0) + 1
    for (src, lvl), n in sorted(counts.items()):
        print(f"  {src:28s} {lvl:12s} {n:3d}")

    print("\narm x level (all legs)")
    arm_counts = {}
    for row in legs.values():
        arm_counts[(row["arm"], row["level"])] = arm_counts.get((row["arm"], row["level"]), 0) + 1
    for (arm, lvl), n in sorted(arm_counts.items()):
        print(f"  {str(arm):14s} {lvl:12s} {n:3d}")

    out_rows = []
    for tier in TIERS:
        rows = tier_members(legs, tier)
        healthy_rows = [r for r in rows if r["level"] == sg.NON_SPASTIC]
        spastic_rows = [r for r in rows if r["level"] == sg.SPASTIC]
        hp = sorted({r["pid"] for r in healthy_rows})
        spp = sorted({r["pid"] for r in spastic_rows})
        print(f"\n{'='*78}\nTIER {tier.upper()}: "
              f"{len(healthy_rows)} non-spastic legs ({len(hp)} participants), "
              f"{len(spastic_rows)} spastic legs ({len(spp)} participants)")
        if len(healthy_rows) < 3 or len(spastic_rows) < 3:
            print("  TOO FEW LEGS ON ONE SIDE -- no ranges computed for this tier.")
            print("  Why, in the order worth checking:")
            unknown = sum(1 for r in legs.values() if r["level"] not in
                          (sg.SPASTIC, sg.NON_SPASTIC))
            nodata = sum(1 for r in legs.values() if r["source"] == sg.SRC_NONE)
            print(f"    {unknown} of {len(legs)} legs have no spasticity level at all"
                  f" ({nodata} with source '{sg.SRC_NONE}')")
            mas_path = getattr(sg, "MAS_CSV", "?")
            print(f"    mas_scores.csv: {'FOUND' if os.path.exists(mas_path) else 'MISSING'}"
                  f"  ({mas_path})")
            try:
                import pt_cohort_common as _p
                reg = _p.load_registry()
                print(f"    participant registry: "
                      f"{'FOUND, ' + str(len(reg)) + ' entries' if reg else 'EMPTY or MISSING'}")
            except Exception as e:
                print(f"    participant registry: could not load ({e})")
            cache_pids = sorted({r['pid'] for r in legs.values()})
            print(f"    participant ids seen in the cache: "
                  f"{', '.join(cache_pids[:12])}{' ...' if len(cache_pids) > 12 else ''}")
            try:
                mas = sg.load_mas_by_leg(condition_prefix=args.condition)
                mas_keys = sorted({str(k) for k in list(mas)[:12]})
                print(f"    keys the MAS table is indexed by: "
                      f"{', '.join(mas_keys) if mas_keys else '(empty)'}")
                print("    If those two id formats do not match, that is the cause:")
                print("    the label lookup silently misses and every leg reads unknown.")
            except Exception as e:
                print(f"    MAS table: could not load ({e})")
            continue

        stat_rows = []
        for m in METRICS:
            h = [r[m] for r in healthy_rows if r.get(m) is not None]
            s = [r[m] for r in spastic_rows if r.get(m) is not None]
            if len(h) < 3 or len(s) < 3:
                continue
            # Argument order is load-bearing. pcc.cliffs_delta(a, b) is
            # documented as "(b > a) pairs minus (a > b) pairs", so the SECOND
            # argument is the one a positive delta says reads higher. Passing
            # (healthy, spastic) therefore makes delta > 0 mean "spastic reads
            # higher", which is what the direction column reports; passing them
            # the other way round silently inverts every direction.
            d = pcc.cliffs_delta(h, s)
            p = pcc.mann_whitney(s, h)[1]       # returns (stat, p)
            auc = 0.5 * (abs(d) + 1.0)          # probability of superiority
            # Key name is pcc._holm_adjust's contract, not a choice: it reads
            # `mann_whitney_p`, writes `holm_p`, and mutates in place rather
            # than returning the list.
            stat_rows.append({"parameter": m, "cliffs_delta": d,
                              "mann_whitney_p": p, "auc": auc})
        if stat_rows:
            pcc._holm_adjust(stat_rows)

        print(f"\n  {'parameter':<18}{'healthy median':>15}{'[min,max]':>22}"
              f"{'spastic median':>15}{'delta':>8}{'AUC':>7}{'holm p':>9}"
              f"{'published':>11}")
        for sr in stat_rows:
            m = sr["parameter"]
            H = describe(healthy_rows, m)
            S = describe(spastic_rows, m)
            band = f"[{H['lo']:.3g}, {H['hi']:.3g}]"
            holm = sr.get("holm_p") or sr.get("mann_whitney_p")
            pub = PUBLISHED_AUC.get(m)
            pub_s = f"{pub[0]:.2f}" if pub else "-"
            print(f"  {m:<18}{H['median']:>15.4g}{band:>22}{S['median']:>15.4g}"
                  f"{sr['cliffs_delta']:>8.2f}{sr['auc']:>7.2f}"
                  f"{(holm if holm is not None else float('nan')):>9.3g}"
                  f"{pub_s:>11}")

        print("\n  screening at the healthy bound, and honestly "
              "(leave-one-participant-out)")
        print("  naive specificity is near-tautological -- the threshold IS the "
              "healthy bound,\n  so every healthy leg sits inside it by "
              "construction. Read the LOPO columns.")
        print(f"  {'parameter':<18}{'dir':>5}{'naive sens':>12}{'naive spec':>12}"
              f"{'LOPO sens':>11}{'LOPO spec':>11}{'LOPO bal':>10}{'vs MDC95':>12}")
        ranked = []
        for sr in stat_rows:
            m = sr["parameter"]
            h = [r[m] for r in healthy_rows if r.get(m) is not None]
            s = [r[m] for r in spastic_rows if r.get(m) is not None]
            rises = (median(s) or 0) > (median(h) or 0)
            naive = screen_at(h, s, max(h)) if rises else None
            if not rises:
                # screen from below: mirror the values so one routine serves both
                naive = screen_at([-x for x in h], [-x for x in s], -min(h))
            lo = lopo([r for r in rows if r.get(m) is not None], m)
            sep = abs((median(s) or 0) - (median(h) or 0))
            mdc = retest.get(m, {}).get("mdc95")
            verdict = "-"
            if mdc is not None:
                verdict = "BELOW NOISE" if sep < mdc else f"{sep/mdc:.1f}x"
            row = {"tier": tier, "parameter": m, "direction": "up" if rises else "down",
                   "healthy_median": median(h), "healthy_lo": min(h), "healthy_hi": max(h),
                   "spastic_median": median(s), "cliffs_delta": sr["cliffs_delta"],
                   "auc": sr["auc"], "holm_p": sr.get("holm_p"), "mann_whitney_p": sr.get("mann_whitney_p"),
                   "naive_sensitivity": naive["sensitivity"] if naive else None,
                   "naive_specificity": naive["specificity"] if naive else None,
                   "lopo_sensitivity": lo["sensitivity"] if lo else None,
                   "lopo_specificity": lo["specificity"] if lo else None,
                   "lopo_balanced_acc": lo["balanced_acc"] if lo else None,
                   "separation": sep, "mdc95": mdc, "separation_vs_mdc95": verdict,
                   "over_a0": m in OVER_A0}
            out_rows.append(row)
            ranked.append(row)
            fmt = lambda v: f"{v:.2f}" if isinstance(v, float) else "   -"
            print(f"  {m:<18}{row['direction']:>5}"
                  f"{fmt(row['naive_sensitivity']):>12}{fmt(row['naive_specificity']):>12}"
                  f"{fmt(row['lopo_sensitivity']):>11}{fmt(row['lopo_specificity']):>11}"
                  f"{fmt(row['lopo_balanced_acc']):>10}{verdict:>12}")

        usable = [r for r in ranked if r["lopo_balanced_acc"] is not None]
        if usable:
            usable.sort(key=lambda r: -r["lopo_balanced_acc"])
            print("\n  ranked by leave-one-participant-out balanced accuracy")
            for i, r in enumerate(usable[:5], 1):
                flag = "  <-- below its own measurement error" if r["separation_vs_mdc95"] == "BELOW NOISE" else ""
                print(f"    {i}. {r['parameter']:<18} {r['lopo_balanced_acc']:.2f}"
                      f"  ({r['direction']}, AUC {r['auc']:.2f}){flag}")
            print("  0.50 balanced accuracy is chance. A metric below its own MDC95")
            print("  cannot carry a threshold whatever its accuracy here says.")
            print(f"\n  Published ceiling: for MAS 0 vs >0 the best metric reaches")
            print(f"  AUC 0.807 on 131 knees. For GRADING severity among spastic")
            print(f"  knees every published metric sat in "
                  f"{PUBLISHED_SEVERITY_CEILING[0]:.2f}-{PUBLISHED_SEVERITY_CEILING[1]:.2f}"
                  f" and none was")
            print("  acceptable -- so a binary screen is the supported shape, and a")
            print("  graded severity index is not. See the benchmarks doc, 4d.")

    if out_rows:
        import csv
        out = os.path.join(BASE, "Model_Analysis_Outputs",
                           "healthy_ranges_and_drivers.csv")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
