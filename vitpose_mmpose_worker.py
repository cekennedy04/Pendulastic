"""
vitpose_mmpose_worker.py
========================
ViTPose-Base inference via the mmpose PyTorch API (mmpose 1.x).
Runs in the openmmlab conda environment.

Usage:
    C:\\Users\\cladi\\miniconda3\\envs\\openmmlab\\python.exe -u vitpose_mmpose_worker.py
        --video  <path.avi>
        --csv    <out.csv>
        --config <td-hm_ViTPose-base_8xb64-210e_coco-256x192.py>
        --ckpt   <td-hm_ViTPose-base_..._20230314.pth>
        [--leg-side left|right]
        [--score-thresh 0.3]
        [--is-duo]

Person selection for duo videos
---------------------------------
ViTPose is a top-down model — it needs a bounding box per person.
Strategy:
  * If mmdet is available: run a person detector, get per-person bboxes,
    run ViTPose on each, then pick with _KneeTracker.
  * Otherwise (solo or no mmdet): pass the full-frame bbox; ViTPose's
    attention mechanism focuses on the dominant subject.
_KneeTracker is always applied so identity is locked across frames once
the first person is chosen.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="mmcv")
warnings.filterwarnings("ignore", category=UserWarning, module="mmpose")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import cv2
import numpy as np
from typing import Optional


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


# ---------------------------------------------------------------------------
# Position-continuity tracker
# ---------------------------------------------------------------------------

class _KneeTracker:
    """Locks target identity across frames by tracking knee pixel position."""

    def __init__(self, leg_side: Optional[str], max_jump_px: float = 150.0):
        self.leg_side    = leg_side
        self.max_jump_px = max_jump_px
        self._last: Optional[np.ndarray] = None
        self._kidx = L_KNEE if leg_side != "right" else R_KNEE

    def reset(self):
        self._last = None

    def pick(self, kp_list, sc_list) -> int:
        """
        kp_list : list of (17, 2) arrays
        sc_list : list of (17,) arrays
        Returns index of chosen person.
        """
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
# Optional mmdet-based person detector
# ---------------------------------------------------------------------------

def _try_load_detector():
    """Returns a (det_model, det_cfg) tuple or None if mmdet is not installed."""
    try:
        from mmdet.apis import init_detector, inference_detector
        # RTMDet-tiny — lightweight, fast on CPU
        DET_CFG  = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "models", "vitpose", "rtmdet_tiny_8xb32-300e_coco.py"
        )
        DET_CKPT = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "models", "vitpose", "rtmdet_tiny_8xb32-300e_coco_20220902_112414-78e30dcc.pth"
        )
        if os.path.isfile(DET_CFG) and os.path.isfile(DET_CKPT):
            print("[vitpose_worker] mmdet found — using RTMDet person detector", flush=True)
            return init_detector(DET_CFG, DET_CKPT, device="cpu"), inference_detector
        else:
            print("[vitpose_worker] mmdet installed but RTMDet weights absent — using full-frame mode", flush=True)
            return None
    except ImportError:
        print("[vitpose_worker] mmdet not installed — using full-frame bbox mode", flush=True)
        return None


def _detect_persons_mmdet(frame_rgb, det_tuple, det_thresh=0.4):
    """Run mmdet person detector, return list of [x1,y1,x2,y2] boxes."""
    det_model, inference_fn = det_tuple
    result = inference_fn(det_model, frame_rgb)
    bboxes = []
    try:
        # mmdet 3.x result format
        pred = result.pred_instances
        for i in range(len(pred.labels)):
            if int(pred.labels[i]) == 0 and float(pred.scores[i]) >= det_thresh:
                b = pred.bboxes[i].cpu().numpy()
                bboxes.append(b[:4].tolist())
    except AttributeError:
        pass
    return bboxes


# ---------------------------------------------------------------------------
# Main inference loop
# ---------------------------------------------------------------------------

def run(video_path: str, csv_path: str, config_path: str, ckpt_path: str,
        leg_side: Optional[str], score_thresh: float, is_duo: bool) -> None:

    from mmpose.apis import init_model, inference_topdown

    print(f"[vitpose_worker] Loading ViTPose-Base...", flush=True)
    print(f"  config : {config_path}", flush=True)
    print(f"  ckpt   : {ckpt_path}", flush=True)

    pose_model = init_model(config_path, ckpt_path, device="cpu")
    pose_model.eval()

    # Try to load person detector for duo videos
    det_tuple = _try_load_detector() if is_duo else None

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    h_vid = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    w_vid = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    full_bbox = np.array([[0, 0, w_vid, h_vid]], dtype=np.float32)

    print(f"[vitpose_worker] {total} frames @ {fps:.0f}fps  ({w_vid}x{h_vid})"
          f"  leg={leg_side or 'auto'}  duo={is_duo}", flush=True)

    sel         = _load_sel(video_path)
    start_frame = int(sel.get("start_frame", 0))
    hip_pt      = _sel_hip_pt(sel, w_vid, h_vid)
    if sel:
        print(f"[vitpose_worker] selection: start_frame={start_frame}  "
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
        if not ok:
            break
        t         = fi / fps

        if fi < start_frame:
            rows.append(_nan_row(fi, t))
            fi += 1
            continue

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # ── Determine bounding boxes ─────────────────────────────────────
        if is_duo and det_tuple is not None:
            person_bboxes = _detect_persons_mmdet(frame_rgb, det_tuple)
            if not person_bboxes:
                person_bboxes = full_bbox.tolist()
        else:
            # Solo or no detector: use full frame
            person_bboxes = full_bbox.tolist()

        bboxes_np = np.array(person_bboxes, dtype=np.float32)

        # ── Run ViTPose on each bounding box ─────────────────────────────
        try:
            data_samples = inference_topdown(
                pose_model, frame_rgb,
                bboxes=bboxes_np, bbox_format="xyxy"
            )
        except Exception as exc:
            print(f"[vitpose_worker] frame {fi}: {exc}", flush=True)
            rows.append(_nan_row(fi, t))
            fi += 1
            continue

        if not data_samples:
            rows.append(_nan_row(fi, t))
            fi += 1
            continue

        # ── Extract keypoints per detected person ─────────────────────────
        kp_list: list = []
        sc_list: list = []
        for ds in data_samples:
            if not hasattr(ds, "pred_instances"):
                continue
            kps = ds.pred_instances.keypoints        # (1, 17, 2)
            scs = ds.pred_instances.keypoint_scores  # (1, 17)
            if kps is None or len(kps) == 0:
                continue
            kp_list.append(np.asarray(kps[0], float))   # (17, 2)
            sc_list.append(np.asarray(scs[0], float))   # (17,)

        if not kp_list:
            rows.append(_nan_row(fi, t))
            fi += 1
            continue

        # ── Pick the target person ────────────────────────────────────────
        best = tracker.pick(kp_list, sc_list)
        kp17 = kp_list[best]
        sc17 = sc_list[best]

        if not leg_side_from_sel:
            override = _sel_leg_side(sel, kp17, w_vid, h_vid)
            if override:
                leg_side_locked = override; leg_side_from_sel = True
                print(f"[vitpose_worker] leg_side locked to '{override}' from knee click", flush=True)
        leg, hip, hs, kne, ks, ank, as_ = _pick_leg(kp17, sc17, leg_side_locked)
        ok3  = float(hs) >= score_thresh and float(ks) >= score_thresh and float(as_) >= score_thresh
        ang  = _angle_deg(hip, kne, ank) if ok3 else float("nan")

        rows.append({"frame": fi, "time_sec": round(t, 6), "leg": leg,
                     "hip_x":   round(float(hip[0]), 2), "hip_y":   round(float(hip[1]), 2), "hip_score":   round(float(hs), 4),
                     "knee_x":  round(float(kne[0]), 2), "knee_y":  round(float(kne[1]), 2), "knee_score":  round(float(ks), 4),
                     "ankle_x": round(float(ank[0]), 2), "ankle_y": round(float(ank[1]), 2), "ankle_score": round(float(as_), 4),
                     "knee_angle_deg": round(ang, 4) if math.isfinite(ang) else float("nan")})

        if fi % 300 == 0:
            print(f"[vitpose_worker] frame {fi}/{total}", flush=True)
        fi += 1

    cap.release()

    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)

    valid = sum(1 for r in rows if math.isfinite(r["knee_angle_deg"]))
    print(f"[vitpose_worker] Done: {valid}/{len(rows)} valid -> {csv_path}", flush=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video",        required=True)
    p.add_argument("--csv",          required=True)
    p.add_argument("--config",       required=True)
    p.add_argument("--ckpt",         required=True)
    p.add_argument("--leg-side",     default=None, choices=["left", "right"])
    p.add_argument("--score-thresh", type=float, default=0.3)
    p.add_argument("--is-duo",       action="store_true")
    args = p.parse_args()

    run(args.video, args.csv, args.config, args.ckpt,
        args.leg_side, args.score_thresh, args.is_duo)


if __name__ == "__main__":
    main()
