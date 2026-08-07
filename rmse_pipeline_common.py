"""
rmse_pipeline_common.py
========================
Discovery, scoring, caching, ranking, and promotion engine for continuous
IMU-vs-OptiTrack and MediaPipe-vs-OptiTrack RMSE validation.

Wraps and generalizes batch_imu_vs_optitrack_rmse.py, sweep_imu_config.py,
sweep_mediapipe_config.py, and batch_mediapipe.py's proven discovery/scoring
primitives without modifying any of them. See
docs/superpowers/specs/2026-08-07-rmse-validation-pipeline-design.md for the
full design (three rounds of Codex review folded into the spec text -- read
its revision note first).
"""
from __future__ import annotations

import batch_imu_vs_optitrack_rmse as imu_discovery
import cv2
import glob
import hashlib
import inspect
import json
import mediapipe
import numpy
import numpy as np
import os
import pickle
import pt_report_common
import re
import scipy
import sys

import imu_calibration_tuner
import pendulastic_pt_score as pt_score
import sweep_mediapipe_config as mediapipe_sweep
import workbench_engine as engine
from reconstruct_imu_raw_logs import reconstruct_trial

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REC_ROOT = os.path.join(BASE_DIR, "Recordings")
OPTI_ROOT = os.path.join(BASE_DIR, "OptiTrack_Recordings")
SWEEP_CACHE_DIR = os.path.join(BASE_DIR, "sweep_cache")
RMSE_TRACKING_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "RMSE_Tracking")
BEST_CONFIG_JSON = os.path.join(BASE_DIR, "rmse_best_config.json")

_PLACEHOLDER = "none"


def _norm(s):
    return s.strip().lower() if s else _PLACEHOLDER


def parse_structural_fields(path, root):
    """Extract the seven canonical structural identity fields from a trial
    path (design spec §4). Models pt_report_common._parse_trial_path's
    proven participant/leg extraction, extended to also capture session,
    position, and height as independent fields (not merged into one
    "condition" string -- design spec §4, tightened in the third Codex
    review round to keep condition and session distinct).

    position and height are frequently absent from the real folder
    structure (e.g. Participant_13_right_post's OptiTrack CSVs sit one
    level higher than left_post's, with no Position_/Height_ segment at
    all) -- both default to the "none" placeholder rather than causing a
    parse failure. participant, leg, and trial_number are required; a path
    that can't resolve all three, or that matches more than one distinct
    participant number (a known archived-data nesting issue), returns None
    rather than guessing."""
    rel = os.path.relpath(path, root).replace("\\", "/")

    pids = sorted(set(m.group(1) for m in re.finditer(r"Participant_(\d+)", rel, re.I)))
    if len(pids) != 1:
        return None
    participant = pids[0]

    m_leg = re.search(r"(?:^|[_/])(left|right)(?:[_/]|$)", rel, re.I)
    if not m_leg:
        return None
    leg = m_leg.group(1).lower()

    m_trial = re.search(r"trial[_\s]*(\d+)", os.path.basename(path), re.I)
    if not m_trial:
        return None
    trial_number = m_trial.group(1)

    session = _PLACEHOLDER
    m_session = re.search(r"(?:^|/)Session_([^/]+)", rel, re.I)
    if m_session:
        session = _norm(m_session.group(1))

    position = _PLACEHOLDER
    m_position = re.search(r"(?:^|/)Position_([^/]+)", rel, re.I)
    if m_position:
        position = _norm(m_position.group(1))

    height = _PLACEHOLDER
    m_height = re.search(r"(?:^|/)Height_([^/]+)", rel, re.I)
    if m_height:
        height = _norm(m_height.group(1))

    # condition: same folder-name-cleanup approach as
    # pt_report_common._parse_trial_path -- strip the participant prefix,
    # the leg token, and any Session_/Position_/Height_ segments, keep
    # whatever's left of the parent-directory chain, deduplicated.
    parts = rel.split("/")[:-1]
    cond_parts = []
    for part in parts:
        low = part.lower()
        if low.startswith("session_") or low.startswith("position_") or low.startswith("height_"):
            continue
        cleaned = part
        if low.startswith("participant_"):
            cleaned = re.sub(r"^participant_\d+_?", "", cleaned, flags=re.I)
        cleaned = re.sub(r"(left|right)", "", cleaned, flags=re.I).strip("_")
        if cleaned:
            cond_parts.append(_norm(cleaned))
    condition = "_".join(dict.fromkeys(cond_parts)) or _PLACEHOLDER

    return {
        "participant": participant, "leg": leg, "condition": condition,
        "session": session, "position": position, "height": height,
        "trial_number": trial_number,
    }


