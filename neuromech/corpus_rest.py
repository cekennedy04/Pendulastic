"""Where do real legs rest? The corpus measurement behind plan D7 (build item B10).

    python -m neuromech.corpus_rest            # ~25 min cold, seconds once cached
    PENDULASTIC_PIPELINE_DIR=<checkout> python -m neuromech.corpus_rest
                                               # replay with another checkout's IMU pipeline
                                               # (e.g. the abs-fold extraction); the scorer is
                                               # always this branch's validated one

Three sources gave three rests (118, 125, 142 deg). This measures it once, by one defined
method, on the phone corpus: every phone trial replayed through the committed IMU pipeline
and scored by the validated scorer, under the production convention (detrend=False) and,
for comparison, the detrend default the test-retest analysis used.

Per trial the quantity is the REST DEPTH, pre-release angle minus the settled tail median
(the scorer's neutral_deg): how far below its released position the leg comes to rest.
That is the number the rig needs, because the rig releases from its own extension, and it
does not depend on the phone's absolute zero. Participants are summarised by their median
trial, then cohorts by the median participant, so a participant with 36 trials counts once.

Cohort comes from each participant's metadata.json diagnosis (participant_paths), never
from a folder name. Controls recorded before raw-sensor logging (markerless video only)
cannot enter this measurement; they are counted and named in the output, not silently lost.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "neuromech", "out")

MIN_POST_RELEASE_S = 5.0      # neutral is the median of the last 25 % of the record
MAX_RELEASE_OFF_DEG = 15.0    # released within 15 deg of full extension, as the protocol asks
FINGERPRINTED = ("pendulastic_pt_score", "imu_calibration_tuner", "imu_calibration_config",
                 "rmse_pipeline_common", "imu_flex_axis")


def _fingerprint() -> str:
    """Hash of the files the modules were ACTUALLY loaded from, so two pipelines never share a cache."""
    h = hashlib.sha256()
    for m in FINGERPRINTED:
        with open(sys.modules[m].__file__, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:12]


def _variant() -> str:
    """Which swing-angle extraction is loaded (same test as test_retest_reliability._angle_variant)."""
    fa = sys.modules["imu_flex_axis"]
    return "SIGNED" if hasattr(fa, "swing_about_axis_deg") else "ABS FOLD"


def _load_pipeline():
    """The validated scorer from THIS branch, then the replay pipeline from
    $PENDULASTIC_PIPELINE_DIR if set. Order matters: whatever is imported first is cached."""
    import pendulastic_pt_score  # noqa: F401  -- pinned before the path changes
    import batch_imu_vs_optitrack_rmse  # noqa: F401  -- discovery + exclusions: not the extraction
    pipe = os.environ.get("PENDULASTIC_PIPELINE_DIR")
    if pipe:
        # discovery imported this branch's extraction modules on the way in; evict them so the
        # replay below loads the other checkout's, and say where each one actually came from
        for m in ("imu_flex_axis", "imu_calibration_tuner", "rmse_pipeline_common", "imu_calibration_config"):
            sys.modules.pop(m, None)
        sys.path.insert(0, os.path.abspath(pipe))
    import imu_flex_axis, imu_calibration_tuner, rmse_pipeline_common  # noqa: F401,E401
    import imu_calibration_config  # noqa: F401
    for m in FINGERPRINTED:
        print(f"  {m:24s} {sys.modules[m].__file__}", flush=True)
    return pipe


def _fin(x):
    return float(x) if x is not None and math.isfinite(float(x)) else None


def score_corpus(verbose=True) -> list[dict]:
    pipe = _load_pipeline()
    import batch_imu_vs_optitrack_rmse as batch
    import imu_calibration_tuner as tuner
    import participant_paths as pp
    import pendulastic_pt_score as pts
    import rmse_pipeline_common as rpc
    from imu_calibration_config import load_config

    cfg = load_config()
    fp = _fingerprint()
    cache_file = os.path.join(OUT, f"corpus_rest_cache_{fp}.json")
    os.makedirs(OUT, exist_ok=True)
    try:
        cache = json.load(open(cache_file, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    trials = batch.discover_trials(analysis="phone")
    rec_root = os.path.join(batch.BASE_DIR, "Recordings")
    if verbose:
        print(f"{len(trials)} phone trials; pipeline {pipe or ROOT} ({_variant()}); "
              f"fingerprint {fp}; {len(cache)} cached", flush=True)
    rows = []
    for i, tr in enumerate(trials):
        key = tr["trial_key"]
        pid = pp.pid_from_dir_name(tr["participant"]) or tr["participant"]
        if key not in cache:
            rec = {}
            try:
                samples = rpc.reconstruct_trial(tr["accel"], tr["gyro"], tr["mag"])
                t, ang = tuner.replay_trial(samples, dict(cfg), tick_s=tuner.ANALYSIS_TICK_S)
                t, ang = np.asarray(t, float), np.asarray(ang, float)
                for conv, det in (("prod", False), ("detrend", True)):
                    p = pts.compute_pt_params(t, ang, detrend=det)
                    if not p:
                        rec[conv] = {"error": "compute_pt_params returned None"}
                        continue
                    rec[conv] = {k: _fin(p.get(k)) for k in ("neutral_deg", "pre_release_deg", "A0_deg")}
                    # The scorer returns the post-release time base, not its length;
                    # derived as test_retest_reliability.py does.
                    t_r = p.get("t_r")
                    rec[conv]["post_release_s"] = (float(t_r[-1] - t_r[0])
                                                   if t_r is not None and len(t_r) > 1 else None)
                    rec[conv]["hold_status"] = (p.get("hold_drift") or {}).get("status")
            except Exception as e:      # one bad file must not stop a 25-minute run
                rec = {"error": f"{type(e).__name__}: {e}"[:160]}
            cache[key] = rec
            if verbose and (i % 10 == 0 or i == len(trials) - 1):
                print(f"  {i + 1}/{len(trials)} {key}", flush=True)
                json.dump(cache, open(cache_file, "w", encoding="utf-8"))
        group = pp.group_for_diagnosis(pp.diagnosis_from_metadata(rec_root, pid)) or "ungrouped"
        rows.append({"trial_key": key, "pid": pid, "group": group, **{"rec": cache[key]}})
    json.dump(cache, open(cache_file, "w", encoding="utf-8"))
    return rows


def _keep(r: dict, conv: str) -> tuple[bool, str]:
    rec = r["rec"]
    if "error" in rec:
        return False, "replay failed"
    c = rec.get(conv, {})
    if "error" in c or c.get("neutral_deg") is None or c.get("pre_release_deg") is None:
        return False, "not scored"
    if (c.get("post_release_s") or 0) < MIN_POST_RELEASE_S:
        return False, f"< {MIN_POST_RELEASE_S:g} s after release"
    if abs(c["pre_release_deg"] - 180.0) > MAX_RELEASE_OFF_DEG:
        return False, f"released > {MAX_RELEASE_OFF_DEG:g} deg from extension"
    if c.get("hold_status") == "drifting":          # the pre-release angle itself is moving
        return False, "hold drifting"
    if not 0.0 < abs(c["pre_release_deg"] - c["neutral_deg"]) <= 120.0:
        return False, "rest outside 0-120 deg below release"
    return True, ""


def summarise(rows: list[dict], conv: str = "prod") -> dict:
    """{group: {n_participants, n_trials, depth_median, depth_range, per_participant, dropped}}."""
    out = {}
    for g in sorted({r["group"] for r in rows}):
        kept, dropped = {}, {}
        for r in (x for x in rows if x["group"] == g):
            ok, why = _keep(r, conv)
            if ok:
                c = r["rec"][conv]
                # Magnitude, not signed: the phone's flexion direction depends on how it
                # was mounted, so neutral settles ~70 deg either side of 180 -- even
                # between trials of one leg (P16 T1 108, T2 242). A0 is unsigned too.
                kept.setdefault(r["pid"], []).append(abs(c["pre_release_deg"] - c["neutral_deg"]))
            else:
                dropped[why] = dropped.get(why, 0) + 1
        per = {pid: (float(np.median(v)), len(v)) for pid, v in sorted(kept.items(), key=lambda kv: str(kv[0]))}
        med = [m for m, _n in per.values()]
        out[g] = {"n_participants": len(per), "n_trials": sum(n for _m, n in per.values()),
                  "depth_median": float(np.median(med)) if med else None,
                  "depth_range": (float(min(med)), float(max(med))) if med else None,
                  "per_participant": per, "dropped": dropped}
    return out


def main() -> int:
    import participant_paths as pp
    from neuromech import pendulum_sim as ref
    rows = score_corpus()
    res = {conv: summarise(rows, conv) for conv in ("prod", "detrend")}
    fp = _fingerprint()
    lines = [f"pipeline {os.environ.get('PENDULASTIC_PIPELINE_DIR') or ROOT}  extraction {_variant()}  fingerprint {fp}"]
    for conv, label in (("prod", "production (detrend=False)"), ("detrend", "detrend default")):
        lines.append(f"\nRest depth = pre-release angle - settled neutral, {label}")
        lines.append(f"{'cohort':10s} {'people':>6s} {'trials':>6s} {'depth':>7s} {'rest knee*':>10s}  participant medians")
        for g, s in res[conv].items():
            if not s["n_participants"]:
                lines.append(f"{g:10s} {0:6d} {0:6d}       -          -  dropped {s['dropped']}")
                continue
            per = ", ".join(f"{pid}:{m:.0f}({n})" for pid, (m, n) in s["per_participant"].items())
            lines.append(f"{g:10s} {s['n_participants']:6d} {s['n_trials']:6d} {s['depth_median']:7.1f} "
                         f"{180 - s['depth_median']:10.1f}  {per}   dropped {s['dropped'] or '-'}")
    lines.append("  * rest knee = 180 - depth: the knee angle at rest for a leg released from full extension")
    ctrl = res["prod"].get(pp.CONTROL_PREFIX)
    if ctrl and ctrl["n_participants"]:
        rest_knee = 180.0 - ctrl["depth_median"]
        inc = ref.thigh_incline_from_resting_deg(rest_knee)
        lines.append(f"\nControls, production convention: rest knee {rest_knee:.1f} deg -> thigh incline "
                     f"{inc:.1f} deg for the model (Params.incline_deg; current default -35.0 gives 125.0)")
    # who cannot be measured: control folders with no raw phone sensor files
    import batch_imu_vs_optitrack_rmse as batch
    rec_root = os.path.join(batch.BASE_DIR, "Recordings")
    seen = {str(r["pid"]) for r in rows}
    missing = [pid for pid, _name, _full in pp.iter_participant_dirs(rec_root)
               if pp.group_for_diagnosis(pp.diagnosis_from_metadata(rec_root, pid)) == pp.CONTROL_PREFIX and str(pid) not in seen]
    if missing:
        lines.append(f"Controls with no raw phone sensor data (markerless only), not in this measurement: {', '.join(sorted(missing, key=str))}")
    txt = "\n".join(lines)
    print(txt)
    with open(os.path.join(OUT, f"corpus_rest_summary_{fp}.txt"), "w", encoding="utf-8") as fh:
        fh.write(txt + "\n")
    with open(os.path.join(OUT, f"corpus_rest_summary_{fp}.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
