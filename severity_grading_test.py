"""Can the app's own code grade severity, and tell healthy from affected?

The question, and why it needs a harness
----------------------------------------
The app computes seven Popovic parameters, sums them into PT7, and maps PT7 to
a MAS grade. Nobody has ever checked whether that mapping RANKS severity,
because the cohort cannot check it: every clinically graded leg on file sits at
MAS 0 or 1, so there is no severe group to compare a mild one against. A
grading claim therefore has to be tested somewhere the severity is known.

This harness tests it in two places and keeps them separate, because they
answer different questions and have different weaknesses:

  MODEL ARM. Score the archetypes from rig_neuromuscular_model, where the
    intended severity and the MECHANISM are both known by construction. This
    is the only arm that can reach MAS 2-4 today. Its weakness is obvious: the
    severities are intended, not clinician-graded, so this arm can show the
    scoring INVERTS or CONFLATES, which is a falsification and stands on its
    own, but it cannot show the scoring is right.

  COHORT ARM. Score the real OptiTrack trials against clinical MAS. This is
    the arm with real limbs in it, and all it can test is healthy versus
    affected -- the severity axis is absent from the data. It only runs where
    the recordings are, so in a clean checkout it reports that and stops.

Nothing here reimplements the scoring. Every number comes through
pendulastic_pt_score.compute_pt_params -> compute_pt_score -> mas_estimate,
which is the same path the app uses, so a finding here is a finding about the
shipped code and not about a reimplementation of it.

Why `mas_estimate` and not `pt_to_mas`
--------------------------------------
Because the refusal is part of the answer. `mas_estimate` declines to return a
grade when the excursion gate says PT7 cannot support one, and a grader that
refuses precisely on the most affected limbs is not a grader -- it is a
healthy-leg detector with a disclaimer. So the refusal RATE is reported as a
first-class result next to the correlations, and a run where severity
correlates beautifully on the trials that were graded is reported as a failure
if the severe ones were all refused.

Run:  python severity_grading_test.py              # model arm, plus cohort if present
      python severity_grading_test.py --cohort     # cohort arm only
      python severity_grading_test.py --csv out.csv
"""
from __future__ import annotations

import argparse
import csv as _csv
import math
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import numpy as np

import pendulastic_pt_score as pts
import pt_cohort_common as coh          # cliffs_delta, mann_whitney, effect_label
import rig_neuromuscular_model as rig

#: Every quantity the swing produces, scored and diagnostic alike. The
#: diagnostics are here because the question is which metric carries severity,
#: and three of the strongest candidates in the literature -- first-flexion
#: amplitude, the plateau angle, peak velocity -- are NOT among the seven the
#: score is built from.
SCORED = ["R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n", "f",
          "area_ratio"]
DIAGNOSTIC = ["A0_deg", "A1_deg", "first_trough_depth", "neutral_deg",
              "omega_peak_deg_s",
              # The absolute goniometric areas, which compute_pt_params already
              # computes and nothing scores. Included on a specific published
              # recommendation: Hernot and Le Cavorzin et al., computing reflex
              # torque in 15 spastic and 10 healthy subjects, found the
              # relaxation index showed "an unsuitable U-shaped pattern of
              # variation with increasing reflex-mediated torque" while "the
              # area under the curve revealed a linear regression", and
              # concluded RI "would benefit to be replaced by more valid
              # measures, such as the area under the goniometric curve". Our
              # `area_ratio` is the RATIO of the two halves, which is a shape
              # measure; P_total is the absolute area they mean.
              "P_total", "P_plus", "P_minus"]
METRICS = SCORED + DIAGNOSTIC

#: Published discrimination for the metrics that have one, for context on any
#: number this harness produces. Whelan et al. 2018, 131 spastic knees; see
#: docs/reference/2026-08-24-pendulum-test-literature-benchmarks.md. The names
#: are THEIRS mapped onto ours and the mapping is approximate where noted --
#: quoted so a result here can be read as better or worse than the field, not
#: as a number floating free.
PUBLISHED_AUC = {
    "first_trough_depth": (0.807, "F1amp, first flexion amplitude"),
    "R2n":                (0.784, "relaxation index -- formula differs from ours"),
    "neutral_deg":        (0.691, "plateau / resting angle"),
    "N":                  (0.665, "Ncyc -- their weakest, ANOVA p = 0.594"),
}
#: Whelan's severity-GRADING ceiling: across MAS grades every metric landed in
#: this band and they judged none acceptable. Any severity claim below this is
#: not an improvement on the published state of the art.
PUBLISHED_SEVERITY_AUC_BAND = (0.59, 0.71)


