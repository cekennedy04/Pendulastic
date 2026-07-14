"""
hrnet_worker.py
===============
Standalone HRNet-W48-COCO inference script.
Called via subprocess by run_new_models_evaluate.py using the openmmlab conda env.

Usage (do not invoke directly — use run_new_models_evaluate.py):
    C:\\Users\\cladi\\miniconda3\\envs\\openmmlab\\python.exe hrnet_worker.py
        --video  <path>
        --csv    <path>
        --config <mmpose config .py path>
        --ckpt   <checkpoint .pth path>

Writes CSV with columns:
    frame, time_sec, leg, hip_x, hip_y, hip_score,
    knee_x, knee_y, knee_score, ankle_x, ankle_y, ankle_score, knee_angle_deg
"""

import argparse
import csv
import json
import math
import os
import sys
import warnings

# Suppress the noisy mmcv DLL stub warnings on import
warnings.filterwarnings("ignore", category=UserWarning, module="mmcv")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import cv2
import numpy as np

# COCO-17 keypoint indices
L_HIP, L_KNEE, L_ANKLE = 11, 13, 15
R_HIP, R_KNEE, R_ANKLE = 12, 14, 16

CSV_FIELDS = [
    "frame", "time_sec", "leg",
    "hip_x", "hip_y", "hip_score",
    "knee_x", "knee_y", "knee_score",
    "ankle_x", "ankle_y", "ankle_score",
    "knee_angle_deg",
]


def _angle_deg(a, b, c):
    ba = np.asarray(a, float) - np.asarray(b, float)
    bc = np.asarray(c, float) - np.asarray(b, float)
    n1, n2 = np.linalg.norm(ba), np.linalg.norm(bc)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    return math.degrees(math.acos(np.clip(np.dot(ba, bc) / (n1 * n2), -1, 1)))


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


def _nan_row(fi, t):
    return {
        "frame": fi, "time_sec": round(t, 6), "leg": "none",
        "hip_x": float("nan"),   "hip_y": float("nan"),   "hip_score": 0.0,
        "knee_x": float("nan"),  "knee_y": float("nan"),  "knee_score": 0.0,
        "ankle_x": float("nan"), "ankle_y": float("nan"), "ankle_score": 0.0,
        "knee_angle_deg": float("nan"),
    }


def run(video_path, csv_path, config_path, ckpt_path, score_thresh=0.3, leg_side=None):
    # ── Load model ──────────────────────────────────────────────────────────
    from mmpose.apis import init_model, inference_topdown

    print(f"[hrnet_worker] Loading HRNet-W48 from checkpoint...", flush=True)
    print(f"  config : {config_path}", flush=True)
    print(f"  ckpt   : {ckpt_path}", flush=True)

    model = init_model(config_path, ckpt_path, device="cpu")
    model.eval()

    # ── Open video ──────────────────────────────────────────────────────────
    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    h_vid = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    w_vid = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))

    if h_vid == 0 or w_vid == 0:
        cap.release()
        print("[hrnet_worker] SKIP: could not read video dimensions.", flush=True)
        return

    print(f"[hrnet_worker] {total} frames @ {fps:.1f} fps  ({w_vid}x{h_vid})", flush=True)

    sel         = _load_sel(video_path)
    start_frame = int(sel.get("start_frame", 0))
    if sel:
        print(f"[hrnet_worker] selection: start_frame={start_frame}", flush=True)

    # Full-frame bounding box for single-person inference
    full_bbox = np.array([[0, 0, w_vid, h_vid]], dtype=np.float32)

    rows = []
    fi   = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = fi / fps

        if fi < start_frame:
            rows.append(_nan_row(fi, t))
            fi += 1
            continue

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        try:
            data_samples = inference_topdown(model, frame_rgb, bboxes=full_bbox,
                                             bbox_format="xyxy")
        except Exception as exc:
            print(f"[hrnet_worker] frame {fi}: inference error: {exc}", flush=True)
            rows.append(_nan_row(fi, t))
            fi += 1
            continue

        if not data_samples or not hasattr(data_samples[0], "pred_instances"):
            rows.append(_nan_row(fi, t))
            fi += 1
            continue

        kps = data_samples[0].pred_instances.keypoints   # (1, 17, 2) x,y
        scs = data_samples[0].pred_instances.keypoint_scores  # (1, 17)

        if kps is None or len(kps) == 0:
            rows.append(_nan_row(fi, t))
            fi += 1
            continue

        kp  = kps[0]   # (17, 2)
        sc  = scs[0]   # (17,)

        # Pick leg: honor leg_side if set, else use confidence heuristic
        if leg_side == "left":
            use_left = True
        elif leg_side == "right":
            use_left = False
        else:
            l_conf = float(sc[L_HIP] + sc[L_KNEE] + sc[L_ANKLE])
            r_conf = float(sc[R_HIP] + sc[R_KNEE] + sc[R_ANKLE])
            use_left = l_conf >= r_conf
        if use_left:
            leg = "left"
            hip, h_s = kp[L_HIP], float(sc[L_HIP])
            kne, k_s = kp[L_KNEE], float(sc[L_KNEE])
            ank, a_s = kp[L_ANKLE], float(sc[L_ANKLE])
        else:
            leg = "right"
            hip, h_s = kp[R_HIP], float(sc[R_HIP])
            kne, k_s = kp[R_KNEE], float(sc[R_KNEE])
            ank, a_s = kp[R_ANKLE], float(sc[R_ANKLE])

        ok3 = h_s >= score_thresh and k_s >= score_thresh and a_s >= score_thresh
        ang = _angle_deg(hip, kne, ank) if ok3 else float("nan")

        rows.append({
            "frame": fi, "time_sec": round(t, 6), "leg": leg,
            "hip_x":   round(float(hip[0]), 2), "hip_y":   round(float(hip[1]), 2), "hip_score":   round(h_s, 4),
            "knee_x":  round(float(kne[0]), 2), "knee_y":  round(float(kne[1]), 2), "knee_score":  round(k_s, 4),
            "ankle_x": round(float(ank[0]), 2), "ankle_y": round(float(ank[1]), 2), "ankle_score": round(a_s, 4),
            "knee_angle_deg": round(ang, 4) if not math.isnan(ang) else float("nan"),
        })

        fi += 1
        if fi % 60 == 0:
            print(f"[hrnet_worker]   frame {fi}/{total}", flush=True)

    cap.release()

    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)

    valid = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"[hrnet_worker] Done: {valid}/{len(rows)} valid frames → {csv_path}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video",  required=True)
    ap.add_argument("--csv",    required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--ckpt",   required=True)
    ap.add_argument("--score-thresh", type=float, default=0.3)
    ap.add_argument("--leg-side", choices=["left", "right"], default=None)
    args = ap.parse_args()
    run(args.video, args.csv, args.config, args.ckpt, args.score_thresh, args.leg_side)


if __name__ == "__main__":
    main()
