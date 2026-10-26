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

    out = {k: (mas[k], statistics.median(v)) for k, v in by_leg.items()}
    if verbose:
        print(f"  {len(out)} participant-legs, "
              f"{sum(len(v) for v in by_leg.values())} interpretable trials "
              f"({refused} refused, {unscorable} unscorable)")
    return out


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--opti-root", help="where OptiTrack_Recordings actually is")
    ap.add_argument("--mas-csv", help="where mas_scores.csv actually is")
    a = ap.parse_args(argv)

    print(f"Re-deriving the zone thresholds for {pts.SCORE_FORMULA_ID}")
    print(f"  scored parameters ({pts._N_PARAMS}): {', '.join(pts._PARAM_KEYS)}")
    print(f"  reported, not scored: {', '.join(pts._DIAGNOSTIC_KEYS)}")
    print()
    legs = leg_scores(a.opti_root, a.mas_csv)
    print()
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
