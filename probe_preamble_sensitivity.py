"""THROWAWAY PROBE -- does pre-release material corrupt the corpus scores?

compute_pt_params measures its active oscillation window from the START of the
series it is handed, not from release (pendulastic_pt_score.py:2468,
t_r[0] + _ACTIVE_WINDOW_CAP_SEC). replay_trial_detailed hands it the WHOLE
recording, whose pre-release portion is however long the operator took to
settle the limb. On the bench trajectory, varying only the preamble length
moved A0 across 11.08 / 80.90 / 63.31 / 49.12 on one identical 90 deg swing
while the release-trimmed value held at 82.5.

The question here is whether that reaches real participants. If trimming to
release improves test-retest, it is an accuracy lever and not a bench artefact.
Reliability is the discriminator for the same reason it was in ab_requalify: a
change that merely moves numbers is not an improvement; one that tightens
repeat agreement is.

Usage:  ~/miniconda3/python.exe probe_preamble_sensitivity.py <out.csv>
"""
from __future__ import annotations

import math
import os
import sys
import warnings

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.chdir(BASE)

import numpy as np  # noqa: E402

PARAMS = ["R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n",
          "f", "area_ratio", "A0_deg"]

#: Release threshold, shared with the live path (1.0 rad/s).
RELEASE_DEG_S = 57.29577951308232
#: Keep this much hold before release. Trimming exactly AT release loses the
#: first extremum -- it needs a sample on its rising side to register at all --
#: and the first trough gets reported instead, half a period of decay later.
TRIM_LEAD_S = 0.5
#: How long the departure must persist to count as a release.
#:
#: A bare threshold crossing is not enough, and the failure is not theoretical:
#: the first version of this detector locked onto the bench trace's sync pulse
#: at 0.200 s instead of the release at 9.190 s -- an 8990 ms error -- because
#: a 5 deg step in one sample is 5000 deg/s and clears any velocity threshold
#: instantly. A real release holds |omega| above threshold for a good fraction
#: of the first quarter period; a pulse edge, and equally an examiner's
#: handling twitch, does not. 100 ms is under the shortest quarter period in
#: the corpus (f < 2.5 Hz) and well over a 1-2 sample spike.
RELEASE_SUSTAIN_S = 0.10


def find_release(t, ang):
    """First SUSTAINED |dtheta/dt| departure past the release threshold.

    The rule the live pipeline latches its zero on, applied to the fused angle
    rather than to raw gyro, plus the sustain requirement described above.
    Returns None if the series never departs.
    """
    m = np.isfinite(ang)
    if m.sum() < 20:
        return None
    tt, aa = np.asarray(t)[m], np.asarray(ang)[m]
    v = np.abs(np.gradient(aa, tt))
    over = v >= RELEASE_DEG_S
    if not over.any():
        return None
    # Walk the runs of consecutive over-threshold samples; take the first that
    # lasts RELEASE_SUSTAIN_S in wall time (not in samples -- the corpus and
    # the bench trace are at different rates).
    edges = np.flatnonzero(np.diff(over.astype(int)))
    starts = list(edges[::2] + 1) if not over[0] else [0] + list(edges[1::2] + 1)
    for s in starts:
        e = s
        while e + 1 < len(over) and over[e + 1]:
            e += 1
        if tt[e] - tt[s] >= RELEASE_SUSTAIN_S:
            return float(tt[s])
    return None


def icc1(groups_map, col=None):
    """ICC(1,1), one-way random, single measurement.

    Formula copied from test_retest_reliability.py so these numbers stay
    comparable with what has already been reported for this corpus.
    """
    groups = [np.asarray(v, float) for v in groups_map.values()]
    groups = [g[np.isfinite(g)] for g in groups]
    groups = [g for g in groups if len(g) >= 2]
    if len(groups) < 3:
        return None
    k = np.mean([len(g) for g in groups])
    allv = np.concatenate(groups)
    n = len(groups)
    gm = allv.mean()
    msb = sum(len(g) * (g.mean() - gm) ** 2 for g in groups) / (n - 1)
    msw = sum(((g - g.mean()) ** 2).sum() for g in groups) / (len(allv) - n)
    icc = (msb - msw) / (msb + (k - 1) * msw) if (msb + (k - 1) * msw) else np.nan
    sem = math.sqrt(msw)
    return {"icc": icc, "sem": sem, "mdc95": 1.96 * math.sqrt(2) * sem,
            "mean": gm, "n_groups": n, "n_trials": len(allv)}


def validate_detector():
    """The detector must find a release we already know before it is trusted
    on trials where we do not."""
    import motor_reference as mr
    import imu_calibration_tuner as tuner
    from imu_calibration_config import load_config
    from bench_dryrun import synth_phone_log
    cfg = load_config()
    print("Release detector, checked against a KNOWN release:")
    errs = []
    for a0 in (90.0, 50.0, 25.0):
        t, th, tr = mr.full_trace(a0, 0.9, 1.6, duration_s=12.0, fs_hz=1000.0)
        tt, ang, q = tuner.replay_trial_detailed(
            synth_phone_log(t, th), dict(cfg), tick_s=tuner.ANALYSIS_TICK_S)
        got = find_release(tt, ang)
        if got is None:
            print(f"  a0={a0:5.1f}  true {tr:6.3f}  NOT FOUND")
            continue
        errs.append(abs(got - tr))
        print(f"  a0={a0:5.1f}  true {tr:6.3f}  found {got:6.3f}  "
              f"err {abs(got - tr) * 1000:5.0f} ms")
    if errs:
        print(f"  max error {max(errs) * 1000:.0f} ms\n")
    return errs


