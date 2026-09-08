"""U4 / R19: are IMU A0 and optical A0 on the same scale?

The excursion gate applies ONE absolute constant, MIN_INTERPRETABLE_A0_DEG, to
whatever A0 reaches it. If the two instruments report different numbers for the
same swing, a trial's verdict depends partly on which one measured it.

A0 is a difference -- start height above the settled tail -- so the datum gap
that dominates raw cross-modality RMSE should cancel here. Polarity is already
handled inside compute_pt_params. So this is a fair comparison in a way that
raw angle RMSE is not.

Joins two existing caches rather than recomputing:
  gate_regrid.json      IMU A0 at both grids ('new' is the current 100 Hz path)
  optical_wrap.json     span / jump stats, used to drop the corrupt references
  optical_recheck.json  optical A0 on the scorer's own loader

The plan says the outcome determines the code change, and not to write a
per-modality branch before the measurement asks for one. Read-only.
"""
import json
import os
import sys

BASE = r"C:\Users\cladi\Pendulastic"
sys.path.insert(0, BASE)
os.chdir(BASE)
C = os.path.join(BASE, "Model_Analysis_Outputs", "_cache")


def load(name):
    with open(os.path.join(C, name), encoding="utf-8") as fh:
        return json.load(fh)


def main():
    import numpy as np
    import pendulastic_pt_score as pts

    imu = load("gate_regrid.json")
    opt = load("optical_recheck.json")
    wrap = load("optical_wrap.json")
    excluded = set(json.load(open(os.path.join(BASE, "excluded_trials.json"),
                                  encoding="utf-8")))

    # Reference curves with a jump no knee can make are not a reference.
    corrupt = {k for k, v in wrap.items()
               if isinstance(v, dict) and v.get("n_jumps", 0) > 0}

    rows = []
    for key, iv in imu.items():
        if key in excluded:
            continue
        ov = opt.get(key)
        if not isinstance(iv, dict) or not isinstance(ov, dict):
            continue
        a_imu, a_opt = iv.get("new"), ov.get("a0_scorer")
        if not isinstance(a_imu, float) or not isinstance(a_opt, float):
            continue
        rows.append((key, a_imu, a_opt, key in corrupt))

    clean = [r for r in rows if not r[3]]
    print(f"paired trials with A0 from both instruments: {len(rows)}")
    print(f"  after dropping corrupt references: {len(clean)} "
          f"({len(rows) - len(clean)} dropped)\n")
    if len(clean) < 10:
        print("too few to conclude")
        return

    i = np.array([r[1] for r in clean])
    o = np.array([r[2] for r in clean])
    d = i - o
    ratio = i / np.where(o == 0, np.nan, o)

    print(f"{'':22}{'IMU':>10}{'optical':>10}")
    print(f"{'median A0 (deg)':22}{np.median(i):>10.2f}{np.median(o):>10.2f}")
    print(f"{'mean':22}{i.mean():>10.2f}{o.mean():>10.2f}")
    print(f"\ndifference IMU - optical: median {np.median(d):+.2f} deg, "
          f"mean {d.mean():+.2f}, sd {d.std(ddof=1):.2f}")
    print(f"  p10 {np.percentile(d, 10):+.2f}   p90 {np.percentile(d, 90):+.2f}")
    print(f"ratio IMU / optical:      median {np.nanmedian(ratio):.3f}   "
          f"p10 {np.nanpercentile(ratio, 10):.3f}   p90 {np.nanpercentile(ratio, 90):.3f}")
    r = np.corrcoef(i, o)[0, 1]
    print(f"correlation: r = {r:.3f}")

    # The question the gate actually asks.
    gate = pts.MIN_INTERPRETABLE_A0_DEG
    di = i < gate
    do = o < gate
    print(f"\nAgainst the {gate:.0f} deg floor:")
    print(f"  refused on IMU only:     {int((di & ~do).sum())}")
    print(f"  refused on optical only: {int((~di & do).sum())}")
    print(f"  refused on both:         {int((di & do).sum())}")
    print(f"  passed on both:          {int((~di & ~do).sum())}")
    disagree = int((di ^ do).sum())
    print(f"  VERDICT DISAGREES on {disagree} of {len(clean)} "
          f"({100 * disagree / len(clean):.0f}%)")

    print("\nWorst disagreements near the floor:")
    near = sorted(((abs(a - b), k, a, b) for k, a, b, _c in clean
                   if min(a, b) < gate * 2), reverse=True)[:6]
    for gap, k, a, b in near:
        flag = "  <- verdict differs" if (a < gate) ^ (b < gate) else ""
        print(f"  {k[:28]:30} IMU {a:6.2f}  optical {b:6.2f}  gap {gap:5.2f}{flag}")


if __name__ == "__main__":
    main()