# ── The production path, called once, in one place ───────────────────────────

def score_waveform(t, angle) -> dict:
    """Params, PT7 and the app's verdict for one knee-angle trace.

    Returns {"params", "pt7", "mas", "interpretable", "reason"}. `params` is
    None and `interpretable` False when the trial could not be scored at all,
    which is a different failure from being scored and then refused -- and the
    two get counted separately below, because "we could not measure this" and
    "we measured this and decline to grade it" are different findings.
    """
    params = pts.compute_pt_params(np.asarray(t, float), np.asarray(angle, float))
    est = pts.mas_estimate(params)
    return {"params": params, "pt7": est["pt7"], "mas": est["mas"],
            "interpretable": est["interpretable"], "reason": est["reason"],
            "scorable": params is not None}


def spearman(xs, ys) -> float:
    """Rank correlation over the pairs where both values are finite."""
    pairs = [(x, y) for x, y in zip(xs, ys)
             if x is not None and y is not None
             and isinstance(x, (int, float)) and isinstance(y, (int, float))
             and math.isfinite(x) and math.isfinite(y)]
    if len(pairs) < 3:
        return float("nan")

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = 0.5 * (i + j) + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    xv = [p[0] for p in pairs]; yv = [p[1] for p in pairs]
    rx, ry = ranks(xv), ranks(yv)
    n = len(pairs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx)
                    * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


def auc(pos, neg) -> float:
    """P(a random `pos` exceeds a random `neg`), ties at half.

    Same formula as generate_literature_comparison_figures.auc; restated here
    rather than imported because that module imports matplotlib at module
    scope and this harness has to run headless on a machine with no display.
    """
    pos = [v for v in pos if v is not None and np.isfinite(v)]
    neg = [v for v in neg if v is not None and np.isfinite(v)]
    if not pos or not neg:
        return float("nan")
    w = sum(1 for a in pos for b in neg if a > b)
    ties = sum(1 for a in pos for b in neg if a == b)
    return (w + 0.5 * ties) / (len(pos) * len(neg))


def monotone_shape(vals) -> str:
    """Whether a metric could carry a severity threshold at all.

    A metric that reverses cannot: two different severities read the same
    value, so no cut on it separates them, however large its correlation.
    """
    clean = [v for v in vals if v is not None and math.isfinite(v)]
    if len(clean) < 3:
        return "n/a"
    ups = sum(1 for a, b in zip(clean, clean[1:]) if b > a)
    downs = sum(1 for a, b in zip(clean, clean[1:]) if b < a)
    if downs == 0:
        return "monotone up"
    if ups == 0:
        return "monotone down"
    turns = [i for i in range(1, len(clean) - 1)
             if (clean[i] - clean[i - 1]) * (clean[i + 1] - clean[i]) < 0]
    return f"NON-MONOTONE (turns at {turns[0]})" if turns else "NON-MONOTONE"


# ── Model arm ────────────────────────────────────────────────────────────────

