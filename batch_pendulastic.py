#!/usr/bin/env python3
"""
batch_pendulastic.py
--------------------
Run the Pendulastic ArcTracker headlessly on every trial in Recordings/ and
export per-frame knee-angle CSVs for comparison against OptiTrack and all
other HPE models via hpe_mas_evaluation.py / gen_figures.py.

v2.0 strategy — primary-direct with ArcTracker segmentation:
  1. Best-seed selection: tries ALL model CSVs, picks the one whose hold-phase
     top-25% gives the highest extension angle (correct leg, correct pivot).
  2. Primary model: picks the most reliable available model (mediapipe_full >
     mediapipe_heavy > rtmpose_l > ...) for angle output.
  3. ArcTracker segmentation:
     - Pre-release (_motion_detected=False): output primary model's CONSTANT
       hold-median angle (no fluctuation → no false N counts).
     - Post-release (_motion_detected=True): output primary model's per-frame
       angle directly (same model throughout → correct A0 scale).
     - Fallback: ArcTracker angle for frames where primary model has no coverage.

Output per trial:
  Recordings/{participant}/Position_N/Height_Joint-Level/
    P_{pid}_Pos_{pos}_H_Joint-Level_T_{trial}_pendulastic_2.0.csv

Usage:
  .venv/Scripts/python.exe batch_pendulastic.py
  .venv/Scripts/python.exe batch_pendulastic.py --overwrite
  .venv/Scripts/python.exe batch_pendulastic.py --participant P_10
"""

from __future__ import annotations
import argparse, csv, math, os, re, sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from pendulastic_viewer import _ArcTracker

REC_ROOT  = ROOT / "Recordings"
MODEL_TAG = "pendulastic_2.0"

# All candidate seed model suffixes — tried in order of preference as fallback
# but the best by seed-angle metric wins regardless
_SEED_CANDIDATES = [
    "mediapipe_lite_0.5",    # best coverage (41 trials, 82.9%)
    "mediapipe_full_0.5",
    "mediapipe_heavy_0.5",
    "mediapipe_2_0.5",
    "mediapipe_1_0.5",
    "mediapipe_0_0.5",
    "rtmpose_l_0.3",
    "rtmpose_m_0.3",
    "rtmpose_s_0.3",
    "hrnet_w48_0.3",
    "pose2sim_lightweight_0.3",
    "movenet_thunder_0.3",
    "vitpose_base_0.3",
]

# Preferred order for primary blend model (highest swing coverage + accuracy first)
_PRIMARY_PREFS = [
    "mediapipe_lite_0.5",    # most swing coverage; 82.9% accuracy on 41 trials
    "mediapipe_full_0.5",    # slightly higher per-trial accuracy; less coverage
    "mediapipe_heavy_0.5",
    "mediapipe_2_0.5",
    "mediapipe_1_0.5",
    "mediapipe_0_0.5",
    "rtmpose_l_0.3",
    "rtmpose_m_0.3",
    "rtmpose_s_0.3",
    "hrnet_w48_0.3",
    "pose2sim_lightweight_0.3",
]

_SEED_START   = 2     # skip first N frames (detection jitter)
_SEED_END     = 100   # extended hold-phase window
_SEED_MIN_VIS = 0.40  # min landmark visibility
_MIN_RADIUS   = 50    # minimum shank length in pixels


# ── Discovery helpers ─────────────────────────────────────────────────────────

def find_all_seed_csvs(folder: Path, trial: int) -> list[Path]:
    seen  = set()
    paths = []
    for pref in _SEED_CANDIDATES:
        for hit in folder.glob(f"*_T_{trial}_{pref}.csv"):
            if hit not in seen:
                seen.add(hit)
                paths.append(hit)
    # Also include any other model CSVs we haven't explicitly listed
    for f in sorted(folder.glob(f"*_T_{trial}_*.csv")):
        if "pendulastic" not in f.name and f not in seen:
            seen.add(f)
            paths.append(f)
    return paths


def find_video(folder: Path, trial: int) -> Path | None:
    for f in folder.iterdir():
        if re.match(rf"^[Tt]rial_{trial}\.(mp4|avi|mov)$", f.name, re.I):
            return f
    return None


def out_csv_path(folder: Path, trial: int) -> Path:
    any_csv = next(folder.glob(f"*_T_{trial}_*.csv"), None)
    if any_csv:
        m = re.match(r"(.+?_T_\d+)_", any_csv.name)
        if m:
            return folder / f"{m.group(1)}_{MODEL_TAG}.csv"
    return folder / f"T_{trial}_{MODEL_TAG}.csv"


