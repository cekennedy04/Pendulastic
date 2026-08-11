"""
batch_imu_vs_optitrack_rmse.py
================================
Standalone batch script: computes real per-trial RMSE-in-degrees between
the IMU-derived fused knee angle and OptiTrack ground truth, across every
real recorded trial that has both.

This exists to answer the question the whole 2026-08-04
imu-stillness-gyro-bias investigation was started for: did the raw-signal
stillness-gate fix to gyro-bias calibration (Tasks 1-4) actually reduce
RMSE against OptiTrack, and is it under the 5-degree goal? Run this script
once at a pre-fix commit and once at the post-fix commit (see
.superpowers/sdd/2026-08-04-imu-stillness-gyro-bias/task-8-9-brief.md for
the verified git-checkout sequence) and diff the two output CSVs.

Uses workbench_engine.py's already-implemented/tested engine -- the same
one the live Workbench UI's "RMSE (deg)" column is built on
(pendulastic_workbench.py:661) -- rather than reimplementing any
validation/loading/scoring logic. The only new logic here is (a)
discovering real split-CSV IMU trials on disk and (b) matching each one to
its OptiTrack counterpart, since the two directory trees are not a
byte-for-byte mirror at every level (confirmed by direct inspection: e.g.
Participant_13_right_post's OptiTrack CSVs sit one directory level higher
than Participant_13_left_post's).

Not pipeline-wired -- a one-off diagnostic, same category as
analyze_accel_drift.py (Task 5).

Usage:
    .venv\\Scripts\\python.exe batch_imu_vs_optitrack_rmse.py
"""
from __future__ import annotations

import csv
import glob
import os
import re
import statistics
import warnings
from typing import Optional

import workbench_engine as engine

BASE_DIR = r"C:\Users\cladi\Pendulastic"
REC_ROOT = os.path.join(BASE_DIR, "Recordings")
OPTI_ROOT = os.path.join(BASE_DIR, "OptiTrack_Recordings")
OUT_CSV = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "imu_vs_optitrack_rmse.csv")

RMSE_GOAL_DEG = 5.0

_TRIAL_ANCHOR_RE = re.compile(r"^Trial_(\d+)_imu\.csv$", re.IGNORECASE)


def derive_component_paths(imu_path: str) -> dict:
    """Given any .../Trial_{n}_imu.csv path, derive the sibling
    accel/gyro/mag paths via simple suffix replacement in the same
    directory (per the brief: no need to reuse workbench_engine's private
    _derive_split_csv_siblings, which is anchor-agnostic for a different,
    UI-facing use case)."""
    if not imu_path.endswith("_imu.csv"):
        raise ValueError(f"{imu_path!r} does not end with '_imu.csv'")
    prefix = imu_path[: -len("_imu.csv")]
    return {
        "imu": imu_path,
        "accel": prefix + "_accel.csv",
        "gyro": prefix + "_gyro.csv",
        "mag": prefix + "_mag.csv",
    }


def _positions_with_trial(scope_dir: str, trial_n: str) -> set:
    """Recursively find the distinct Position_* directory names under
    scope_dir that contain a Trial_{trial_n}_imu.csv file anywhere beneath
    them (case-insensitive on both the Position_ prefix and the filename).
    Used to detect whether a trial number is genuinely ambiguous across
    more than one position once a walk-up has left the position-scoped
    part of the tree -- e.g. Position_1 and Position_2 both have a
    Trial_3_imu.csv, so a single shared-ancestor OptiTrack match for
    "trial_3" can't be safely attributed to either one."""
    if not os.path.isdir(scope_dir):
        return set()
    target = f"trial_{trial_n}_imu.csv"
    positions = set()
    for dirpath, _dirnames, filenames in os.walk(scope_dir):
        if not any(fn.lower() == target for fn in filenames):
            continue
        pos_parts = [p for p in os.path.normpath(dirpath).split(os.sep)
                     if p.lower().startswith("position_")]
        if pos_parts:
            # Nearest Position_* ancestor of this trial file.
            positions.add(pos_parts[-1])
    return positions