def model_arm(verbose: bool = True) -> dict:
    """Score every rig archetype and ask three questions of the result."""
    rows = []
    for key, prof in rig.PROFILES.items():
        sim = rig.simulate(prof)
        res = score_waveform(sim["t"], sim["angle_deg"])
        p = res["params"] or {}
        rows.append({
            "profile": key,
            "intent": rig.SEVERITY_INTENT.get(key),
            "pt7": res["pt7"],
            "mas": res["mas"],
            "interpretable": res["interpretable"],
            "scorable": res["scorable"],
            "reason": res["reason"],
            **{m: p.get(m) for m in METRICS},
        })

    if verbose:
        print("=" * 100)
        print("MODEL ARM -- the app's scoring, run on waveforms whose severity "
              "and mechanism are known")
        print("=" * 100)
        print(f"{'profile':22s} {'int':>4s} {'A0':>6s} {'plateau':>8s} "
              f"{'ftd':>6s} {'N':>5s} {'PT7':>7s} {'grade':>6s}  note")
        print("-" * 100)
        for r in rows:
            intent = "-" if r["intent"] is None else str(r["intent"])
            grade = r["mas"] if r["mas"] is not None else "REFUSED"
            note = "" if r["interpretable"] else r["reason"].split(".")[0]
            def f(v, w=6, d=1):
                return f"{v:{w}.{d}f}" if isinstance(v, float) and math.isfinite(v) else f"{'--':>{w}s}"
            print(f"{r['profile']:22s} {intent:>4s} {f(r['A0_deg'])} "
                  f"{f(r['neutral_deg'],8)} {f(r['first_trough_depth'])} "
                  f"{f(r['N'],5,2)} {f(r['pt7'],7,3)} {grade:>6s}  {note[:34]}")

    rows_by_profile = {r["profile"]: r for r in rows}
    graded = [r for r in rows if r["intent"] is not None]
    graded.sort(key=lambda r: (r["intent"], r["profile"]))

    # Q1. Does anything track the intended severity?
    #
    # Two columns, because they are two questions and the first draft of this
    # ran them together and got the second one wrong. `rho_all` is over every
    # graded profile including the three that share intent rank 3, which is the
    # realistic mixture. The SHAPE test cannot be run on that mixture: three
    # profiles at the same intended severity differ from each other, and a
    # sequence that steps sideways registers as a turn, so every metric came
    # back "NON-MONOTONE" for a reason that had nothing to do with severity.
    # Shape is therefore measured on the TONE LADDER alone -- L0 to L4, one
    # profile per level, one mechanism -- which is the only pure severity axis
    # in the library. Within-level disagreement is Q2's job, not this one.
    intents = [r["intent"] for r in graded]
    ladder = [rows_by_profile[f"tone_L{i}"] for i in range(5)
              if f"tone_L{i}" in rows_by_profile]
    ladder_rank = [rig.SEVERITY_INTENT[r["profile"]] for r in ladder]
    q1 = {}
    for m in ["pt7"] + METRICS:
        q1[m] = {
            "rho_all": spearman(intents, [r[m] for r in graded]),
            "rho_ladder": spearman(ladder_rank, [r[m] for r in ladder]),
            "shape": monotone_shape([r[m] for r in ladder]),
        }
    if verbose:
        print()
        print("Q1  Does the metric track the INTENDED severity?")
        print(f"{'metric':22s} {'rho all':>8s} {'rho ladder':>11s}  "
              f"shape on the ladder")
        print("-" * 78)
        for m, d in sorted(q1.items(), key=lambda kv: -abs(
                kv[1]["rho_ladder"] if kv[1]["rho_ladder"] == kv[1]["rho_ladder"]
                else 0)):
            tag = ""
            if m in PUBLISHED_AUC:
                tag = f"   [pub AUC {PUBLISHED_AUC[m][0]:.3f}]"
            print(f"{m:22s} {d['rho_all']:8.2f} {d['rho_ladder']:11.2f}  "
                  f"{d['shape']}{tag}")
        print("  'rho all' spans the 7 graded profiles, three of which share "
              "intent 3 and")
        print("  differ only in mechanism. 'ladder' is tone_L0..L4 alone: one "
              "mechanism,")
        print("  one profile per level, which is the only pure severity axis "
              "here. A large")
        print("  rho on a NON-MONOTONE metric is still not a severity "
              "measure -- two")
        print("  different severities read the same value, so no cut "
              "separates them.")

    # Q2. Do two legs at the SAME intended grade score the same?
    #
    # This is the question the whole mechanism parameterisation exists for, and
    # the one a cohort can never answer. Three profiles share intent rank 3 and
    # differ only in HOW the limb is affected.
    by_intent = {}
    for r in graded:
        by_intent.setdefault(r["intent"], []).append(r)
    q2 = []
    for intent, group in sorted(by_intent.items()):
        if len(group) < 2:
            continue
        vals = [r["pt7"] for r in group if r["pt7"] is not None
                and math.isfinite(r["pt7"])]
        q2.append({
            "intent": intent,
            "profiles": [r["profile"] for r in group],
            "pt7": vals,
            "spread": (max(vals) - min(vals)) if len(vals) >= 2 else float("nan"),
            "ratio": (max(vals) / min(vals)) if len(vals) >= 2 and min(vals) > 0
                     else float("nan"),
            "grades": [r["mas"] if r["mas"] else "REFUSED" for r in group],
        })
    if verbose and q2:
        print()
        print("Q2  Do profiles at the SAME intended severity get the same "
              "score?")
        for g in q2:
            print(f"  intent {g['intent']}:  " + "   ".join(
                f"{n}={v:.3f}" for n, v in zip(g["profiles"], g["pt7"])))
            print(f"            spread {g['spread']:.3f}  "
                  f"ratio {g['ratio']:.1f}x   grades "
                  f"{', '.join(g['grades'])}")
        print("  A score that is a severity measure cannot do this. A spread "
              "here means")
        print("  PT7 is reading the KIND of impairment, not how much of it "
              "there is.")

    # Q4. The signal-to-confound ratio, which is the whole answer in one number.
    #
    # Q1 says PT7 is monotone on the ladder and Q2 says it differs 8-fold
    # between mechanisms at one severity. Those two facts only mean something
    # together: a severity measure has to respond MORE to severity than to
    # anything else. So divide the range PT7 covers across the full severity
    # ladder by the largest spread it shows among profiles at a single
    # severity. Below 1 the metric is reading the confound.
    q4 = {}
    for m in ["pt7"] + METRICS:
        lv = [r[m] for r in ladder
              if isinstance(r[m], float) and math.isfinite(r[m])]
        sev_range = (max(lv) - min(lv)) if len(lv) >= 2 else float("nan")
        worst = float("nan")
        for group in by_intent.values():
            if len(group) < 2:
                continue
            gv = [r[m] for r in group
                  if isinstance(r[m], float) and math.isfinite(r[m])]
            if len(gv) >= 2:
                s = max(gv) - min(gv)
                worst = s if worst != worst else max(worst, s)
        q4[m] = {"severity_range": sev_range, "mechanism_spread": worst,
                 "ratio": (sev_range / worst) if worst == worst and worst > 0
                          else float("inf")}
    if verbose:
        print()
        print("Q4  Signal against confound: does the metric respond MORE to "
              "severity")
        print("    than to a change of mechanism at the same severity?")
        print(f"{'metric':22s} {'severity':>9s} {'mechanism':>10s} "
              f"{'ratio':>7s}  verdict")
        print("-" * 72)
        for m, d in sorted(q4.items(), key=lambda kv: -(
                kv[1]["ratio"] if kv[1]["ratio"] == kv[1]["ratio"] else -1)):
            v = ("severity dominates" if d["ratio"] >= 2.0
                 else "comparable" if d["ratio"] >= 1.0
                 else "CONFOUND DOMINATES")
            print(f"{m:22s} {d['severity_range']:9.3f} "
                  f"{d['mechanism_spread']:10.3f} {d['ratio']:7.2f}  {v}")
        print("  'severity' is the range over tone_L0..L4, the full dialled "
              "range. ")
        print("  'mechanism' is the largest spread among profiles sharing one "
              "intended")
        print("  severity. A ratio under 1 means the metric moves further when "
              "the KIND")
        print("  of impairment changes than when its AMOUNT goes from none to "
              "severe.")

    # Q5. What grade does the app actually print along the ladder?
    #
    # The correlations above can all be excellent while the output is one
    # grade for every severity, because a monotone metric compressed inside a
    # single threshold band produces a constant verdict.
    if verbose:
        print()
        print("Q5  The grade the app would actually print, along the ladder")
        cells = []
        for r in ladder:
            g = r["mas"] if r["mas"] else "REFUSED"
            cells.append(f"L{rig.SEVERITY_INTENT[r['profile']]}={g}")
        print("  " + "   ".join(cells))
        distinct = {r["mas"] for r in ladder if r["mas"]}
        print(f"  {len(distinct)} distinct grade(s) across 5 dialled "
              f"severities: {', '.join(sorted(distinct)) or '(none)'}")
        print(f"  PT7 thresholds in play: 0 if <= 0.12, 1 if <= 0.28, "
              f"1+ if <= 0.44, 2 if <= 0.60, 3 if <= 0.78, else 4")

    # Q3. Who gets refused, and is it the affected ones?
    refused = [r for r in rows if not r["interpretable"]]
    refused_graded = [r for r in graded if not r["interpretable"]]
    worst = [r for r in graded if r["intent"] is not None and r["intent"] >= 3]
    q3 = {
        "n": len(rows),
        "n_unscorable": sum(1 for r in rows if not r["scorable"]),
        "n_refused": len(refused),
        "refused": [r["profile"] for r in refused],
        "refused_frac_graded": (len(refused_graded) / len(graded)) if graded else float("nan"),
        "refused_frac_worst": (sum(1 for r in worst if not r["interpretable"])
                               / len(worst)) if worst else float("nan"),
    }
    if verbose:
        print()
        print("Q3  The refusal rate, which is part of the answer and not a "
              "footnote")
        print(f"  {q3['n_refused']} of {q3['n']} profiles get no grade: "
              f"{', '.join(q3['refused']) if q3['refused'] else '(none)'}")
        print(f"  among the graded profiles:        "
              f"{100*q3['refused_frac_graded']:.0f}% refused")
        print(f"  among intended severity 3 and 4:  "
              f"{100*q3['refused_frac_worst']:.0f}% refused")

    return {"rows": rows, "q1": q1, "q2": q2, "q3": q3, "q4": q4}


