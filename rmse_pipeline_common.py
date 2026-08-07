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
import hashlib
import json
import os
import re

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