def find_optitrack_match(imu_path: str, rec_root: str, opti_root: str) -> Optional[str]:
    """Find the OptiTrack CSV matching a Trial_{n}_imu.csv anchor under the
    mirrored opti_root tree.

    The OptiTrack directory structure is NOT a byte-for-byte mirror of
    Recordings/ at every level -- confirmed by direct inspection of the
    real data on disk: Participant_13_left_post's OptiTrack CSVs sit at
    the fully-mirrored depth (.../Session_post/Position_1/
    Height_Joint-Level/trial_N_optitrack.csv), but
    Participant_13_right_post's sit one directory level higher, directly
    under .../Session_post/trial_N_optitrack.csv. So this walks upward
    from the fully-mirrored guess toward (but never including) opti_root
    itself, checking each ancestor directory's direct children (not
    recursive) for a case-insensitive trial_{n}_optitrack.csv, and returns
    the first match found. Stops at the top-level participant directory
    (never checks opti_root's own direct children) so a same-numbered
    trial file for an unrelated participant sitting loose at opti_root
    can't be mistaken for this trial's match. Returns None if the anchor
    isn't under rec_root, doesn't match the Trial_{n}_imu.csv pattern, or
    no match exists at any scoped level -- never raises for "just not
    found," since a missing OptiTrack counterpart (e.g. the real
    right_post Trial_5) is an expected, logged-not-crashed case.

    Position-collision guard: if the IMU anchor's own path runs through a
    Position_* directory (e.g. .../Session_post/Position_1/
    Height_Joint-Level/Trial_3_imu.csv) and the walk-up has to climb to an
    ancestor at or above that Position_* level to find its match (exactly
    the real right_post case, where the match sits directly under
    Session_post, above Position_1), the match is no longer inherently
    position-scoped -- a same-numbered trial dumped for a second position
    into that same shallow ancestor would silently look like a match too.
    Before trusting such a match, this checks whether more than one
    Position_* subdirectory under that same shallow ancestor (searched on
    the Recordings/ side, where positions are unambiguous) has an IMU
    trial with the same number. If so, the match is genuinely ambiguous:
    this warns and returns None (skip-and-log) rather than silently
    guessing which position it belongs to. If only one position has that
    trial number (the common case, including today's single-Position_1
    dataset), the match is trusted as before."""
    imu_path = os.path.normpath(imu_path)
    rec_root = os.path.normpath(rec_root)
    opti_root = os.path.normpath(opti_root)

    m = _TRIAL_ANCHOR_RE.match(os.path.basename(imu_path))
    if not m:
        return None
    trial_n = m.group(1)
    target_name = f"trial_{trial_n}_optitrack.csv"

    imu_dir = os.path.dirname(imu_path)
    try:
        rel = os.path.relpath(imu_dir, rec_root)
    except ValueError:
        return None  # e.g. different drives on Windows
    if rel == os.curdir or rel.startswith(".."):
        return None

    parts = rel.split(os.sep)

    # Redundant-wrapper fallback (2026-08-11 fix): Recordings/ was
    # reorganized to nest each participant's legacy per-condition folders
    # under one Participant_N/ parent (e.g.
    # Recordings/Participant_13/Participant_13_left_post/...), but
    # OptiTrack_Recordings/ never got that extra wrapper for those legacy
    # folders -- they still sit as flat top-level siblings
    # (OptiTrack_Recordings/Participant_13_left_post/...), alongside a
    # SEPARATE OptiTrack_Recordings/Participant_13/ for newer recordings
    # that genuinely are mirrored. The depth-walk below only ever shrinks
    # from the END of `parts`, so it can never find a match that's missing
    # a component from the FRONT. When parts[0] and parts[1] both name the
    # same participant (the exact double-nesting signature), also try the
    # de-wrapped path with parts[0] dropped, tried strictly after every
    # depth of the direct path so a genuine mirrored match is always
    # preferred over the fallback.
    _pid_re = re.compile(r"^Participant_(\d+)$", re.IGNORECASE)
    part_candidates = [parts]
    if len(parts) > 1:
        m0, m1 = _pid_re.match(parts[0]), re.match(r"^Participant_(\d+)[_/]", parts[1], re.IGNORECASE)
        if m0 and m1 and m0.group(1) == m1.group(1):
            part_candidates.append(parts[1:])

    for candidate_parts in part_candidates:
        pos_idx = next((i for i, p in enumerate(candidate_parts)
                        if p.lower().startswith("position_")), None)

        for depth in range(len(candidate_parts), 0, -1):
            candidate_dir = os.path.join(opti_root, *candidate_parts[:depth])
            if not os.path.isdir(candidate_dir):
                continue
            for entry in os.listdir(candidate_dir):
                if entry.lower() != target_name:
                    continue
                match_path = os.path.join(candidate_dir, entry)
                if pos_idx is not None and depth <= pos_idx:
                    # Walked up to/above the Position_* level -- the match is
                    # no longer inherently scoped to this trial's position.
                    # Verify no sibling position also has this trial number
                    # before trusting it.
                    scope_dir = os.path.join(rec_root, *parts[:len(parts) - len(candidate_parts) + pos_idx])
                    positions = _positions_with_trial(scope_dir, trial_n)
                    if len(positions) > 1:
                        warnings.warn(
                            f"Ambiguous OptiTrack match for {imu_path!r}: "
                            f"candidate {match_path!r} sits above the "
                            f"Position_* level ({scope_dir!r}) and "
                            f"{sorted(positions)} all have a trial "
                            f"{trial_n} IMU file. Skipping rather than "
                            f"guessing which position it belongs to.",
                            stacklevel=2,
                        )
                        return None
                return match_path
    return None