# ── Cohort arm ───────────────────────────────────────────────────────────────

def cohort_arm(verbose: bool = True) -> dict:
    """Score the real OptiTrack trials against clinical MAS, with a funnel.

    The funnel is the point of the structure. An earlier run of this analysis
    produced an empty table and the emptiness said nothing about where the
    rows went -- discovery, the MAS join, the condition filter and the scorer
    can each silently drop everything, and they fail identically from the
    outside. So every stage counts what it dropped and the count is printed
    whether or not anything survives.
    """
    import pt_report_common as prc
    import spasticity_grouping as sg

    funnel = []

    def stage(name, n, note=""):
        funnel.append({"stage": name, "n": n, "note": note})
        return n

    if not os.path.isdir(prc.OPTI_ROOT):
        if verbose:
            print("=" * 100)
            print("COHORT ARM -- not available in this checkout")
            print("=" * 100)
            print(f"  {prc.OPTI_ROOT} does not exist. The recordings are "
                  f"gitignored live data,")
            print("  so this arm only runs on a machine that has them. "
                  "Nothing is inferred")
            print("  from their absence.")
        return {"available": False, "funnel": funnel, "rows": []}

    trials = prc.discover_all_trials()
    stage("discovered trials", len(trials))

    mas = sg.load_mas_by_leg(condition_prefix="pre")
    stage("legs with a pre-timepoint clinical MAS", len(mas))

    pre = [r for r in trials if str(r["condition"]).lower().startswith("pre")
           or r["condition"] == "default"]
    stage("trials at the pre timepoint", len(pre),
          "condition starts with 'pre', or is the single-session 'default'")

    joined = [r for r in pre if (str(r["participant"]), r["leg"]) in mas]
    stage("trials whose leg has a clinical MAS", len(joined))

    rows, unscorable, refused = [], 0, 0
    for rec in joined:
        try:
            t, ang = pts.load_optitrack(rec["path"])
        except Exception as exc:                      # noqa: BLE001
            unscorable += 1
            funnel.append({"stage": "load failed", "n": 1,
                           "note": f"{os.path.basename(rec['path'])}: {exc}"})
            continue
        res = score_waveform(t, ang)
        if not res["scorable"]:
            unscorable += 1
            continue
        if not res["interpretable"]:
            refused += 1
        grade = mas[(str(rec["participant"]), rec["leg"])]
        p = res["params"]
        rows.append({
            "participant": rec["participant"], "leg": rec["leg"],
            "condition": rec["condition"], "trial": rec["trial"],
            "mas": grade, "mas_rank": pts.MAS_RANK.get(sg.mas_level(grade) or grade),
            "spastic": (pts.MAS_RANK.get(grade, 0) > 0),
            "pt7": res["pt7"], "app_grade": res["mas"],
            "interpretable": res["interpretable"],
            **{m: p.get(m) for m in METRICS},
        })
    stage("trials the scorer could measure", len(rows),
          f"{unscorable} unscorable")
    stage("of those, trials the app will GRADE", len(rows) - refused,
          f"{refused} refused by the excursion gate")

    if verbose:
        print("=" * 100)
        print("COHORT ARM -- the app's scoring on real limbs with clinical MAS")
        print("=" * 100)
        for f in funnel:
            print(f"  {f['n']:6d}  {f['stage']:46s} {f['note']}")

    out = {"available": True, "funnel": funnel, "rows": rows,
           "n_unscorable": unscorable, "n_refused": refused}
    if not rows:
        if verbose:
            print("  No scored rows. The funnel above says which stage emptied "
                  "it -- read")
            print("  the first line whose count is zero, not this message.")
        return out

    # Healthy vs affected. This is all the data can test: a severity axis
    # needs more than one affected grade, and this cohort has MAS 0 and 1.
    heal = [r for r in rows if not r["spastic"]]
    spas = [r for r in rows if r["spastic"]]
    grades = sorted({r["mas"] for r in rows}, key=lambda g: pts.MAS_RANK.get(g, 99))
    disc = {}
    for m in ["pt7"] + METRICS:
        a = [r[m] for r in heal]
        b = [r[m] for r in spas]
        # cliffs_delta(a, b) is P(b>a) - P(a>b): healthy FIRST, so a positive
        # delta means the affected legs read HIGHER. Passing them the other way
        # round silently inverts the reported direction of every effect.
        disc[m] = {
            "auc": auc(b, a),
            "delta": coh.cliffs_delta(a, b),
            "p": coh.mann_whitney(np.asarray(a, float), np.asarray(b, float))[1],
            "n_healthy": len(a), "n_spastic": len(b),
        }
    if verbose:
        print()
        print(f"Healthy vs affected  ({len(heal)} healthy trials, "
              f"{len(spas)} affected; grades present: {', '.join(grades)})")
        print(f"{'metric':22s} {'AUC':>6s} {'delta':>7s} {'p':>8s}  "
              f"published")
        print("-" * 74)
        for m, d in sorted(disc.items(), key=lambda kv: -(
                kv[1]["auc"] if kv[1]["auc"] == kv[1]["auc"] else 0)):
            pub = f"{PUBLISHED_AUC[m][0]:.3f}  {PUBLISHED_AUC[m][1]}" \
                if m in PUBLISHED_AUC else ""
            print(f"{m:22s} {d['auc']:6.3f} {d['delta']:7.2f} {d['p']:8.4f}  {pub}")
        lo, hi = PUBLISHED_SEVERITY_AUC_BAND
        print(f"  Severity grading is NOT tested here and cannot be: it needs "
              f"more than")
        print(f"  one affected grade. The published severity ceiling is AUC "
              f"{lo}-{hi} and")
        print(f"  the authors judged none of it acceptable.")

    out["discrimination"] = disc
    return out