def discover_trials() -> list[tuple[Path, int]]:
    trials = []
    pat    = re.compile(r"^[Tt]rial_(\d+)\.(mp4|avi|mov)$", re.I)
    for pdir in sorted(REC_ROOT.iterdir()):
        if not pdir.is_dir() or pdir.name.startswith("LiveCapture"):
            continue
        for pos_dir in sorted(pdir.iterdir()):
            if not pos_dir.is_dir() or not pos_dir.name.startswith("Position_"):
                continue
            folder = pos_dir / "Height_Joint-Level"
            if not folder.is_dir():
                continue
            for f in sorted(folder.iterdir()):
                m = pat.match(f.name)
                if m:
                    trials.append((folder, int(m.group(1))))
    return trials


# ── Seed extraction ───────────────────────────────────────────────────────────

def _stable_seed(csv_path: Path):
    """
    Extract (leg, hip_px, knee_px, ankle_px) from the most-extended hold frames.
    Returns None on failure.
    """
    try:
        df = pd.read_csv(csv_path)
    except Exception as exc:
        print(f"    WARN: cannot read seed CSV — {exc}")
        return None

    df.columns = df.columns.str.strip()
    need = {"hip_x", "hip_y", "knee_x", "knee_y", "ankle_x", "ankle_y"}
    if not need.issubset(df.columns):
        return None

    leg = "right"
    if "leg" in df.columns:
        vals = df["leg"].dropna()
        if len(vals):
            leg = str(vals.iloc[0]).strip()

    window = df.iloc[_SEED_START:_SEED_END].copy()

    # Filter by landmark visibility
    if "knee_score" in df.columns and "ankle_score" in df.columns:
        window = window[
            (window["knee_score"]  >= _SEED_MIN_VIS) &
            (window["ankle_score"] >= _SEED_MIN_VIS)
        ]

    if len(window) < 3:
        window = df.iloc[_SEED_START:min(_SEED_START + 20, len(df))]
    if len(window) == 0:
        return None

    # KEY IMPROVEMENT: use top-25% most-extended frames (highest knee angle)
    if "knee_angle_deg" in window.columns:
        valid_ang = window.dropna(subset=["knee_angle_deg"])
        if len(valid_ang) >= 5:
            thresh   = valid_ang["knee_angle_deg"].quantile(0.75)
            extended = valid_ang[valid_ang["knee_angle_deg"] >= thresh]
            if len(extended) >= 3:
                window = extended

    hip   = np.array([window["hip_x"].median(),   window["hip_y"].median()],   dtype=np.float64)
    knee  = np.array([window["knee_x"].median(),  window["knee_y"].median()],  dtype=np.float64)
    ankle = np.array([window["ankle_x"].median(), window["ankle_y"].median()], dtype=np.float64)

    if not (np.isfinite(knee).all() and np.isfinite(ankle).all() and
            np.isfinite(hip).all()):
        return None
    if float(np.linalg.norm(ankle - knee)) < _MIN_RADIUS:
        return None

    return leg, hip, knee, ankle


def _seed_angle(leg, hip, knee, ankle) -> float:
    v_h = hip - knee
    v_a = ankle - knee
    nh, na = np.linalg.norm(v_h), np.linalg.norm(v_a)
    if nh < 1 or na < 1:
        return 0.0
    cos_a = float(np.clip(np.dot(v_h, v_a) / (nh * na), -1.0, 1.0))
    return math.degrees(math.acos(cos_a))


def find_best_seed(folder: Path, trial: int):
    """
    Try all available model CSVs as seeds. Return the one whose initial
    extension angle is highest (most extended = best camera coverage).
    Returns (csv_path, leg, hip, knee, ankle, angle_deg) or None.
    """
    best_angle  = -1.0
    best_result = None

    for csv_path in find_all_seed_csvs(folder, trial):
        result = _stable_seed(csv_path)
        if result is None:
            continue
        leg, hip, knee, ankle = result
        ang = _seed_angle(leg, hip, knee, ankle)
        if ang > best_angle:
            best_angle  = ang
            best_result = (csv_path, leg, hip, knee, ankle, ang)

    return best_result


# ── Primary model helpers ─────────────────────────────────────────────────────