def _parse_trial_identifiers(imu_path: str) -> dict:
    """Parse participant/position/trial identifiers out of the anchor
    path, reusing the existing Recordings/ directory-naming convention
    (e.g. .../Participant_13_right_post/Session_post/Position_1/
    Height_Joint-Level/Trial_3_imu.csv)."""
    parts = os.path.normpath(imu_path).split(os.sep)
    participant = next((p for p in parts if p.startswith("Participant_")), "unknown")
    position = next((p for p in parts if p.startswith("Position_")), "unknown")
    trial_name = os.path.basename(imu_path)
    m = _TRIAL_ANCHOR_RE.match(trial_name)
    trial = f"Trial_{m.group(1)}" if m else trial_name
    return {"participant": participant, "position": position, "trial": trial}


def discover_trials() -> list:
    """Glob Recordings/**/Trial_*_imu.csv, derive each trial's 4 component
    sibling paths and matching OptiTrack path. Trials with no OptiTrack
    match are still returned (optitrack_path=None) so main() can count and
    log them as skipped rather than silently dropping them from the
    discovery output."""
    trials = []
    pattern = os.path.join(REC_ROOT, "**", "Trial_*_imu.csv")
    for imu_path in sorted(glob.glob(pattern, recursive=True)):
        ids = _parse_trial_identifiers(imu_path)
        component_paths = derive_component_paths(imu_path)
        optitrack_path = find_optitrack_match(imu_path, REC_ROOT, OPTI_ROOT)
        trials.append({
            **ids,
            **component_paths,
            "optitrack_path": optitrack_path,
        })
    return trials


