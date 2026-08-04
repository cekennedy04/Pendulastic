"""
pendulastic_storage.py
=======================
Local, per-participant persistence for the Workbench's longitudinal
dashboard: participants/{id}/history.json. Purely local -- no relation to
web/api's separate in-memory participant_db/trial_db (a different app).

See docs/superpowers/specs/2026-08-04-longitudinal-dashboard-design.md.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

PARTICIPANTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "participants")


def normalize_participant_id(participant_id: str) -> str:
    """" p5 ", "p5", and "P5" must all resolve to the same
    participants/P5/history.json, or a typo'd case/whitespace variant
    silently creates a duplicate participant folder."""
    return participant_id.strip().upper()


def _history_path(participant_id: str) -> str:
    return os.path.join(PARTICIPANTS_DIR, normalize_participant_id(participant_id),
                        "history.json")


def _empty_history(participant_id: str) -> dict:
    return {
        "participant_id": normalize_participant_id(participant_id),
        "legs": {"left": {"sessions": []}, "right": {"sessions": []}},
    }


def _session_skip_reason(session) -> str:
    """Returns "" if session is well-formed, else the reason it's being
    skipped."""
    if not isinstance(session, dict):
        return "not a dict"
    for key in ("label", "date", "reference_trace", "traces"):
        if key not in session:
            return f"missing '{key}'"
    try:
        datetime.fromisoformat(session["date"])
    except (ValueError, TypeError):
        return f"unparseable date {session.get('date')!r}"
    return ""


def load_history(participant_id: str) -> dict:
    """Defensive read: a missing file, corrupt JSON, missing keys, or a
    malformed session never raises -- each problem is either defaulted or
    the offending session is skipped and reported in "_skipped", so a
    shorter-than-expected history reads as "corrupted", not "this trial
    was never recorded" (design spec Section 5)."""
    pid = normalize_participant_id(participant_id)
    try:
        with open(_history_path(pid), "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return _empty_history(pid)

    if not isinstance(raw, dict):
        return _empty_history(pid)

    history = _empty_history(pid)
    skipped = []
    legs = raw.get("legs")
    if isinstance(legs, dict):
        for leg in ("left", "right"):
            leg_data = legs.get(leg)
            if not isinstance(leg_data, dict):
                continue
            sessions = leg_data.get("sessions")
            if not isinstance(sessions, list):
                continue
            kept = []
            for session in sessions:
                reason = _session_skip_reason(session)
                if reason:
                    msg = f"skipped malformed session for {pid}/{leg}: {reason}"
                    logger.warning(msg)
                    skipped.append(msg)
                else:
                    kept.append(session)
            history["legs"][leg]["sessions"] = kept
    if skipped:
        history["_skipped"] = skipped
    return history


def save_trial(participant_id: str, leg: str, session_label: str, date: str,
              traces: dict, metrics_by_label: dict, reference_trace: str) -> None:
    """Upserts one session into participants/{id}/history.json under
    legs[leg]["sessions"]. Matches on (session_label, date): a session
    already present with that exact label+date is replaced in place
    (guards against a researcher re-saving the same trial after
    reprocessing, or double-clicking Save); any other label/date
    combination is appended. Writes atomically (temp file + os.replace,
    matching imu_calibration_config.save_config's precedent) so a crash
    mid-write can't corrupt the file. Never touches other legs' or other
    (label, date) sessions."""
    if leg not in ("left", "right"):
        raise ValueError(f"leg must be 'left' or 'right', got {leg!r}")
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}") from e

    pid = normalize_participant_id(participant_id)
    history = load_history(pid)
    history.pop("_skipped", None)

    session = {
        "label": session_label,
        "date": date,
        "reference_trace": reference_trace,
        "traces": {
            label: {
                "t": list(t),
                "angle": list(angle),
                "metrics": metrics_by_label[label],
            }
            for label, (t, angle) in traces.items()
        },
    }

    sessions = history["legs"][leg]["sessions"]
    for i, existing in enumerate(sessions):
        if existing["label"] == session_label and existing["date"] == date:
            sessions[i] = session
            break
    else:
        sessions.append(session)

    dir_path = os.path.join(PARTICIPANTS_DIR, pid)
    os.makedirs(dir_path, exist_ok=True)
    path = os.path.join(dir_path, "history.json")
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)
    os.replace(tmp_path, path)