def main() -> int:
    import pandas as pd
    import batch_imu_vs_optitrack_rmse as batch
    import imu_calibration_tuner as tuner
    import rmse_pipeline_common as rpc
    import pendulastic_pt_score as pts
    from imu_calibration_config import load_config

    out_path = sys.argv[1] if len(sys.argv) > 1 else "preamble_out.csv"
    cfg = load_config()
    validate_detector()

    rows = []
    trials = batch.discover_trials()
    print(f"scoring {len(trials)} trials both ways...", flush=True)
    for i, tr in enumerate(trials):
        key = tr["trial_key"]
        if not key:
            continue
        rec = {"key": key, "group": key.rsplit("_", 1)[0],
               "scored_full": False, "scored_trim": False,
               "pre_release_s": np.nan, "reason": ""}
        try:
            samples = rpc.reconstruct_trial(tr["accel"], tr["gyro"], tr["mag"])
            t, ang, q = tuner.replay_trial_detailed(
                samples, dict(cfg), tick_s=tuner.ANALYSIS_TICK_S)
            if not q.ok:
                rec["reason"] = q.reason
                rows.append(rec)
                continue
            t = np.asarray(t, float)
            ang = np.asarray(ang, float)

            p_full = pts.compute_pt_params(t, ang)
            if p_full:
                rec["scored_full"] = True
                for f in PARAMS:
                    rec[f"full_{f}"] = p_full.get(f)

            t_rel = find_release(t, ang)
            if t_rel is None:
                rec["reason"] = "no_release_found"
            else:
                finite_t0 = float(t[np.isfinite(ang)][0])
                rec["pre_release_s"] = t_rel - finite_t0
                keep = t >= (t_rel - TRIM_LEAD_S)
                if keep.sum() > 50:
                    p_trim = pts.compute_pt_params(t[keep] - t[keep][0], ang[keep])
                    if p_trim:
                        rec["scored_trim"] = True
                        for f in PARAMS:
                            rec[f"trim_{f}"] = p_trim.get(f)
                else:
                    rec["reason"] = "too_short_after_trim"
        except Exception as e:
            rec["reason"] = f"exc_{type(e).__name__}"
        rows.append(rec)
        if (i + 1) % 40 == 0:
            print(f"  {i + 1}/{len(trials)}", flush=True)

    d = pd.DataFrame(rows)
    d.to_csv(out_path, index=False)
    print(f"\nwrote {out_path}")
    print(f"scored full {int(d.scored_full.sum())}, "
          f"trimmed {int(d.scored_trim.sum())}, of {len(d)}")

    pr = d.pre_release_s.dropna()
    if len(pr):
        print("\npre-release material in the series (seconds):")
        print(f"  median {pr.median():.2f}   p90 {pr.quantile(0.9):.2f}   "
              f"max {pr.max():.2f}   >5s: {(pr > 5).sum()}/{len(pr)}   "
              f">9s: {(pr > 9).sum()}/{len(pr)}")

    both = d[d.scored_full & d.scored_trim]
    print(f"\nHow much do the scores move? ({len(both)} trials scored both ways)")
    print(f"{'param':16s} {'median |d|':>11s} {'p90 |d|':>10s} {'moved >10%':>12s}")
    for f in PARAMS:
        a, b = both[f"full_{f}"], both[f"trim_{f}"]
        dd = (b - a).abs()
        rel = dd / a.abs().replace(0, np.nan)
        print(f"{f:16s} {dd.median():11.4f} {dd.quantile(0.9):10.4f} "
              f"{int((rel > 0.10).sum()):6d}/{len(both):<5d}")

    print("\nDoes trimming improve test-retest? ICC(1,1), groups with >=2 repeats")
    print(f"{'param':16s} {'ICC full':>9s} {'ICC trim':>9s} {'delta':>8s} "
          f"{'MDC full':>10s} {'MDC trim':>10s}")
    for f in PARAMS:
        stats = {}
        for tag in ("full", "trim"):
            sub = d[d[f"scored_{tag}"]]
            gm = {g: v[f"{tag}_{f}"].values for g, v in sub.groupby("group")}
            stats[tag] = icc1(gm)
        if not stats["full"] or not stats["trim"]:
            continue
        sf, st = stats["full"], stats["trim"]
        print(f"{f:16s} {sf['icc']:9.3f} {st['icc']:9.3f} "
              f"{st['icc'] - sf['icc']:+8.3f} {sf['mdc95']:10.3f} "
              f"{st['mdc95']:10.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
