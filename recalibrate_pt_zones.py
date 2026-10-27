"""Re-derive PT_HEALTHY_MAX and PT_BORDERLINE_MAX from the control cohort.

    python recalibrate_pt_zones.py --opti-root /path/to/OptiTrack_Recordings \
                                   --mas-csv   /path/to/mas_scores.csv

Both thresholds are percentiles of cohort SCORES, so they are only meaningful
under the formula those scores were computed with. Any change to
`pendulastic_pt_score._PARAM_KEYS` invalidates them, and a stale pair shifts
the healthy boundary in the false-negative direction -- it reads borderline
limbs as healthy. `tests/test_pt_zone_parity.py` refuses to let zone
classification be switched on while `PT_ZONES_CALIBRATED_FOR` differs from
`SCORE_FORMULA_ID`; this script is how that is cleared.

Why a script rather than a note: this has been done by hand twice (2026-08-10
and 2026-08-24) and the arithmetic lives only in a comment, so nobody can
reproduce either number without re-deriving the method from prose. The
arithmetic is unchanged from that comment:

    PT_HEALTHY_MAX    = the MAS-0 75th percentile
    PT_BORDERLINE_MAX = midpoint between PT_HEALTHY_MAX and the MAS>=1 median

Unit of observation is the participant-LEG, not the trial: a participant with
eight usable trials would otherwise outvote one with two, and the quantity
being estimated is a property of limbs. Each leg contributes the median of its
own baseline trials.

The cohort is gitignored clinical data that was never committed to any branch,
so this cannot run in CI and cannot run in a fresh clone. It takes the paths as
arguments so nothing has to be copied into the checkout.
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys

import numpy as np

import pendulastic_pt_score as pts


def leg_scores(opti_root: str | None, mas_csv: str | None, verbose: bool = True):
    """{(participant, leg): (mas_rank, median_pt7)} over interpretable baseline trials."""
    import pt_report_common as prc
    import spasticity_grouping as sg

    if opti_root:
        prc.OPTI_ROOT = os.path.abspath(os.path.expanduser(opti_root))
    if mas_csv:
        sg.MAS_CSV = os.path.abspath(os.path.expanduser(mas_csv))
    if not os.path.isdir(prc.OPTI_ROOT):
        raise SystemExit(
            f"recordings not found at {prc.OPTI_ROOT}\n"
            f"  pass --opti-root, and --mas-csv if mas_scores.csv is elsewhere.\n"
            f"  These are gitignored live data: a fresh clone never has them."
        )

    mas = sg.load_mas_by_leg(condition_prefix="pre")
    by_leg: dict[tuple, list[float]] = {}
    by_leg_params: dict[tuple, list[dict]] = {}
    refused = unscorable = 0
    for rec in prc.discover_all_trials():
        cond = str(rec["condition"]).lower()
        if not (cond.startswith("pre") or rec["condition"] == "default"):
            continue
        key = (str(rec["participant"]), rec["leg"])
        if key not in mas:
            continue
        try:
            t, ang = pts.load_optitrack(rec["path"])
        except Exception:                                   # noqa: BLE001
            unscorable += 1
            continue
        params = pts.compute_pt_params(t, ang)
        if not params:
            unscorable += 1
            continue
        est = pts.mas_estimate(params)
        # Only interpretable trials: a refusal is not a low score, and
        # averaging one in would be reading a verdict the scorer declined to
        # give. The no-oscillation gate added 2026-10-26 refuses here too,
        # which is correct -- an arrested limb has no PT7 to percentile.
        if not est["interpretable"]:
            refused += 1
            continue
        by_leg.setdefault(key, []).append(float(est["pt7"]))
        by_leg_params.setdefault(key, []).append(params)

    out = {k: (mas[k], statistics.median(v)) for k, v in by_leg.items()}
    if verbose:
        print(f"  {len(out)} participant-legs, "
              f"{sum(len(v) for v in by_leg.values())} interpretable trials "
              f"({refused} refused, {unscorable} unscorable)")
    return out, by_leg_params


def healthy_reference(legs: dict, per_leg_params: dict, verbose: bool = True):
    """What the control cohort actually measures for each scored parameter.

    This exists for omega_max_n, and the rest come free. HEALTHY_REF's
    omega_max_n is 9.5672 and is 1.7-2.2x above every independent estimate of a
    healthy limb (emulator 5.11 with a 5.55 ceiling, anthropometry ~4.3,
    measured peak velocity ~4.76). But the penalty is one-sided, so the
    reference cannot simply be lowered to a physics value: 9.5672 is the median
    THIS pipeline produces, and if the pipeline still reads ~8.5 for healthy
    limbs then a reference of 4.76 sits below every real reading and the term
    goes inert -- a silent zero, the same failure omega_min_n had before
    a23606a, reached from the other side.

    The one number that settles it is what the control legs measure on the
    current scoring path. That is what this prints. It does NOT write anything:
    changing HEALTHY_REF changes every score, so it is a decision, and the
    decision needs to see the gap between what the cohort measures and what
    physics predicts before anyone picks a value.
    """
    import statistics as st
    by_key = {}
    for k in pts._PARAM_KEYS + pts._DIAGNOSTIC_KEYS:
        vals = [p[k] for key, plist in per_leg_params.items() if _rank(legs[key][0]) == 0
                for p in plist if k in p and np.isfinite(p[k])]
        if vals:
            by_key[k] = (st.median(vals), min(vals), max(vals), len(vals))
    if verbose and by_key:
        print()
        print("  What the MAS-0 control legs measure, against the shipped reference:")
        print(f"    {'parameter':16} {'measured median':>16} {'[min - max]':>20} "
              f"{'HEALTHY_REF':>12} {'ratio':>7}")
        for k, (med, lo, hi, n) in by_key.items():
            ref = pts.HEALTHY_REF.get(k)
            r = (med / ref) if ref not in (None, 0) else float("nan")
            star = "  <-- scored" if k in pts._PARAM_KEYS else "  (reported only)"
            print(f"    {k:16} {med:16.4f} {f'[{lo:.3f} - {hi:.3f}]':>20} "
                  f"{ref if ref is None else f'{ref:12.4f}'} {r:7.2f}{star}")
        print(f"    (n = {max(v[3] for v in by_key.values())} control trials)")
        print()
        print("    omega_max_n is the one to look at. If the measured median is")
        print("    near 9.57 the reference is self-consistent and the quantity")
        print("    itself is inflated -- fix the extraction, not the constant.")
        print("    If it is near 4.8 the constant is simply stale and can be")
        print("    replaced. Nothing is written either way.")
    return by_key


def derive(legs: dict, verbose: bool = True):
    """(PT_HEALTHY_MAX, PT_BORDERLINE_MAX) by the documented arithmetic."""
    healthy = [pt for rank, pt in legs.values() if _rank(rank) == 0]
    impaired = [pt for rank, pt in legs.values() if _rank(rank) >= 1]
    if len(healthy) < 5 or len(impaired) < 5:
        raise SystemExit(
            f"not enough legs to calibrate: {len(healthy)} at MAS 0, "
            f"{len(impaired)} at MAS>=1. Refusing to emit a threshold from this."
        )
    h_max = float(np.percentile(healthy, 75))
    b_max = float(0.5 * (h_max + statistics.median(impaired)))
    if verbose:
        print(f"  MAS 0   n={len(healthy):3d}  median {statistics.median(healthy):.4f}  "
              f"75th pct {h_max:.4f}")
        print(f"  MAS>=1  n={len(impaired):3d}  median {statistics.median(impaired):.4f}")
        print()
        print(f"  PT_HEALTHY_MAX    = {h_max:.4f}   (was {pts.PT_HEALTHY_MAX})")
        print(f"  PT_BORDERLINE_MAX = {b_max:.4f}   (was {pts.PT_BORDERLINE_MAX})")
    return h_max, b_max


def _rank(mas) -> int:
    """MAS label or rank -> ordinal. load_mas_by_leg may give either."""
    if isinstance(mas, (int, float)):
        return int(mas)
    return pts.MAS_RANK.get(str(mas).strip(), 0)


def smoke(verbose: bool = True) -> bool:
    """Run the whole thing against a synthetic corpus, so it is known to work.

    This script cannot be exercised in a clean checkout -- the recordings are
    gitignored live data -- so its first real run would otherwise be on the
    machine that has them, at the moment the answer is wanted. That is the
    worst time to find a path bug. Same reasoning, and the same tree shape, as
    severity_grading_test.smoke_cohort_arm.

    It is a PLUMBING test, not a calibration: the limbs are noiseless model
    traces, so the thresholds it prints mean nothing. What it proves is that
    discovery, the MAS join, the condition filter, the loader, the scorer and
    the percentile arithmetic all connect.
    """
    import csv as _c
    import shutil
    import tempfile
    import numpy as _np
    import pt_report_common as prc
    import spasticity_grouping as sg
    from neuromech import profiles as PR

    root = tempfile.mkdtemp(prefix="pendulastic-recal-smoke-")
    saved = (prc.OPTI_ROOT, prc.ARCHIVE_ROOT, prc.EXCLUDED_TRIALS_PATH, sg.MAS_CSV)
    try:
        opti = os.path.join(root, "OptiTrack_Recordings")
        # Six legs per arm: derive() refuses below five, and that floor is the
        # point -- a calibration from four legs is not one.
        plan = ([(str(i), leg, "0", "tone_L0") for i in range(1, 4) for leg in ("left", "right")] +
                [(str(i), leg, "2", prof) for i, prof in ((4, "tone_L2"), (5, "tone_L3"), (6, "tone_L2"))
                 for leg in ("left", "right")])
        rng = _np.random.default_rng(20261026)
        for pid, leg, _grade, prof in plan:
            d = os.path.join(opti, f"Participant_{pid}_{leg}_pre", "Session_pre",
                             "Position_1", "Height_Joint-Level")
            os.makedirs(d, exist_ok=True)
            sim = PR.simulate(PR.PROFILES[prof])
            for tr in (1, 2):
                # Motive exports FLEXION (0 = extended); the loader converts.
                flex = 180.0 - sim["angle_deg"] + rng.normal(0.0, 0.15, len(sim["angle_deg"]))
                with open(os.path.join(d, f"trial_{tr}_optitrack.csv"), "w",
                          newline="", encoding="utf-8") as fh:
                    w = _c.writer(fh)
                    w.writerow(["frame", "time_sec", "knee_angle_deg"])
                    for i, (ti, fi) in enumerate(zip(sim["t"], flex)):
                        w.writerow([i, f"{ti:.4f}", f"{fi:.4f}"])
        mas_path = os.path.join(root, "mas_scores.csv")
        with open(mas_path, "w", newline="", encoding="utf-8") as fh:
            w = _c.writer(fh)
            w.writerow(["participant", "leg", "condition", "mas_grade",
                        "mas_flexion", "mas_extension", "assessed_by"])
            for pid, leg, grade, _p in plan:
                w.writerow([pid, leg, "pre", grade, "", "", "CLINICIAN"])

        prc.OPTI_ROOT = opti
        prc.ARCHIVE_ROOT = os.path.join(root, "no-archive")
        prc.EXCLUDED_TRIALS_PATH = os.path.join(root, "excluded.json")
        sg.MAS_CSV = mas_path
        legs, per_leg_params = leg_scores(None, None, verbose=verbose)
        healthy_reference(legs, per_leg_params, verbose=verbose)
        h, b = derive(legs, verbose=verbose)
    finally:
        (prc.OPTI_ROOT, prc.ARCHIVE_ROOT, prc.EXCLUDED_TRIALS_PATH, sg.MAS_CSV) = saved
        shutil.rmtree(root, ignore_errors=True)

    ok = len(legs) == len(plan) and 0.0 < h < b
    if verbose:
        print()
        print(f"  smoke: {'PASS' if ok else 'FAIL'} -- expected {len(plan)} legs and "
              f"0 < healthy < borderline, got {len(legs)} legs, {h:.4f} < {b:.4f}")
        print("  (synthetic limbs: the THRESHOLDS above are meaningless, the plumbing is not)")
    return bool(ok)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--opti-root", help="where OptiTrack_Recordings actually is")
    ap.add_argument("--mas-csv", help="where mas_scores.csv actually is")
    ap.add_argument("--smoke", action="store_true",
                    help="run against a synthetic corpus to prove the plumbing, then exit")
    a = ap.parse_args(argv)

    if a.smoke:
        return 0 if smoke() else 1

    print(f"Re-deriving the zone thresholds for {pts.SCORE_FORMULA_ID}")
    print(f"  scored parameters ({pts._N_PARAMS}): {', '.join(pts._PARAM_KEYS)}")
    print(f"  reported, not scored: {', '.join(pts._DIAGNOSTIC_KEYS)}")
    print()
    legs, per_leg_params = leg_scores(a.opti_root, a.mas_csv)
    healthy_reference(legs, per_leg_params)
    h_max, b_max = derive(legs)
    print()
    print("  To apply, in pendulastic_pt_score.py AND mobile-imu-core/src/pt_score.rs:")
    print(f"    PT_HEALTHY_MAX    = {h_max:.4f}")
    print(f"    PT_BORDERLINE_MAX = {b_max:.4f}")
    print(f'    PT_ZONES_CALIBRATED_FOR = "{pts.SCORE_FORMULA_ID}"')
    print()
    print("  Then regenerate the goldens and rebuild the wasm:")
    print("    python mobile-imu-core/tests/fixtures/gen_fixtures.py")
    print("    cd webapp && npm run build:wasm")
    print()
    print("  Both languages must move in the same commit: "
          "tests/test_pt_zone_parity.py pins them to each other.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