def evaluate_trial(imu_path: str, accel_path: str, gyro_path: str,
                   mag_path: str, opti_path: str, ids: Optional[dict] = None) -> dict:
    """Run one trial through validate_component_csv (all 4 components) ->
    load_imu_trial_from_components -> load_optitrack_trial -> compare_pair,
    and flatten the result into one row. Never raises -- any failure
    becomes status="error" with the reason in `error`, so one bad trial
    doesn't crash the whole batch run."""
    if ids is None:
        ids = _parse_trial_identifiers(imu_path)

    row = {
        "participant": ids["participant"],
        "position": ids["position"],
        "trial": ids["trial"],
        "imu_path": imu_path,
        "optitrack_path": opti_path,
        "status": "error",
        "rmse_deg": None,
        "mae_deg": None,
        "bias_deg": None,
        "lag_sec": None,
        "n_samples": None,
        "optitrack_method": None,
        "error": None,
    }

    validations = {
        "accel": engine.validate_component_csv(accel_path, "accel"),
        "gyro": engine.validate_component_csv(gyro_path, "gyro"),
        "mag": engine.validate_component_csv(mag_path, "mag"),
        "imu": engine.validate_component_csv(imu_path, "imu"),
    }
    bad = [kind for kind, v in validations.items() if not v["ok"]]
    if bad:
        row["error"] = "; ".join(
            f"{kind}: {validations[kind]['error']}" for kind in bad)
        return row

    try:
        t, angle, _imu_reference = engine.load_imu_trial_from_components(validations)
    except Exception as e:
        row["error"] = f"load_imu_trial_from_components failed: {type(e).__name__}: {e}"
        return row

    try:
        ref_t, ref_angle, method = engine.load_optitrack_trial(opti_path)
    except Exception as e:
        row["error"] = f"load_optitrack_trial failed: {type(e).__name__}: {e}"
        return row
    row["optitrack_method"] = method

    result = engine.compare_pair(ref_t, ref_angle, t, angle)
    if result["status"] != "ok":
        row["error"] = result.get("error")
        return row

    row["status"] = "ok"
    row["rmse_deg"] = result["rmse_deg"]
    row["mae_deg"] = result["mae_deg"]
    row["bias_deg"] = result["bias_deg"]
    row["lag_sec"] = result["lag_sec"]
    row["n_samples"] = result["n_samples"]
    return row


_FIELDNAMES = ["participant", "position", "trial", "imu_path", "optitrack_path",
              "status", "rmse_deg", "mae_deg", "bias_deg", "lag_sec",
              "n_samples", "optitrack_method", "error"]


def main():
    trials = discover_trials()
    print(f"Discovered {len(trials)} Trial_*_imu.csv anchor(s) under {REC_ROOT}")

    unmatched = [t for t in trials if t["optitrack_path"] is None]
    matched = [t for t in trials if t["optitrack_path"] is not None]
    for t in unmatched:
        print(f"  [skip] {t['participant']} {t['position']} {t['trial']}: "
              f"no matching OptiTrack CSV found")

    rows = []
    for t in matched:
        row = evaluate_trial(t["imu"], t["accel"], t["gyro"], t["mag"],
                             t["optitrack_path"], ids=t)
        rows.append(row)
        if row["status"] == "ok":
            print(f"  [ok]   {row['participant']} {row['position']} {row['trial']}: "
                  f"rmse={row['rmse_deg']:.3f} deg (n={row['n_samples']}, "
                  f"optitrack_method={row['optitrack_method']})")
        else:
            print(f"  [error] {row['participant']} {row['position']} {row['trial']}: "
                  f"{row['error']}")

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved: {OUT_CSV}")

    ok_rmse = [r["rmse_deg"] for r in rows if r["status"] == "ok"]
    n_error = len(rows) - len(ok_rmse)
    print(f"\nSummary: {len(trials)} trial(s) discovered, "
          f"{len(unmatched)} skipped (no OptiTrack match), "
          f"{len(ok_rmse)} evaluated successfully, {n_error} error(s).")
    if ok_rmse:
        mean_rmse = statistics.mean(ok_rmse)
        median_rmse = statistics.median(ok_rmse)
        n_under_goal = sum(1 for r in ok_rmse if r < RMSE_GOAL_DEG)
        print(f"RMSE (deg): mean={mean_rmse:.3f}, median={median_rmse:.3f}, "
              f"{n_under_goal}/{len(ok_rmse)} trial(s) under the "
              f"{RMSE_GOAL_DEG}-degree goal.")
    else:
        print("No successful trials to summarize.")


if __name__ == "__main__":
    main()
