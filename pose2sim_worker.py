"""
pose2sim_worker.py
==================
Pose2Sim 2D pose estimation worker using rtmlib.Body (COCO_17, balanced RTMPose
with person tracking) — the same underlying model that Pose2Sim's poseEstimation
step uses internally.  Runs in .venv.

Usage:
    .venv\\Scripts\\python.exe -u pose2sim_worker.py \\
        --video V.avi  --csv out.csv  --mode balanced \\
        [--leg-side left|right]  [--score-thresh 0.3]  [--is-duo]

Modes (rtmlib.Body):
    lightweight  — fastest, least accurate
    balanced     — default (same as Pose2Sim default)
    performance  — slowest, most accurate
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from typing import Optional

import cv2
import numpy as np


def _load_sel(video_path: str) -> dict:
    base = os.path.dirname(os.path.abspath(__file__))
    sel_file = os.path.join(base, "tracking_selections.json")
    if not os.path.exists(sel_file):
        return {}
    try:
        rel = os.path.relpath(os.path.abspath(video_path), base).replace("\\", "/")
        with open(sel_file, encoding="utf-8") as f:
            return json.load(f).get(rel, {})
    except Exception:
        return {}


def _sel_hip_pt(sel: dict, w: int, h: int) -> Optional[np.ndarray]:
    xn = sel.get("hip_x_norm") or sel.get("click_x_norm")
    yn = sel.get("hip_y_norm") or sel.get("click_y_norm")
    if xn is None:
        return None
    return np.array([xn * w, yn * h])


def _sel_leg_side(sel: dict, kp17: np.ndarray, w: int, h: int) -> Optional[str]:
    xn = sel.get("knee_x_norm")
    yn = sel.get("knee_y_norm")
    if xn is None:
        return None
    kpt = np.array([xn * w, yn * h])
    dL  = float(np.linalg.norm(np.asarray(kp17[L_KNEE], float) - kpt))
    dR  = float(np.linalg.norm(np.asarray(kp17[R_KNEE], float) - kpt))
    return "left" if dL < dR else "right"

# ---------------------------------------------------------------------------
# COCO-17 indices
# ---------------------------------------------------------------------------
L_HIP, L_KNEE, L_ANKLE = 11, 13, 15
R_HIP, R_KNEE, R_ANKLE = 12, 14, 16

CSV_FIELDS = [
    "frame", "time_sec", "leg",
    "hip_x",   "hip_y",   "hip_score",
    "knee_x",  "knee_y",  "knee_score",
    "ankle_x", "ankle_y", "ankle_score",
    "knee_angle_deg",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _angle_deg(a, b, c) -> float:
    ba = np.asarray(a, float) - np.asarray(b, float)
    bc = np.asarray(c, float) - np.asarray(b, float)
    n1, n2 = np.linalg.norm(ba), np.linalg.norm(bc)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    return math.degrees(math.acos(np.clip(np.dot(ba, bc) / (n1 * n2), -1, 1)))


def _nan_row(fi: int, t: float) -> dict:
    return {"frame": fi, "time_sec": round(t, 6), "leg": "none",
            "hip_x": float("nan"),   "hip_y": float("nan"),   "hip_score": 0.0,
            "knee_x": float("nan"),  "knee_y": float("nan"),  "knee_score": 0.0,
            "ankle_x": float("nan"), "ankle_y": float("nan"), "ankle_score": 0.0,
            "knee_angle_deg": float("nan")}


def _pick_leg(kp, sc, leg_side: Optional[str]):
    if leg_side == "left":
        return "left",  kp[L_HIP], sc[L_HIP], kp[L_KNEE], sc[L_KNEE], kp[L_ANKLE], sc[L_ANKLE]
    if leg_side == "right":
        return "right", kp[R_HIP], sc[R_HIP], kp[R_KNEE], sc[R_KNEE], kp[R_ANKLE], sc[R_ANKLE]
    lc = float(sc[L_HIP]) + float(sc[L_KNEE]) + float(sc[L_ANKLE])
    rc = float(sc[R_HIP]) + float(sc[R_KNEE]) + float(sc[R_ANKLE])
    if lc >= rc:
        return "left",  kp[L_HIP], sc[L_HIP], kp[L_KNEE], sc[L_KNEE], kp[L_ANKLE], sc[L_ANKLE]
    return "right", kp[R_HIP], sc[R_HIP], kp[R_KNEE], sc[R_KNEE], kp[R_ANKLE], sc[R_ANKLE]


class _KneeTracker:
    def __init__(self, leg_side: Optional[str], max_jump_px: float = 150.0):
        self.leg_side    = leg_side
        self.max_jump_px = max_jump_px
        self._last: Optional[np.ndarray] = None
        self._kidx = L_KNEE if leg_side != "right" else R_KNEE

    def pick(self, kp_list, sc_list) -> int:
        n = len(kp_list)
        if n == 0: return 0
        if n == 1:
            self._last = np.asarray(kp_list[0][self._kidx], float).copy()
            return 0

        def _conf(i):
            sc = sc_list[i]
            if self.leg_side == "left":  return float(sc[L_HIP]+sc[L_KNEE]+sc[L_ANKLE])
            if self.leg_side == "right": return float(sc[R_HIP]+sc[R_KNEE]+sc[R_ANKLE])
            return float(np.mean(sc))

        if self._last is None:
            best = max(range(n), key=_conf)
        else:
            dists = [np.linalg.norm(kp_list[i][self._kidx] - self._last) for i in range(n)]
            best  = int(np.argmin(dists))
            if dists[best] > self.max_jump_px:
                best = max(range(n), key=_conf)

        self._last = np.asarray(kp_list[best][self._kidx], float).copy()
        return best


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def run(video_path: str, csv_path: str, mode: str,
        leg_side: Optional[str], score_thresh: float, is_duo: bool) -> None:

    from rtmlib import Body

    device = "cpu"
    try:
        import onnxruntime as ort
        if "CUDAExecutionProvider" in ort.get_available_providers():
            device = "cuda"
    except ImportError:
        pass

    print(f"[pose2sim_worker] mode={mode}  device={device}"
          f"  leg={leg_side or 'auto'}  duo={is_duo}", flush=True)

    # rtmlib.Body wraps RTMPose COCO_17 — same underlying model Pose2Sim uses
    model = Body(
        backend="onnxruntime",
        device=device,
        to_openpose=False,   # return numpy arrays, not OpenPose JSON format
        mode=mode,
    )

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"[pose2sim_worker] {total} frames @ {fps:.0f}fps", flush=True)

    sel         = _load_sel(video_path)
    start_frame = int(sel.get("start_frame", 0))
    _w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    _h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    hip_pt = _sel_hip_pt(sel, _w, _h)
    if sel:
        print(f"[pose2sim_worker] selection: start_frame={start_frame}  "
              f"hip={'set' if hip_pt is not None else 'no'}  "
              f"joints={'set' if sel.get('knee_x_norm') else 'no'}", flush=True)

    tracker = _KneeTracker(leg_side)
    if hip_pt is not None:
        tracker._last = hip_pt

    leg_side_locked   = leg_side
    leg_side_from_sel = False

    rows: list = []
    fi = 0

    while True:
        ok, frame = cap.read()
        if not ok: break
        t = fi / fps

        if fi < start_frame:
            rows.append(_nan_row(fi, t))
            fi += 1
            continue

        kps, scs = model(frame)  # (N,17,2) and (N,17) or None

        if kps is None or len(kps) == 0:
            rows.append(_nan_row(fi, t))
        else:
            kp_list = [kps[i] for i in range(len(kps))]
            sc_list = [scs[i] for i in range(len(scs))]
            best    = tracker.pick(kp_list, sc_list) if (is_duo or len(kp_list) > 1) else 0

            kp17 = kps[best]; sc17 = scs[best]
            if not leg_side_from_sel:
                override = _sel_leg_side(sel, kp17, _w, _h)
                if override:
                    leg_side_locked = override; leg_side_from_sel = True
                    print(f"[pose2sim_worker] leg_side locked to '{override}' from knee click", flush=True)
            leg, hip, hs, kne, ks, ank, as_ = _pick_leg(kp17, sc17, leg_side_locked)
            ok3  = float(hs) >= score_thresh and float(ks) >= score_thresh and float(as_) >= score_thresh
            ang  = _angle_deg(hip, kne, ank) if ok3 else float("nan")
            rows.append({"frame": fi, "time_sec": round(t, 6), "leg": leg,
                         "hip_x": round(float(hip[0]),2), "hip_y": round(float(hip[1]),2), "hip_score": round(float(hs),4),
                         "knee_x": round(float(kne[0]),2), "knee_y": round(float(kne[1]),2), "knee_score": round(float(ks),4),
                         "ankle_x": round(float(ank[0]),2), "ankle_y": round(float(ank[1]),2), "ankle_score": round(float(as_),4),
                         "knee_angle_deg": round(ang, 4) if math.isfinite(ang) else float("nan")})

        if fi % 300 == 0:
            print(f"[pose2sim_worker] frame {fi}/{total}", flush=True)
        fi += 1

    cap.release()

    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)

    valid = sum(1 for r in rows if math.isfinite(r["knee_angle_deg"]))
    print(f"[pose2sim_worker] Done: {valid}/{len(rows)} valid -> {csv_path}", flush=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video",        required=True)
    p.add_argument("--csv",          required=True)
    p.add_argument("--mode",         default="balanced",
                   choices=["lightweight", "balanced", "performance"])
    p.add_argument("--leg-side",     default=None, choices=["left", "right"])
    p.add_argument("--score-thresh", type=float, default=0.3)
    p.add_argument("--is-duo",       action="store_true")
    args = p.parse_args()

    run(args.video, args.csv, args.mode, args.leg_side, args.score_thresh, args.is_duo)


if __name__ == "__main__":
    main()