def write_csv(path, model, cohort):
    """One flat file, both arms, with an `arm` column. Written so the numbers
    can be checked by hand against the printed tables rather than taken from
    them."""
    cols = (["arm", "label", "intent_or_mas", "pt7", "app_grade",
             "interpretable"] + METRICS)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(cols)
        for r in model["rows"]:
            w.writerow(["model", r["profile"], r["intent"], r["pt7"],
                        r["mas"], r["interpretable"]]
                       + [r.get(m) for m in METRICS])
        for r in cohort.get("rows", []):
            w.writerow(["cohort",
                        f"{r['participant']}_{r['leg']}_{r['condition']}_T{r['trial']}",
                        r["mas"], r["pt7"], r["app_grade"], r["interpretable"]]
                       + [r.get(m) for m in METRICS])
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cohort", action="store_true",
                    help="run the cohort arm only")
    ap.add_argument("--model", action="store_true",
                    help="run the model arm only")
    ap.add_argument("--csv", metavar="PATH", help="write both arms to a CSV")
    args = ap.parse_args()
    both = not (args.cohort or args.model)

    model = {"rows": [], "q1": {}, "q2": [], "q3": {}}
    cohort = {"available": False, "rows": []}
    if both or args.model:
        model = model_arm()
        print()
    if both or args.cohort:
        cohort = cohort_arm()

    if args.csv:
        print()
        print(f"wrote {write_csv(args.csv, model, cohort)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