_TRIAL_KEY_FIELDS = ("participant", "leg", "condition", "session",
                    "position", "height", "trial_number")


def compute_trial_key(fields):
    """Deterministic hash of the canonical structural tuple (design spec
    §4, position bug fixed in the third Codex review round). Never hashes
    which source files exist -- that would break identity stability across
    a capability change (e.g. a video added later for a previously
    IMU-only capture)."""
    canonical = {"v": 1, **{k: fields[k] for k in _TRIAL_KEY_FIELDS}}
    blob = json.dumps(canonical, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def discover_imu_trials():
    """Every IMU trial with matched split-CSV components, via
    batch_imu_vs_optitrack_rmse.discover_trials() (reused as-is -- its
    component-path derivation and OptiTrack matching are already correct
    and tested). Re-parsed through parse_structural_fields() for the
    canonical trial_key rather than trusting the source script's own
    participant/position labels, which don't capture session/height."""
    out = []
    for t in imu_discovery.discover_trials():
        fields = parse_structural_fields(t["imu"], REC_ROOT)
        if fields is None:
            continue
        out.append({
            **fields,
            "trial_key": compute_trial_key(fields),
            "imu_anchor_path": t["imu"],
            "imu_component_paths": {"imu": t["imu"], "accel": t["accel"],
                                    "gyro": t["gyro"], "mag": t["mag"]},
            "optitrack_path": t["optitrack_path"],
        })
    return out


def discover_video_trials():
    """Every trial with an OptiTrack CSV and a matching video, walking
    OPTI_ROOT the same way batch_mediapipe.discover_new_trials() does
    (credited convention, not a call into that generator -- its
    CSV/annotated-video existence flags and print() side effects are
    specific to its own batch-processing pipeline, not relevant here).
    Video may sit beside the OptiTrack CSV itself, or under the mirrored
    Recordings/ tree -- both are checked, matching the real observed
    layout variance across participants."""
    out = []
    pattern = os.path.join(OPTI_ROOT, "**", "*_optitrack.csv")
    for opti_path in sorted(glob.glob(pattern, recursive=True)):
        m = re.match(r"trial_(\d+)_optitrack\.csv", os.path.basename(opti_path), re.I)
        if not m:
            continue
        trial_n = m.group(1)
        opti_dir = os.path.dirname(opti_path)
        rel = os.path.relpath(opti_dir, OPTI_ROOT)
        rec_dir = os.path.join(REC_ROOT, rel)

        # Build candidate basenames to look for (order: opti_dir first, then rec_dir)
        candidate_basenames = [
            f"trial_{trial_n}.mp4",
            f"Trial_{trial_n}.mp4",
            f"trial_{trial_n}.avi",
            f"Trial_{trial_n}.avi",
        ]

        video_path = None
        # Check opti_dir first, then rec_dir
        for dirname in [opti_dir, rec_dir]:
            if video_path:
                break
            if os.path.isdir(dirname):
                # List directory files once, then check candidates in priority order
                dir_files = os.listdir(dirname)
                for candidate_basename in candidate_basenames:
                    if candidate_basename in dir_files:
                        video_path = os.path.join(dirname, candidate_basename)
                        break

        if video_path is None:
            continue

        fields = parse_structural_fields(opti_path, OPTI_ROOT)
        if fields is None:
            continue
        out.append({
            **fields,
            "trial_key": compute_trial_key(fields),
            "video_path": video_path,
            "optitrack_path": opti_path,
        })
    return out


def discover_scorable_trials():
    """Merge discover_imu_trials()/discover_video_trials() by trial_key
    into TrialRecords with per-methodology capability flags (design spec
    §4). A trial_key with no optitrack_path on the side(s) that produced
    it, or with disagreeing optitrack_path values across sides, is
    excluded rather than heuristically resolved -- a silent wrong pairing
    is worse than a skipped trial."""
    by_key = {}
    for imu in discover_imu_trials():
        if not imu["optitrack_path"]:
            continue
        rec = by_key.setdefault(imu["trial_key"], {
            **{k: imu[k] for k in _TRIAL_KEY_FIELDS},
            "trial_key": imu["trial_key"], "optitrack_path": imu["optitrack_path"],
            "imu_anchor_path": None, "imu_component_paths": None, "video_path": None,
            "has_imu_rmse": False, "has_mediapipe_rmse": False, "exclusion_reasons": [],
        })
        if rec["optitrack_path"] != imu["optitrack_path"]:
            rec["exclusion_reasons"].append("conflicting_optitrack_path")
            continue
        rec["imu_anchor_path"] = imu["imu_anchor_path"]
        rec["imu_component_paths"] = imu["imu_component_paths"]
        rec["has_imu_rmse"] = True

    for vid in discover_video_trials():
        if not vid["optitrack_path"]:
            continue
        rec = by_key.setdefault(vid["trial_key"], {
            **{k: vid[k] for k in _TRIAL_KEY_FIELDS},
            "trial_key": vid["trial_key"], "optitrack_path": vid["optitrack_path"],
            "imu_anchor_path": None, "imu_component_paths": None, "video_path": None,
            "has_imu_rmse": False, "has_mediapipe_rmse": False, "exclusion_reasons": [],
        })
        if rec["optitrack_path"] != vid["optitrack_path"]:
            rec["exclusion_reasons"].append("conflicting_optitrack_path")
            continue
        rec["video_path"] = vid["video_path"]
        rec["has_mediapipe_rmse"] = True

    excluded = pt_report_common.load_excluded_trials()
    kept = []
    for rec in by_key.values():
        if rec["exclusion_reasons"]:
            continue
        legacy_key = pt_report_common.trial_key(
            rec["participant"], rec["leg"], rec["condition"], rec["trial_number"])
        if legacy_key in excluded:
            continue
        kept.append(rec)
    return kept


def sha256_file(path, stat_cache, force=False):
    """Content hash of one file, gated by a size/mtime pre-filter --
    unchanged stat reuses the cached digest from a prior call within the
    same stat_cache dict, no re-read. force=True always re-hashes,
    bypassing the pre-filter entirely (needed by the watcher plan's
    reconciliation pass, which is the correctness safety net and must not
    share this speed optimization's blind spot -- design spec §7.1's
    third-round correction)."""
    st = os.stat(path)
    stat_key = (path, st.st_size, st.st_mtime_ns)
    if not force and stat_key in stat_cache:
        return stat_cache[stat_key][1]
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    stat_cache[stat_key] = (stat_key, digest)
    return digest


def compute_input_fingerprints(trial, methodology, stat_cache, force=False):
    """Per design spec §7.1: every input file a candidate's score actually
    depends on, for the given methodology. optitrack is always included;
    imu's four split CSVs are included for methodology="imu", the video
    for methodology="mediapipe"."""
    fps = {"optitrack": sha256_file(trial["optitrack_path"], stat_cache, force=force)}
    if methodology == "imu":
        fps["imu"] = {name: sha256_file(p, stat_cache, force=force)
                      for name, p in trial["imu_component_paths"].items()}
    elif methodology == "mediapipe":
        fps["video"] = sha256_file(trial["video_path"], stat_cache, force=force)
    else:
        raise ValueError(f"unknown methodology: {methodology!r}")
    return fps


_FINGERPRINTED_MODULES = ("rmse_pipeline_common", "workbench_engine",
                          "imu_calibration_tuner", "reconstruct_imu_raw_logs",
                          "sweep_imu_config", "sweep_mediapipe_config", "batch_mediapipe")


def compute_implementation_fingerprint():
    """Hash of everything that can silently change a candidate's score
    without touching any trial's input files: both grids (imported live,
    per Global Constraints), the source of every module this pipeline's
    scoring path depends on, and the installed numpy/scipy/opencv/
    mediapipe package versions (design spec §7.1)."""
    import sweep_imu_config
    import sweep_mediapipe_config

    parts = [
        json.dumps(sweep_imu_config.WIDE_GRID, sort_keys=True),
        json.dumps({"model_variants": sweep_mediapipe_config.MODEL_VARIANTS,
                    "vis_thresh": sweep_mediapipe_config.VIS_THRESH_CANDIDATES},
                   sort_keys=True),
    ]
    for mod_name in _FINGERPRINTED_MODULES:
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        try:
            parts.append(inspect.getsource(mod))
        except (OSError, TypeError):
            pass
    parts.append(f"numpy={numpy.__version__}")
    parts.append(f"scipy={scipy.__version__}")
    parts.append(f"opencv={cv2.__version__}")
    parts.append(f"mediapipe={mediapipe.__version__}")

    blob = "\x00".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def score_imu_candidate(trial, params):
    """RMSE-vs-OptiTrack for one IMU candidate config on one trial. Reuses
    reconstruct_imu_raw_logs.reconstruct_trial() to build the raw sample
    stream, imu_calibration_tuner.replay_trial() to run the AHRS/fusion
    candidate, and workbench_engine.compare_pair() to score -- the same
    pipeline sweep_imu_config.py's score_config() already uses per-trial
    (design spec §5). Returns None if fewer than 10 finite angle samples
    result (unscoreable, matching sweep_imu_config.py's own threshold) or
    if compare_pair reports a non-ok status."""
    comp = trial["imu_component_paths"]
    samples = reconstruct_trial(comp["accel"], comp["gyro"], comp["mag"])
    if not samples:
        return None
    t_m, ang_m = imu_calibration_tuner.replay_trial(samples, params)
    if np.count_nonzero(np.isfinite(ang_m)) < 10:
        return None
    opti_t, opti_ang = pt_score.load_optitrack(trial["optitrack_path"])
    result = engine.compare_pair(opti_t, opti_ang, t_m, ang_m)
    if result.get("status") != "ok":
        return None
    return result["rmse_deg"]


_LANDMARK_CACHE_DIR = lambda: os.path.join(SWEEP_CACHE_DIR, "landmarks")


def extract_landmarks_cached(trial, model_variant, model_path):
    """Raw per-frame landmark extraction, cached separately from the
    per-config RMSE cache (design spec §7.1, added in the second Codex
    review round) -- a per-(trial, full-config) RMSE cache alone would
    re-run MediaPipe inference every time vis_thresh changes even though
    only the cheap re-thresholding step actually depends on it. Cache key:
    (trial_key, model_variant, video content hash)."""
    stat_cache = {}
    video_fp = sha256_file(trial["video_path"], stat_cache)
    cache_dir = _LANDMARK_CACHE_DIR()
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(
        cache_dir, f"{trial['trial_key']}_{model_variant}_{video_fp}.pkl")
    if os.path.isfile(cache_file):
        with open(cache_file, "rb") as f:
            return pickle.load(f)
    frames = mediapipe_sweep.extract_raw_landmarks(trial["video_path"], trial["leg"], model_path)
    tmp_path = cache_file + ".tmp"
    with open(tmp_path, "wb") as f:
        pickle.dump(frames, f)
    os.replace(tmp_path, cache_file)
    return frames


def score_mediapipe_candidate(trial, model_variant, model_path, vis_thresh):
    """RMSE-vs-OptiTrack for one MediaPipe candidate (model_variant,
    vis_thresh) on one trial. Landmark extraction is cached and reused
    across every vis_thresh candidate for the same (trial, model_variant)
    -- only workbench_engine.compare_pair's cheap re-thresholding runs per
    candidate (design spec §5, §7.1)."""
    frames = extract_landmarks_cached(trial, model_variant, model_path)
    opti_t, opti_ang = pt_score.load_optitrack(trial["optitrack_path"])
    return mediapipe_sweep.score_frames(frames, opti_t, opti_ang, vis_thresh)


def compute_cache_key(methodology, trial, candidate, input_fingerprints, implementation_fingerprint):
    """Design spec §7.1: content-addressed, not size/mtime -- depends on
    the trial's identity, the exact candidate config, every input file's
    current content, and the current implementation fingerprint, so a code
    fix or grid change naturally misses cache rather than silently serving
    a stale result."""
    canonical = {
        "schema": 2, "methodology": methodology, "trial_key": trial["trial_key"],
        "candidate": candidate, "input_fingerprints": input_fingerprints,
        "implementation_fingerprint": implementation_fingerprint,
    }
    blob = json.dumps(canonical, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _sweep_cache_manifest_path():
    return os.path.join(SWEEP_CACHE_DIR, "manifest.json")


def load_sweep_cache():
    """{cache_key: rmse_deg} manifest. Missing or malformed file -> empty
    dict (defensive pattern matching pt_cohort_common.load_registry() --
    this is a file a human could plausibly delete or hand-edit)."""
    path = _sweep_cache_manifest_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"{path} failed to parse -- treating as empty.")
        return {}


def save_sweep_cache(cache):
    os.makedirs(SWEEP_CACHE_DIR, exist_ok=True)
    tmp_path = _sweep_cache_manifest_path() + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
    os.replace(tmp_path, _sweep_cache_manifest_path())


def rank_candidates(candidate_scores, cohort, participant_of, min_participants=3):
    """Design spec §7.2: one frozen ranking cohort (every eligible trial
    for this methodology), every candidate scored against the same cohort.
    A candidate that fails to score on a required cohort trial is marked
    low_coverage and reported but excluded from the winner entirely (never
    aggregated over an easier subset -- this is what makes "same scored
    subset" literally true rather than aspirational). Coverage is full
    coverage: a candidate is only ranking-eligible if it scored every
    trial in the cohort -- a fractional floor would let a candidate win
    by cherry-picking away the hardest trial, which is exactly the bug
    this function exists to prevent (caught in task review after the
    original 80%-floor draft let a 4/5-scoring candidate beat a
    5/5-scoring one). If the cohort itself has fewer than min_participants
    distinct participants, ranking is skipped for this sweep entirely
    (returns [])."""
    cohort_participants = {participant_of[t] for t in cohort}
    if len(cohort_participants) < min_participants:
        return []

    required_n = len(cohort)
    rows = []
    for candidate_key, per_trial in candidate_scores.items():
        scored_in_cohort = [t for t in cohort if t in per_trial]
        n_trials = len(scored_in_cohort)
        n_participants = len({participant_of[t] for t in scored_in_cohort})
        low_coverage = n_trials < required_n or n_participants < min_participants
        median_rmse = (float(np.median([per_trial[t] for t in scored_in_cohort]))
                       if scored_in_cohort else None)
        rows.append({"candidate_key": candidate_key, "median_rmse": median_rmse,
                    "n_trials": n_trials, "n_participants": n_participants,
                    "low_coverage": low_coverage})

    winners = [r for r in rows if not r["low_coverage"]]
    winners.sort(key=lambda r: r["median_rmse"])
    losers = [r for r in rows if r["low_coverage"]]
    return winners + losers


def load_best_config():
    """Missing/malformed file -> the empty structure, not an error --
    matches pt_cohort_common.load_registry()'s defensive pattern."""
    if not os.path.isfile(BEST_CONFIG_JSON):
        return {"mediapipe": None, "imu": None, "history": []}
    try:
        with open(BEST_CONFIG_JSON, encoding="utf-8") as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"{BEST_CONFIG_JSON} failed to parse -- treating as empty.")
        return {"mediapipe": None, "imu": None, "history": []}
    cfg.setdefault("mediapipe", None)
    cfg.setdefault("imu", None)
    cfg.setdefault("history", [])
    return cfg