def find_primary_from_csvs(all_seed_csvs: list[Path], leg: str,
                            min_swing_frames: int = 20) -> Path | None:
    """
    Pick the most reliable model CSV for angle output.
    Prefers models in _PRIMARY_PREFS order, but requires at least
    min_swing_frames of high-confidence swing-phase coverage (frame > _SEED_END).
    Falls back to any available model with the most swing coverage.
    """
    # First pass: try preferred models in order, require adequate swing coverage
    for pref in _PRIMARY_PREFS:
        for csv_path in all_seed_csvs:
            if pref not in csv_path.name:
                continue
            angles = _load_blend_angles(csv_path, leg_filter=leg)
            swing_frames = sum(1 for fi in angles if fi > _SEED_END)
            if swing_frames >= min_swing_frames:
                return csv_path

    # Second pass: any preferred model with any swing frames
    for pref in _PRIMARY_PREFS:
        for csv_path in all_seed_csvs:
            if pref in csv_path.name:
                angles = _load_blend_angles(csv_path, leg_filter=leg)
                if any(fi > _SEED_END for fi in angles):
                    return csv_path

    # Last resort: preferred model by name only (may have only hold-phase data)
    for pref in _PRIMARY_PREFS:
        for csv_path in all_seed_csvs:
            if pref in csv_path.name:
                return csv_path

    return all_seed_csvs[0] if all_seed_csvs else None


def _primary_hold_angle(csv_path: Path, leg: str) -> float | None:
    """
    Compute the median angle during the hold phase from the primary model.
    Uses top-25% most-extended frames in first 100 frames.
    """
    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return None

    df.columns = df.columns.str.strip()
    if "knee_angle_deg" not in df.columns:
        return None

    window = df.iloc[_SEED_START:_SEED_END].copy()

    if "leg" in df.columns and leg:
        window = window[window["leg"].astype(str).str.strip() == leg]

    if "knee_score" in df.columns and "ankle_score" in df.columns:
        window = window[
            (window["knee_score"]  >= _SEED_MIN_VIS) &
            (window["ankle_score"] >= _SEED_MIN_VIS)
        ]

    valid_ang = window.dropna(subset=["knee_angle_deg"])
    if len(valid_ang) >= 5:
        thresh   = valid_ang["knee_angle_deg"].quantile(0.75)
        extended = valid_ang[valid_ang["knee_angle_deg"] >= thresh]
        if len(extended) >= 3:
            valid_ang = extended

    if len(valid_ang) == 0:
        return None

    return float(valid_ang["knee_angle_deg"].median())


# ── Per-frame angle map ───────────────────────────────────────────────────────

def _load_blend_angles(csv_path: Path, leg_filter: str | None = None) -> dict[int, float]:
    """
    Load per-frame angles from one model CSV. Only includes frames where both
    knee_score and ankle_score >= 0.5 and the angle is finite.
    Optionally filters to only rows matching leg_filter.
    """
    blend = {}
    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return blend

    df.columns = df.columns.str.strip()
    if "frame" not in df.columns or "knee_angle_deg" not in df.columns:
        return blend

    ks_col = "knee_score" if "knee_score" in df.columns else None
    as_col = "ankle_score" if "ankle_score" in df.columns else None
    lg_col = "leg"         if "leg"         in df.columns else None

    for _, row in df.iterrows():
        try:
            fi  = int(row["frame"])
            ang = float(row["knee_angle_deg"])
            if not math.isfinite(ang):
                continue
            if leg_filter and lg_col:
                if str(row[lg_col]).strip() != leg_filter:
                    continue
            ks  = float(row[ks_col]) if ks_col else 1.0
            as_ = float(row[as_col]) if as_col else 1.0
            if ks >= 0.5 and as_ >= 0.5:
                blend[fi] = ang
        except (ValueError, TypeError):
            continue

    return blend


# ── Per-trial ArcTracker run ──────────────────────────────────────────────────

