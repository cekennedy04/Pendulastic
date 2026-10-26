"""Compare two test-retest runs on the trials BOTH of them scored.

Why matched. Two variants of the angle path do not score the same trials: on
2026-10-23 the abs extraction scored 164 where the signed one scored 161, which
also changed the group and participant counts. Comparing the two summary tables
therefore compares two populations as well as two algorithms, and the
difference cannot be attributed. OPTION_B_CORE_RECONCILIATION_PLAN.md's gate 2
asks whether the shipping branch regresses the corpus against the abs parent;
answering it honestly needs one trial set.

This reads the per-trial caches both runs leave behind, intersects them, and
recomputes reliability on that intersection using the SAME icc1 that
test_retest_reliability.py reports with -- imported, not reimplemented.

    python retest_compare.py CACHE_A.json CACHE_B.json

With no arguments it lists the caches it can find and stops.
"""
from __future__ import annotations

import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from test_retest_reliability import PARAMS, EXTRA, MIN_REPEATS, icc1  # noqa: E402

CACHE_DIR = os.path.join(BASE, "Model_Analysis_Outputs", "_cache")
POPOVIC = list(PARAMS)          # the seven the gate is about


def load(path):
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    meta = raw.pop("__meta__", None) or {}
    scored = {k: v for k, v in raw.items()
              if isinstance(v, dict) and "error" not in v}
    failed = {k for k, v in raw.items() if isinstance(v, dict) and "error" in v}
    return scored, failed, meta


def describe(path, scored, failed, meta):
    label = meta.get("angle_extraction", "UNKNOWN (cache predates variant stamping)")
    print(f"  {os.path.basename(path)}")
    print(f"    angle extraction {label}")
    if meta.get("tree"):
        print(f"    tree             {meta['tree']}")
    print(f"    scored {len(scored)}, failed {len(failed)}")


def frame(scored, keys, excluded):
    import pandas as pd
    rows = []
    for k in sorted(keys):
        if k in excluded:
            continue
        rows.append({"trial_key": k, "group": k.rsplit("_", 1)[0], **scored[k]})
    return pd.DataFrame(rows)


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        if os.path.isdir(CACHE_DIR):
            print("caches found:")
            for n in sorted(os.listdir(CACHE_DIR)):
                if n.endswith(".json"):
                    print(f"  {os.path.join(CACHE_DIR, n)}")
        else:
            print(f"no cache directory at {CACHE_DIR}")
        return 2

    a_path, b_path = argv
    a_scored, a_failed, a_meta = load(a_path)
    b_scored, b_failed, b_meta = load(b_path)
    print("inputs")
    describe(a_path, a_scored, a_failed, a_meta)
    describe(b_path, b_scored, b_failed, b_meta)

    try:
        with open(os.path.join(BASE, "excluded_trials.json"), encoding="utf-8") as fh:
            excluded = set(json.load(fh))
    except OSError:
        excluded = set()
    if excluded:
        print(f"\nhonouring {len(excluded)} operator exclusion(s)")

    both = set(a_scored) & set(b_scored)
    only_a = set(a_scored) - set(b_scored)
    only_b = set(b_scored) - set(a_scored)
    print(f"\nscored by both: {len(both)}"
          f"   only A: {len(only_a)}   only B: {len(only_b)}")
    if only_a or only_b:
        print("  (these are what made the unmatched tables incomparable)")
        for tag, s in (("only A", only_a), ("only B", only_b)):
            if s:
                shown = ", ".join(sorted(s)[:6])
                print(f"    {tag}: {shown}{' ...' if len(s) > 6 else ''}")

    fa, fb = frame(a_scored, both, excluded), frame(b_scored, both, excluded)
    if fa.empty:
        print("\nno trials in common after exclusions")
        return 1

    sizes = fa.groupby("group").size()
    keep = set(sizes[sizes >= MIN_REPEATS].index)
    fa, fb = fa[fa.group.isin(keep)], fb[fb.group.isin(keep)]
    print(f"{len(keep)} groups with >= {MIN_REPEATS} repeats inside the "
          f"intersection, {len(fa)} trials")
    if not keep:
        print("no group has enough repeats in common to compare")
        return 1

    print(f"\n{'parameter':<18}{'ICC A':>8}{'ICC B':>8}{'SEM A':>10}{'SEM B':>10}"
          f"{'SEM change':>12}")
    better = worse = 0
    for col in POPOVIC + list(EXTRA):
        if col not in fa or col not in fb:
            continue
        ra, rb = icc1(fa, col), icc1(fb, col)
        if not ra or not rb:
            continue
        if ra["sem"] > 0:
            pct = 100.0 * (rb["sem"] - ra["sem"]) / ra["sem"]
            change = f"{pct:+.1f}%"
        else:
            change = "n/a"
        if col in POPOVIC:
            if rb["sem"] < ra["sem"]:
                better += 1
            elif rb["sem"] > ra["sem"]:
                worse += 1
        print(f"{col:<18}{ra['icc']:>8.3f}{rb['icc']:>8.3f}"
              f"{ra['sem']:>10.3f}{rb['sem']:>10.3f}{change:>12}")

    print(f"\nover the {len(POPOVIC)} Popovic parameters: B better on {better}, "
          f"worse on {worse}")
    print("negative SEM change = B is more repeatable than A on the same trials")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