def _save_best_config(cfg):
    tmp_path = BEST_CONFIG_JSON + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
    os.replace(tmp_path, BEST_CONFIG_JSON)


def record_sweep_result(methodology, ranked, dataset_fingerprint,
                        implementation_fingerprint, epsilon=0.1):
    """Design spec §5/§7.3: every sweep re-scores/re-ranks the incumbent on
    the SAME current cohort as every challenger (ranked already reflects
    this -- rank_candidates scores every candidate, including whatever
    config load_best_config() currently holds, against this sweep's
    cohort), so promotion is always apples-to-apples. epsilon is in
    absolute RMSE degrees (design spec §5).

    Edge case (design spec §5, third Codex review round): if the
    incumbent's exact config isn't present in `ranked` at all (e.g.
    dropped from a hand-edited grid), it's no longer rankable -- promote
    the best valid candidate from this sweep instead of keeping a stale,
    no-longer-comparable RMSE. If no candidate in `ranked` is valid
    (not low_coverage), current best becomes unavailable (None) rather
    than silently retaining an old number."""
    cfg = load_best_config()
    incumbent = cfg.get(methodology)
    valid = [r for r in ranked if not r["low_coverage"]]
    best_this_sweep = valid[0] if valid else None

    incumbent_still_ranked = None
    if incumbent is not None:
        incumbent_still_ranked = next(
            (r for r in ranked if r["candidate_key"] == incumbent["config"]
             and not r["low_coverage"]), None)

    promote = False
    if best_this_sweep is None:
        new_entry = None
    elif incumbent is None or incumbent_still_ranked is None:
        promote = True
        new_entry = best_this_sweep
    elif incumbent_still_ranked["median_rmse"] < best_this_sweep["median_rmse"] + epsilon:
        new_entry = None  # incumbent (re-scored) still wins or challenger's edge is within epsilon
    else:
        promote = True
        new_entry = best_this_sweep

    if best_this_sweep is None and incumbent is not None and incumbent_still_ranked is None:
        # No valid candidate this sweep AND the incumbent itself couldn't be
        # re-ranked -- current best becomes unavailable, not stale.
        cfg[methodology] = None
        _save_best_config(cfg)
        return {"promoted": False, "reason": "no_valid_candidate"}

    if promote:
        cfg[methodology] = {
            "config": new_entry["candidate_key"], "rmse": new_entry["median_rmse"],
            "n_trials": new_entry["n_trials"], "n_participants": new_entry["n_participants"],
        }
        cfg["history"].append({
            "methodology": methodology, "config": new_entry["candidate_key"],
            "rmse": new_entry["median_rmse"], "dataset_fingerprint": dataset_fingerprint,
            "implementation_fingerprint": implementation_fingerprint,
            "n_trials": new_entry["n_trials"], "n_participants": new_entry["n_participants"],
        })
        _save_best_config(cfg)
        return {"promoted": True}

    _save_best_config(cfg)
    return {"promoted": False, "reason": "within_epsilon"}
