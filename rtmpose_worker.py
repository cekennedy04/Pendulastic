"""
rtmpose_worker.py
=================
General rtmlib inference worker — handles RTMPose, ViTPose, and RTMO.
Runs in .venv (needs rtmlib + onnxruntime).  Called as subprocess from
pendulastic_pipeline.py.

Usage:
    # RTMPose (local ONNX):
    python -u rtmpose_worker.py --video V --csv C --pose-class RTMPose --pose-onnx end2end.onnx

    # ViTPose (downloads once):
    python -u rtmpose_worker.py --video V --csv C --pose-class ViTPose --pose-url https://...

    # RTMO (downloads once, no detector):
    python -u rtmpose_worker.py --video V --csv C --pose-class RTMO --pose-url https://...
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


# ---------------------------------------------------------------------------
# Tracking selection helpers
# ---------------------------------------------------------------------------

def _load_sel(video_path: str) -> dict:
    """Return the tracking_selections.json entry for this video, or {}."""
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
    """Pixel position of user-marked hip (for tracker seeding)."""
    xn = sel.get("hip_x_norm") or sel.get("click_x_norm")
    yn = sel.get("hip_y_norm") or sel.get("click_y_norm")
    if xn is None:
        return None
    return np.array([xn * w, yn * h])


def _sel_leg_side(sel: dict, kp17: np.ndarray, w: int, h: int) -> Optional[str]:
    """
    Determine left/right from the knee click position.
    Compares kp17[L_KNEE] and kp17[R_KNEE] distances to the marked knee point.
    Returns 'left', 'right', or None if no knee click is stored.
    """
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

_YOLOX_URL = ("https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/"
              "onnx_sdk/yolox_m_8xb8-300e_humanart-c2c7a14a.zip")


# ---------------------------------------------------------------------------
# Helpers (duplicated from pipeline for subprocess isolation)
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
        self._last      = None
        self._kidx       = L_KNEE if leg_side != "right" else R_KNEE

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

def run(video_path: str, csv_path: str,
        pose_class: str, pose_onnx: Optional[str], pose_url: Optional[str],
        det_url: Optional[str], leg_side: Optional[str],
        score_thresh: float, is_duo: bool) -> None:

    from rtmlib import Custom

    device = "cpu"
    try:
        import onnxruntime as ort
        if "CUDAExecutionProvider" in ort.get_available_providers():
            device = "cuda"
    except ImportError:
        pass

    print(f"[rtmlib_worker] pose_class={pose_class}  device={device}"
          f"  leg={leg_side or 'auto'}  duo={is_duo}", flush=True)

    if pose_class == "RTMO":
        model = Custom(
            pose_class="RTMO",
            pose=pose_url,
            pose_input_size=(640, 640),
            backend="onnxruntime", device=device,
        )
    elif pose_class == "ViTPose":
        model = Custom(
            det_class="YOLOX",   det=det_url or _YOLOX_URL,  det_input_size=(640, 640),
            pose_class="ViTPose", pose=pose_url,              pose_input_size=(192, 256),
            backend="onnxruntime", device=device,
        )
    else:  # RTMPose — local ONNX file
        model = Custom(
            det_class="YOLOX",    det=det_url or _YOLOX_URL, det_input_size=(640, 640),
            pose_class="RTMPose", pose=pose_onnx,            pose_input_size=(192, 256),
            backend="onnxruntime", device=device,
        )

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"[rtmlib_worker] {total} frames @ {fps:.0f}fps", flush=True)

    # Load per-video tracking selection
    sel         = _load_sel(video_path)
    start_frame = int(sel.get("start_frame", 0))
    w_vid = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h_vid = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    hip_pt = _sel_hip_pt(sel, w_vid, h_vid)
    if sel:
        has_joints = sel.get("knee_x_norm") is not None
        print(f"[rtmlib_worker] selection: start_frame={start_frame}  "
              f"hip={'set' if hip_pt is not None else 'no'}  "
              f"joints={'set' if has_joints else 'no'}", flush=True)

    tracker = _KneeTracker(leg_side)
    if hip_pt is not None:
        tracker._last = hip_pt

    # leg_side will be locked from the knee click on the first detected frame
    leg_side_locked  = leg_side
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

        kps, scs = model(frame)  # kps (N,K,2)  scs (N,K)

        if kps is None or len(kps) == 0:
            rows.append(_nan_row(fi, t))
        else:
            kp_list = [kps[i] for i in range(len(kps))]
            sc_list = [scs[i] for i in range(len(scs))]
            best    = tracker.pick(kp_list, sc_list) if (is_duo or len(kp_list) > 1) else 0

            kp17 = kps[best]; sc17 = scs[best]
            # Lock leg_side from knee click on first valid detection
            if not leg_side_from_sel:
                override = _sel_leg_side(sel, kp17, w_vid, h_vid)
                if override:
                    leg_side_locked = override
                    leg_side_from_sel = True
                    print(f"[rtmlib_worker] leg_side locked to '{override}' from knee click", flush=True)
            leg, hip, hs, kne, ks, ank, as_ = _pick_leg(kp17, sc17, leg_side_locked)
            ok3  = float(hs) >= score_thresh and float(ks) >= score_thresh and float(as_) >= score_thresh
            ang  = _angle_deg(hip, kne, ank) if ok3 else float("nan")
            rows.append({"frame": fi, "time_sec": round(t, 6), "leg": leg,
                         "hip_x": round(float(hip[0]),2), "hip_y": round(float(hip[1]),2), "hip_score": round(float(hs),4),
                         "knee_x": round(float(kne[0]),2), "knee_y": round(float(kne[1]),2), "knee_score": round(float(ks),4),
                         "ankle_x": round(float(ank[0]),2), "ankle_y": round(float(ank[1]),2), "ankle_score": round(float(as_),4),
                         "knee_angle_deg": round(ang, 4) if math.isfinite(ang) else float("nan")})

        if fi % 300 == 0:
            print(f"[rtmlib_worker] frame {fi}/{total}", flush=True)
        fi += 1

    cap.release()

    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)

    valid = sum(1 for r in rows if math.isfinite(r["knee_angle_deg"]))
    print(f"[rtmlib_worker] Done: {valid}/{len(rows)} valid -> {csv_path}", flush=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video",        required=True)
    p.add_argument("--csv",          required=True)
    p.add_argument("--pose-class",   default="RTMPose", choices=["RTMPose", "ViTPose", "RTMO"])
    p.add_argument("--pose-onnx",    default=None, help="Local ONNX path (RTMPose)")
    p.add_argument("--pose-url",     default=None, help="Download URL (ViTPose/RTMO)")
    p.add_argument("--det-url",      default=None, help="Detector download URL (optional override)")
    p.add_argument("--leg-side",     default=None, choices=["left", "right"])
    p.add_argument("--score-thresh", type=float, default=0.3)
    p.add_argument("--is-duo",       action="store_true")
    args = p.parse_args()

    run(args.video, args.csv,
        args.pose_class, args.pose_onnx, args.pose_url, args.det_url,
        args.leg_side, args.score_thresh, args.is_duo)


if __name__ == "__main__":
    main()
