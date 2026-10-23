"""Within-session test-retest reliability of the phone-IMU PT parameters.

The one quantitative result available without a reference instrument, a MAS
grade or the bench rig: the same leg, swung several times in one sitting,
should score the same each time. Every input is already on disk.

Scored on the PRODUCTION path -- reconstruct_trial -> replay_trial at
ANALYSIS_TICK_S (100 Hz) -> compute_pt_params -- not on raw prox_pitch, which
bypasses the AHRS and the EMA and is not what the app computes.

Reports per parameter:
  ICC(1,1)   one-way random, because repeat trials are not distinguishable
             raters -- "trial 2" carries no meaning that "trial 1" does not.
  SEM        within-subject SD, the spread of a single measurement.
  MDC95      1.96 * sqrt(2) * SEM -- the change a second measurement must show
             before it can be called real. The clinically useful number.
  CV         SEM / grand mean, for cross-parameter comparison.

Honours excluded_trials.json (operator exclusions only; this never drops a
trial on its own). Caches per-trial params so a re-run is cheap.

Run:  ~/miniconda3/python.exe test_retest_reliability.py
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import warnings

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.chdir(BASE)

# The cache is keyed by a fingerprint of the code that produces the numbers,
# not just by trial. It used to be one fixed filename keyed on trial_key alone,
# with `if key in cache: continue` -- so re-running after changing the angle
# path (a revert, a branch switch, a config edit) silently reused the previous
# variant's parameters and reported the two as identical. That is the worst
# possible failure for a comparison whose entire purpose is to detect a
# difference, and it cost a run on 2026-10-23. Switching variants now lands in
# a different cache file automatically, so no one has to remember to delete it,
# and re-running the SAME variant is still cheap.
_FINGERPRINT_SOURCES = (
    "imu_flex_axis.py",
    "imu_calibration_tuner.py",
    "pendulastic_pt_score.py",
    "rmse_pipeline_common.py",
    "pendulastic_imu_server.py",
)


def _algo_fingerprint(cfg) -> str:
    """Short digest of every file that decides a trial's parameters, plus the
    calibration config. Content-based, so it moves with an uncommitted edit --
    a git revision would not."""
    h = hashlib.sha256()
    for name in _FINGERPRINT_SOURCES:
        path = os.path.join(BASE, name)
        h.update(name.encode())
        try:
            with open(path, "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(b"<missing>")
    h.update(json.dumps(dict(cfg), sort_keys=True, default=str).encode())
    return h.hexdigest()[:12]


def _angle_variant() -> str:
    """Which swing-angle extraction is in this tree, in words.

    The fingerprint above identifies a variant uniquely but says nothing about
    WHICH one, so a run had to be labelled by hand from the branch name -- and
    an uncommitted `git revert` that follows you across a checkout makes the
    branch name a lie. That mislabelled two runs on 2026-10-23. 61d5723 added
    `swing_about_axis_deg` to imu_flex_axis.py and its revert removes it, so
    the tree can simply be asked.
    """
    try:
        with open(os.path.join(BASE, "imu_flex_axis.py"), encoding="utf-8") as fh:
            src = fh.read()
    except OSError:
        return "UNKNOWN (imu_flex_axis.py could not be read)"
    if "def swing_about_axis_deg" in src:
        return "SIGNED  (swing_about_axis_deg present: 61d5723 applied)"
    return "ABS FOLD (no swing_about_axis_deg: 61d5723 absent or reverted)"


def _tree_state() -> str:
    """Branch, commit and dirtiness -- the provenance a result needs quoting."""
    import subprocess

    def git(*a):
        try:
            return subprocess.run(("git",) + a, cwd=BASE, capture_output=True,
                                  text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    head = git("rev-parse", "--short", "HEAD") or "?"
    branch = git("rev-parse", "--abbrev-ref", "HEAD") or "?"
    dirty = [l for l in (git("status", "--porcelain") or "").splitlines() if l.strip()]
    suffix = f", {len(dirty)} uncommitted change(s)" if dirty else ", clean"
    return f"{branch} @ {head}{suffix}"


def _cache_path(fp: str) -> str:
    return os.path.join(BASE, "Model_Analysis_Outputs", "_cache",
                        f"test_retest_params_{fp}.json")
# Carries the fingerprint for the same reason the cache does: comparing two
# variants means two result files, and a fixed name silently overwrote the
# first run with the second.
def _out_csv(fp: str) -> str:
    return os.path.join(BASE, "Model_Analysis_Outputs",
                        f"test_retest_reliability_{fp}.csv")

PARAMS = ["R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n",
          "f", "area_ratio"]
EXTRA = ["A0_deg", "omega_peak_deg_s"]

# Recorded alongside the parameters, not scored for reliability: these are what
# distinguish the competing explanations for A0's scatter. A0 = release angle
# minus neutral, so if A0 moves between repeats it is because one of those two
# moved, or because the swing really was different. Simulation through this same
# scorer (2026-10-23) ruled out stop time, hold length, angle noise and linear
# tail drift, and showed that GENUINE release-angle variation leaves the
# A0-normalised ratios perfectly stable -- the opposite of the measured
# ordering. So the remaining candidate is the angle trace itself differing in
# shape between repeats, and these columns are how that gets tested on real
# trials rather than argued about.
DIAG = ["neutral_deg", "pre_release_deg", "release_t_s", "post_release_s"]
MIN_REPEATS = 3


def main() -> int:
    import numpy as np
    import pandas as pd
    import batch_imu_vs_optitrack_rmse as batch
    import imu_calibration_tuner as tuner
    import rmse_pipeline_common as rpc
    import pendulastic_pt_score as pts
    from imu_calibration_config import load_config

    cfg = load_config()
    fp = _algo_fingerprint(cfg)
    cache_file = _cache_path(fp)
    variant = _angle_variant()
    print(f"angle extraction      {variant}", flush=True)
    print(f"tree                  {_tree_state()}", flush=True)
    print(f"algorithm fingerprint {fp}", flush=True)
    print(f"cache                 {cache_file}", flush=True)
    trials = batch.discover_trials()
    print(f"{len(trials)} trials discovered", flush=True)

    try:
        cache = json.load(open(cache_file, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if cache:
        print(f"  reusing {len(cache)} cached trials for this fingerprint", flush=True)

    for i, tr in enumerate(trials):
        key = tr["trial_key"]
        if key in cache:
            continue
        try:
            samples = rpc.reconstruct_trial(tr["accel"], tr["gyro"], tr["mag"])
            t, ang = tuner.replay_trial(samples, dict(cfg),
                                        tick_s=tuner.ANALYSIS_TICK_S)
            p = pts.compute_pt_params(np.asarray(t, float),
                                      np.asarray(ang, float))
        except Exception as e:
            cache[key] = {"error": f"{type(e).__name__}: {e}"[:140]}
        else:
            if not p:
                cache[key] = {"error": "compute_pt_params returned None"}
            else:
                rec = {k: (float(p[k]) if p.get(k) is not None
                           and math.isfinite(float(p[k])) else None)
                       for k in PARAMS + EXTRA if k in p}
                hold = p.get("hold_drift") or {}
                rec["hold_status"] = hold.get("status")
                # t_r is the time base from the release onward, so its first
                # element is when the release was detected and its span is how
                # much post-release signal the neutral estimate had to work
                # with. Both are needed to tell a mis-placed release from a
                # mis-estimated neutral.
                t_r = p.get("t_r")
                rec["neutral_deg"] = (float(p["neutral_deg"])
                                      if p.get("neutral_deg") is not None
                                      and math.isfinite(float(p["neutral_deg"])) else None)
                rec["pre_release_deg"] = (float(p["pre_release_deg"])
                                          if p.get("pre_release_deg") is not None
                                          and math.isfinite(float(p["pre_release_deg"])) else None)
                if t_r is not None and len(t_r) > 1:
                    rec["release_t_s"] = float(t_r[0])
                    rec["post_release_s"] = float(t_r[-1] - t_r[0])
                else:
                    rec["release_t_s"] = None
                    rec["post_release_s"] = None
                cache[key] = rec
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        tmp = cache_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        os.replace(tmp, cache_file)
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(trials)}", flush=True)

    excluded = set(json.load(open(os.path.join(BASE, "excluded_trials.json"),
                                  encoding="utf-8")))
    rows = []
    for tr in trials:
        key = tr["trial_key"]
        rec = cache.get(key)
        if not rec or "error" in rec:
            continue
        # trial_key is pid_leg_condition_Tn -- the group is everything but Tn
        parts = key.rsplit("_", 1)
        rows.append({"trial_key": key, "group": parts[0],
                     "excluded": key in excluded, **rec})
    d = pd.DataFrame(rows)
    scored = len(d)
    d = d[~d.excluded]
    print(f"\nscored {scored} trials; {scored - len(d)} operator-excluded "
          f"-> {len(d)} kept", flush=True)

    sizes = d.groupby("group").size()
    keep = set(sizes[sizes >= MIN_REPEATS].index)
    r = d[d.group.isin(keep)]
    pids = {g.split("_")[0] for g in keep}
    print(f"{len(keep)} groups with >= {MIN_REPEATS} repeats, "
          f"{len(r)} trials, {len(pids)} participants\n", flush=True)
    if r.empty:
        return 1

    def icc1(frame, col):
        """ICC(1,1): one-way random effects, single measurement."""
        groups = [g[col].dropna().values for _, g in frame.groupby("group")]
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

    print(f"{'parameter':16s} {'ICC(1,1)':>9s} {'SEM':>9s} {'MDC95':>9s} "
          f"{'mean':>9s} {'CV%':>7s} {'groups':>7s}")
    out = []
    for col in PARAMS + EXTRA:
        if col not in r:
            continue
        s = icc1(r, col)
        if not s:
            continue
        cv = (s["sem"] / abs(s["mean"]) * 100) if s["mean"] else float("nan")
        out.append({"parameter": col, **s, "cv_pct": cv})
        print(f"{col:16s} {s['icc']:9.3f} {s['sem']:9.3f} {s['mdc95']:9.3f} "
              f"{s['mean']:9.3f} {cv:7.1f} {s['n_groups']:7d}")

    out_csv = _out_csv(fp)
    pd.DataFrame([{**o, "fingerprint": fp, "angle_extraction": variant,
                   "tree": _tree_state()} for o in out]).to_csv(out_csv, index=False)
    print(f"\nwrote {out_csv}")

    # ---- where A0's scatter comes from -----------------------------------
    # A0 = release angle - neutral. If A0 moves between repeats of the same
    # leg, either the leg was released from a different angle (a protocol
    # matter), or the neutral estimate moved (an algorithm matter), or the
    # trace itself was shaped differently. These are the three columns.
    diag_cols = [c for c in ("pre_release_deg", "neutral_deg", "A0_deg") if c in r]
    if diag_cols:
        print("\nwithin-group scatter of A0 and its two terms")
        print("  (mean over groups of the within-group SD, degrees)")
        mean_sd = {}
        for col in diag_cols:
            sds = [g[col].dropna().std(ddof=1) for _, g in r.groupby("group")
                   if g[col].dropna().size >= 2]
            sds = [v for v in sds if v == v]
            if sds:
                mean_sd[col] = float(np.mean(sds))
                print(f"  {col:18s} {np.mean(sds):7.2f}   (median {np.median(sds):6.2f},"
                      f" max {np.max(sds):7.2f}, n={len(sds)} groups)")
        # Read the three numbers rather than asserting a conclusion: an earlier
        # draft printed "the trace shape differs" unconditionally, which would
        # have been wrong on exactly the data that disproves it.
        a0, pre, neu = (mean_sd.get("A0_deg"), mean_sd.get("pre_release_deg"),
                        mean_sd.get("neutral_deg"))
        if None not in (a0, pre, neu):
            if a0 > 2.0 * max(pre, neu):
                print("  => A0 moves more than either term it is built from, so the "
                      "trace SHAPE\n     differs between repeats -- not merely its "
                      "offset or its scale.")
            elif pre >= max(neu, 1e-9) and abs(a0 - pre) <= 0.5 * max(pre, 1e-9):
                print("  => A0 tracks the RELEASE ANGLE: the leg is let go from a "
                      "different\n     extension each repeat. That is a protocol "
                      "matter, and the A0-normalised\n     ratios should absorb it.")
            elif neu > pre:
                print("  => the NEUTRAL estimate is what moves, so A0's scatter is "
                      "algorithmic\n     rather than physical.")
            else:
                print("  => no single term dominates; see the per-group spread above.")

    # ---- does a fuller settled tail help? --------------------------------
    # The neutral is the tail median, so a trial stopped early has less to
    # estimate it from. If that is what moves A0, restricting to the
    # longer-tailed half should visibly shrink its SEM.
    if "post_release_s" in r and r["post_release_s"].notna().sum() >= 10:
        cut = float(r["post_release_s"].median())
        longer = r[r["post_release_s"] >= cut]
        print(f"\nrestricted to the longer-tailed half (post-release >= {cut:.1f} s, "
              f"{len(longer)} trials)")
        print(f"  {'parameter':16s} {'ICC':>9s} {'SEM':>9s} {'MDC95':>9s}   vs all trials")
        for col in ("A0_deg", "R2n", "omega_max_n", "omega_peak_deg_s", "N"):
            if col not in r:
                continue
            a, b = icc1(r, col), icc1(longer, col)
            if not a or not b:
                continue
            print(f"  {col:16s} {b['icc']:9.3f} {b['sem']:9.3f} {b['mdc95']:9.3f}"
                  f"   SEM {a['sem']:.3f} -> {b['sem']:.3f}"
                  f" ({'better' if b['sem'] < a['sem'] else 'no better'})")

    print("\ninterpretation bands (Koo & Li): "
          "<0.50 poor, 0.50-0.75 moderate, 0.75-0.90 good, >0.90 excellent")
    hs = r.get("hold_status")
    if hs is not None:
        print(f"\npre-release hold across kept trials: "
              f"{dict(hs.value_counts(dropna=False))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