def run_trial(video_path: Path, seed_csv: Path,
              leg: str, hip: np.ndarray, knee: np.ndarray, ankle: np.ndarray,
              seed_angle: float, all_seed_csvs: list[Path], out_path: Path) -> bool:

    print(f"    Seed: leg={leg}  knee=({knee[0]:.0f},{knee[1]:.0f})"
          f"  radius={np.linalg.norm(ankle-knee):.0f}px  angle={seed_angle:.1f}°"
          f"  from {seed_csv.name}")
    if seed_angle < 100:
        print(f"    WARN: seed angle {seed_angle:.1f}° looks like flexion — check")

    # Primary model: most reliable with adequate swing coverage
    primary_csv = find_primary_from_csvs(all_seed_csvs, leg=leg)
    primary_angles = _load_blend_angles(primary_csv, leg_filter=leg) if primary_csv else {}

    # Stable hold-phase reference from primary model (constant during pre-release)
    # Falls back to seed_angle if primary model has no hold-phase data
    hold_ref = _primary_hold_angle(primary_csv, leg) if primary_csv else None
    if hold_ref is None:
        hold_ref = seed_angle
    primary_name = primary_csv.name if primary_csv else "none"
    print(f"    Primary: {primary_name}  hold_ref={hold_ref:.1f}°"
          f"  primary_frames={len(primary_angles)}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"    ERROR: cannot open video {video_path.name}")
        return False

    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"    Video: {fps:.1f} fps  {total} frames  ({total/fps:.1f}s)")

    tracker     = _ArcTracker()
    rows        = []
    fi          = 0
    initialized = False

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if not initialized:
            tracker.init(frame, hip, knee, ankle)
            h_px, k_px, a_px, arc_ang = tracker.step(frame)
            initialized = True
        else:
            h_px, k_px, a_px, arc_ang = tracker.step(frame)

        arc_val   = float(arc_ang)
        is_motion = getattr(tracker, '_motion_detected', False)
        primary_ang = primary_angles.get(fi)

        if primary_ang is not None:
            # Primary model has a high-confidence measurement: always use it
            final_ang = primary_ang
        elif not is_motion:
            # Pre-release hold phase, no primary coverage: use stable hold reference
            # (constant → zero noise → prevents false N counts)
            final_ang = hold_ref
        else:
            # Swing phase, no primary coverage: fall back to ArcTracker
            final_ang = arc_val

        ang_out = round(final_ang, 4) if math.isfinite(final_ang) else ""
        rows.append((
            fi,
            round(fi / fps, 6),
            leg,
            round(float(h_px[0]), 2) if h_px is not None else "",
            round(float(h_px[1]), 2) if h_px is not None else "",
            1.0,
            round(float(k_px[0]), 2),
            round(float(k_px[1]), 2),
            1.0,
            round(float(a_px[0]), 2),
            round(float(a_px[1]), 2),
            1.0,
            ang_out,
        ))
        fi += 1

    cap.release()

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["frame", "time_sec", "leg",
                    "hip_x", "hip_y", "hip_score",
                    "knee_x", "knee_y", "knee_score",
                    "ankle_x", "ankle_y", "ankle_score",
                    "knee_angle_deg"])
        w.writerows(rows)

    n_ang      = sum(1 for r in rows if r[-1] != "")
    n_primary  = sum(1 for i in range(len(rows)) if i in primary_angles)
    n_arcfill  = sum(1 for i, r in enumerate(rows)
                     if r[-1] != "" and i not in primary_angles)
    print(f"    -> {len(rows)} frames  ({n_ang} with angle,"
          f" {n_primary} primary, {n_arcfill} arc-fill)  -> {out_path.name}")
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Batch Pendulastic tracker (v2.0)")
    ap.add_argument("--overwrite",   action="store_true",
                    help="Re-run trials that already have a pendulastic CSV")
    ap.add_argument("--participant", default=None,
                    help="Substring filter on participant folder name")
    ap.add_argument("--clean-old",   action="store_true",
                    help="Delete pendulastic CSVs from older versions before running")
    args = ap.parse_args()

    if args.clean_old:
        print("Cleaning old pendulastic CSVs...")
        for old_csv in REC_ROOT.rglob("*_pendulastic_*.csv"):
            if MODEL_TAG not in old_csv.name:
                print(f"  Removing {old_csv.relative_to(REC_ROOT)}")
                old_csv.unlink()
        print()

    trials = discover_trials()
    print(f"Discovered {len(trials)} trials\n")

    n_ok = n_skip = n_fail = 0

    for folder, trial_n in trials:
        participant = folder.parent.parent.name
        if args.participant and args.participant.lower() not in participant.lower():
            continue

        label    = f"{participant} / trial {trial_n}"
        out_path = out_csv_path(folder, trial_n)

        if out_path.exists() and not args.overwrite:
            print(f"[DONE] {label} — already exists, skipping")
            n_skip += 1
            continue

        video = find_video(folder, trial_n)
        if video is None:
            print(f"[SKIP] {label} — no video")
            n_skip += 1
            continue

        all_csvs = find_all_seed_csvs(folder, trial_n)
        best = find_best_seed(folder, trial_n)
        if best is None:
            print(f"[SKIP] {label} — no usable seed CSV")
            n_skip += 1
            continue

        seed_csv, leg, hip, knee, ankle, seed_ang = best
        print(f"\n[RUN ] {label}")
        print(f"    Video : {video.name}")

        try:
            ok = run_trial(video, seed_csv, leg, hip, knee, ankle, seed_ang,
                           all_csvs, out_path)
        except Exception as exc:
            import traceback
            print(f"    ERROR: {exc}")
            traceback.print_exc()
            ok = False

        if ok:
            n_ok += 1
        else:
            n_fail += 1

    print(f"\n{'='*60}")
    print(f"Complete — processed={n_ok}  already_done={n_skip}  failed={n_fail}")


if __name__ == "__main__":
    main()
