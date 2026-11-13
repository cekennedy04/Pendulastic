"""
run_detrpose_participant_0.py
==============================
Runs DETRPose-S on Participant 0 trial videos (both positions) and writes
knee-angle CSVs into the OptiTrack_Recordings tree.

Videos: trial_1.mp4 / trial_2.mp4  (lowercase, .mp4)
Output: P_0_Pos_{pos}_H_Joint-Level_T_{trial}_detrpose_s_0.5.csv

Run from the Pendulastic directory:
    .venv\\Scripts\\python.exe run_detrpose_participant_0.py
"""

import os, sys, math, csv
import cv2
import numpy as np
import torch
import torch.nn as nn
import torchvision.transforms as T
from PIL import Image

BASE_DIR    = r"C:\Users\cladi\Pendulastic"
DETR_DIR    = os.path.join(BASE_DIR, "models", "DETRPose-main")
CONFIG_PATH = os.path.join(DETR_DIR, "configs", "detrpose", "detrpose_hgnetv2_s.py")
WEIGHT_PATH = os.path.join(DETR_DIR, "weights", "detrpose_hgnetv2_s.pth")

SCORE_THRESH = 0.5
TRIALS       = [1, 2]
POSITIONS    = [1, 2]

L_HIP, L_KNEE, L_ANKLE = 11, 13, 15
R_HIP, R_KNEE, R_ANKLE = 12, 14, 16

sys.path.insert(0, DETR_DIR)


class DeployModel(nn.Module):
    def __init__(self, model, postprocessor):
        super().__init__()
        self.model         = model.deploy()
        self.postprocessor = postprocessor.deploy()

    def forward(self, images, orig_target_sizes):
        out = self.model(images)
        return self.postprocessor(out, orig_target_sizes)


def load_detrpose(config_path, weight_path, device="cpu"):
    from src.core import LazyConfig, instantiate
    cfg = LazyConfig.load(config_path)
    if hasattr(cfg.model.backbone, "pretrained"):
        cfg.model.backbone.pretrained = False
    model         = instantiate(cfg.model)
    postprocessor = instantiate(cfg.postprocessor)
    ckpt  = torch.load(weight_path, map_location="cpu", weights_only=False)
    state = ckpt.get("ema", {}).get("module") or ckpt.get("model") or ckpt
    model.load_state_dict(state, strict=True)
    deploy = DeployModel(model, postprocessor).to(device)
    deploy.eval()
    return deploy


def _angle_deg(a, b, c):
    ba = np.asarray(a, float) - np.asarray(b, float)
    bc = np.asarray(c, float) - np.asarray(b, float)
    n1, n2 = np.linalg.norm(ba), np.linalg.norm(bc)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    return math.degrees(math.acos(np.clip(np.dot(ba, bc) / (n1 * n2), -1.0, 1.0)))


def _pick_leg_detr(kp17, person_score):
    def span(h, k, a):
        ys = [h[1], k[1], a[1]]
        return max(ys) - min(ys)
    l_span = span(kp17[L_HIP], kp17[L_KNEE], kp17[L_ANKLE])
    r_span = span(kp17[R_HIP], kp17[R_KNEE], kp17[R_ANKLE])
    if l_span >= r_span:
        return "left",  kp17[L_HIP], kp17[L_KNEE], kp17[L_ANKLE], float(person_score)
    else:
        return "right", kp17[R_HIP], kp17[R_KNEE], kp17[R_ANKLE], float(person_score)


def _nan_row(frame_idx, t_sec):
    return {
        "frame": frame_idx, "time_sec": round(t_sec, 6), "leg": "none",
        "hip_x": float("nan"),   "hip_y": float("nan"),   "hip_score": 0.0,
        "knee_x": float("nan"),  "knee_y": float("nan"),  "knee_score": 0.0,
        "ankle_x": float("nan"), "ankle_y": float("nan"), "ankle_score": 0.0,
        "knee_angle_deg": float("nan"),
    }


TRANSFORMS = T.Compose([T.Resize((640, 640)), T.ToTensor()])


def process_trial(pos, trial_num, model, device):
    vid_dir    = os.path.join(BASE_DIR, "OptiTrack_Recordings",
                              "Participant_0", f"Position_{pos}", "Height_Joint-Level")
    video_path = os.path.join(vid_dir, f"trial_{trial_num}.mp4")
    if not os.path.isfile(video_path):
        print(f"[SKIP] Not found: {video_path}")
        return

    out_name = f"P_0_Pos_{pos}_H_Joint-Level_T_{trial_num}_detrpose_s_{SCORE_THRESH}.csv"
    out_path = os.path.join(vid_dir, out_name)
    if os.path.isfile(out_path):
        print(f"[SKIP] Already exists: {out_name}")
        return

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"[P0 Pos{pos} Trial {trial_num}] {total} frames @ {fps:.1f} fps -> {out_name}")

    rows      = []
    frame_idx = 0

    with torch.no_grad():
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t_sec  = frame_idx / fps
            im_pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            w, h   = im_pil.size
            orig_size = torch.tensor([[w, h]], device=device)
            im_data   = TRANSFORMS(im_pil).unsqueeze(0).to(device)

            scores_t, _labels, kpts_t = model(im_data, orig_size)
            scores_np = scores_t[0].cpu().numpy()
            kpts_np   = kpts_t[0].cpu().numpy()

            valid = np.where(scores_np >= SCORE_THRESH)[0]
            if len(valid) == 0:
                rows.append(_nan_row(frame_idx, t_sec))
            else:
                best   = valid[int(np.argmax(scores_np[valid]))]
                kp17   = kpts_np[best]
                pscore = scores_np[best]
                leg, hip, knee, ankle, sc = _pick_leg_detr(kp17, pscore)
                ang = _angle_deg(hip, knee, ankle)
                rows.append({
                    "frame": frame_idx, "time_sec": round(t_sec, 6), "leg": leg,
                    "hip_x":   round(float(hip[0]),   2), "hip_y":   round(float(hip[1]),   2),
                    "hip_score": round(sc, 4),
                    "knee_x":  round(float(knee[0]),  2), "knee_y":  round(float(knee[1]),  2),
                    "knee_score": round(sc, 4),
                    "ankle_x": round(float(ankle[0]), 2), "ankle_y": round(float(ankle[1]), 2),
                    "ankle_score": round(sc, 4),
                    "knee_angle_deg": round(ang, 4) if not math.isnan(ang) else float("nan"),
                })
            frame_idx += 1
            if frame_idx % 300 == 0:
                print(f"  frame {frame_idx}/{total}", flush=True)

    cap.release()

    fieldnames = ["frame","time_sec","leg",
                  "hip_x","hip_y","hip_score",
                  "knee_x","knee_y","knee_score",
                  "ankle_x","ankle_y","ankle_score",
                  "knee_angle_deg"]
    with open(out_path, "w", newline="") as fh:
        csv.DictWriter(fh, fieldnames=fieldnames).writeheader()
        csv.DictWriter(fh, fieldnames=fieldnames).writerows(rows)

    valid_n = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"  Done: {len(rows)} frames, {valid_n} valid -> {out_path}")


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Device: {device}")
    print(f"Loading DETRPose-S from {WEIGHT_PATH} ...")
    model = load_detrpose(CONFIG_PATH, WEIGHT_PATH, device)
    print("Model ready.\n")

    for pos in POSITIONS:
        for trial in TRIALS:
            process_trial(pos, trial, model, device)

    print("\nAll done.")


if __name__ == "__main__":
    main()
