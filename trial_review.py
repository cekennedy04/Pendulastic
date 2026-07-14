"""
trial_review.py
===============
Post-trial interactive fitting review and start-frame selector.

After MediaPipe writes the angle CSV for a trial, call::

    from trial_review import TrialReviewer
    result = TrialReviewer(video_path, csv_path, current_sel).run()
    # result["action"] in {"accept", "rerun", "skip"}
    # result["sel"]    is the updated tracking dict (absent for "skip")

What you see
------------
  Top panel  : video frame with two overlaid marker styles
               - Filled circle    = joint position detected by MediaPipe (from CSV)
               - Ring + cross     = user reference position (from tracking_selections.json)
               If they match, the fit is correct. If they differ, re-click.

  Bottom strip: knee-angle time series so you can identify where the
                pendulum is released and pick the right start frame.

Controls
--------
  SPACE        mark current frame as analysis start
  Left / Right step ±1 frame        A / D also work
  , / .        step ±10 frames
  F            jump to current start_frame
  Click        re-click joints in order: HIP -> KNEE -> ANKLE
               (clicking past ANKLE cycles back to HIP)
  BACKSPACE    clear all joint overrides and restart the click sequence
  ENTER        accept — save and proceed
  R            rerun  — save and re-run MediaPipe inference with new settings
  S / Q / ESC  skip   — discard changes, move on
"""
from __future__ import annotations

import math
import os
from typing import Optional

import cv2
import numpy as np
import pandas as pd

_WINDOW      = "Trial Review"
_JOINT_ORDER = ["hip", "knee", "ankle"]
_JOINT_COLOR = {
    "hip":   (255, 130,  30),   # orange
    "knee":  ( 50, 220,  50),   # green
    "ankle": ( 30, 120, 255),   # blue
}
_JOINT_LABEL = {
    "hip":   "HIP (1/3)",
    "knee":  "KNEE (2/3)",
    "ankle": "ANKLE (3/3)",
}


class TrialReviewer:
    """
    Interactive post-trial review window.

    Parameters
    ----------
    video_path    : path to the original trial video
    csv_path      : path to the CSV written by mediapipe_worker
    current_sel   : dict from tracking_selections.json for this video
                    (may be empty if the video was not pre-configured)
    """

    def __init__(self, video_path: str, csv_path: str, current_sel: dict):
        self.video_path = video_path
        self.csv_path   = csv_path

        # ── Joint positions (normalised 0-1) ──────────────────────────────────
        self.joints: dict[str, tuple[float, float]] = {}
        for j in _JOINT_ORDER:
            xn = current_sel.get(f"{j}_x_norm")
            yn = current_sel.get(f"{j}_y_norm")
            if j == "hip":                           # backwards compat
                xn = xn or current_sel.get("click_x_norm")
                yn = yn or current_sel.get("click_y_norm")
            if xn is not None and yn is not None:
                self.joints[j] = (float(xn), float(yn))

        # First click will set the joint at _next_joint_idx
        self._next_joint_idx: int = 0

        # ── Start frame ───────────────────────────────────────────────────────
        self.start_frame: Optional[int] = current_sel.get("start_frame")

        # ── Load angle CSV ────────────────────────────────────────────────────
        self._csv_frames:  np.ndarray = np.array([], dtype=int)
        self._csv_angles:  np.ndarray = np.array([], dtype=float)
        self._csv_hip_px:  list       = []
        self._csv_kne_px:  list       = []
        self._csv_ank_px:  list       = []
        self._load_csv()

        # ── Video ─────────────────────────────────────────────────────────────
        self.cap   = cv2.VideoCapture(video_path)
        self.fps   = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.total = max(1, int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        self.w     = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.h     = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._cache: dict[int, np.ndarray] = {}

        # ── Display geometry (set in run()) ───────────────────────────────────
        self._disp_w  = 1
        self._video_h = 1

    # ── CSV loading ────────────────────────────────────────────────────────────

    def _load_csv(self) -> None:
        if not os.path.isfile(self.csv_path):
            return
        try:
            df = pd.read_csv(self.csv_path)
            if "frame" in df.columns:
                self._csv_frames = df["frame"].values.astype(int)
            if "knee_angle_deg" in df.columns:
                self._csv_angles = df["knee_angle_deg"].values.astype(float)
            for col, store in [
                (("hip_x",   "hip_y"),   "_csv_hip_px"),
                (("knee_x",  "knee_y"),  "_csv_kne_px"),
                (("ankle_x", "ankle_y"), "_csv_ank_px"),
            ]:
                if col[0] in df.columns and col[1] in df.columns:
                    setattr(self, store,
                            list(zip(df[col[0]].values.astype(float),
                                     df[col[1]].values.astype(float))))
        except Exception as exc:
            print(f"[trial_review] could not load CSV: {exc}")

    # ── Frame cache ────────────────────────────────────────────────────────────

    def _read_frame(self, fi: int) -> np.ndarray:
        if fi in self._cache:
            return self._cache[fi]
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, frame = self.cap.read()
        frame = frame if ok else np.zeros((self.h, self.w, 3), dtype=np.uint8)
        self._cache[fi] = frame.copy()
        if len(self._cache) > 40:
            del self._cache[min(self._cache)]
        return frame

    def release(self) -> None:
        self.cap.release()

    # ── CSV lookup ─────────────────────────────────────────────────────────────

    def _csv_idx(self, fi: int) -> Optional[int]:
        if len(self._csv_frames) == 0:
            return None
        idxs = np.where(self._csv_frames == fi)[0]
        return int(idxs[0]) if len(idxs) else None

    # ── Video panel overlay ────────────────────────────────────────────────────

    def _overlay_video(self, frame: np.ndarray, fi: int) -> np.ndarray:
        out  = frame.copy()
        h, w = out.shape[:2]

        # -- MediaPipe detections from CSV (filled circles) -------------------
        row = self._csv_idx(fi)
        if row is not None:
            for store, jname in [
                ("_csv_hip_px",  "hip"),
                ("_csv_kne_px",  "knee"),
                ("_csv_ank_px",  "ankle"),
            ]:
                pts = getattr(self, store)
                if pts and row < len(pts):
                    px, py = pts[row]
                    if math.isfinite(px) and math.isfinite(py):
                        col = _JOINT_COLOR[jname]
                        cx, cy = int(round(px)), int(round(py))
                        cv2.circle(out, (cx, cy), 9, col, -1)   # filled = detected
                        cv2.putText(out, jname[0].upper(), (cx + 11, cy + 5),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2, cv2.LINE_AA)

        # -- User reference positions (hollow ring + cross) -------------------
        for jname, (xn, yn) in self.joints.items():
            px, py = int(xn * w), int(yn * h)
            col = _JOINT_COLOR[jname]
            cv2.circle(out, (px, py), 15, col, 2)
            cv2.drawMarker(out, (px, py), col, cv2.MARKER_CROSS, 26, 2)

        # -- Legend -----------------------------------------------------------
        lx, ly = w - 180, 18
        cv2.putText(out, "Filled = detected", (lx, ly),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(out, "Ring   = user ref", (lx, ly + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (200, 200, 200), 1, cv2.LINE_AA)

        # -- Start frame label ------------------------------------------------
        if self.start_frame is not None:
            col   = (0, 230, 0) if fi == self.start_frame else (0, 140, 0)
            label = f"START  f={self.start_frame}  ({self.start_frame/self.fps:.1f}s)"
            cv2.putText(out, label, (10, 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.62, col, 2, cv2.LINE_AA)

        # -- Next joint prompt ------------------------------------------------
        if self._next_joint_idx < len(_JOINT_ORDER):
            nj  = _JOINT_ORDER[self._next_joint_idx]
            col = _JOINT_COLOR[nj]
            cv2.putText(out, f"Click: {_JOINT_LABEL[nj]}", (10, h - 78),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2, cv2.LINE_AA)
        else:
            cv2.putText(out, "All joints set", (10, h - 78),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (100, 255, 100), 2, cv2.LINE_AA)

        # -- HUD --------------------------------------------------------------
        hud = [
            f"Frame {fi}/{self.total-1}  ({fi/self.fps:.2f}s)",
            "SPACE=start  BKSP=clear joints  LEFT/RIGHT=step  ,/.=x10",
            "ENTER=accept  R=rerun  S/Q/ESC=skip",
        ]
        for i, line in enumerate(hud):
            cv2.putText(out, line, (10, h - 50 + i * 17),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.40, (200, 200, 60), 1, cv2.LINE_AA)

        # -- Video filename ---------------------------------------------------
        cv2.putText(out, os.path.basename(self.video_path),
                    (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.46,
                    (180, 220, 255), 1, cv2.LINE_AA)

        return out

    # ── Angle strip ────────────────────────────────────────────────────────────

    def _render_strip(self, strip_w: int, strip_h: int, fi: int) -> np.ndarray:
        strip    = np.full((strip_h, strip_w, 3), 28, dtype=np.uint8)
        ang      = self._csv_angles
        frames   = self._csv_frames
        if len(ang) == 0 or len(frames) == 0:
            cv2.putText(strip, "No angle data yet", (10, strip_h // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (100, 100, 100), 1)
            return strip

        pad = 28
        pw  = strip_w - 2 * pad
        ph  = strip_h - 2 * pad

        fi_min, fi_max = int(frames.min()), int(frames.max())
        fi_range = max(fi_max - fi_min, 1)

        valid = np.isfinite(ang)
        if valid.sum() == 0:
            return strip
        ang_min = float(ang[valid].min()) - 4
        ang_max = float(ang[valid].max()) + 4
        ang_rng = max(ang_max - ang_min, 1.0)

        def _px(f_val: float, a_val: float):
            x = pad + int((f_val - fi_min) / fi_range * pw)
            y = pad + int((1.0 - (a_val - ang_min) / ang_rng) * ph)
            return x, y

        # Axes
        cv2.line(strip, (pad, pad), (pad, strip_h - pad), (60, 60, 60), 1)
        cv2.line(strip, (pad, strip_h - pad), (strip_w - pad, strip_h - pad), (60, 60, 60), 1)

        # Angle curve
        pts = [_px(frames[i], ang[i]) for i in range(len(frames)) if valid[i]]
        if len(pts) > 1:
            cv2.polylines(strip, [np.array(pts, np.int32)],
                          isClosed=False, color=(70, 190, 70), thickness=1)

        # Start-frame vertical line
        if self.start_frame is not None and fi_min <= self.start_frame <= fi_max:
            sx, _ = _px(self.start_frame, ang_min)
            cv2.line(strip, (sx, pad), (sx, strip_h - pad), (0, 230, 0), 2)
            cv2.putText(strip, "start", (sx + 3, pad + 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 230, 0), 1)

        # Current frame cursor
        if fi_min <= fi <= fi_max:
            cx, _ = _px(fi, ang_min)
            cv2.line(strip, (cx, pad), (cx, strip_h - pad), (0, 200, 255), 1)

        # Y-axis labels
        cv2.putText(strip, f"{ang_max:.0f}", (2, pad + 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.33, (130, 130, 130), 1)
        cv2.putText(strip, f"{ang_min:.0f}", (2, strip_h - pad - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.33, (130, 130, 130), 1)
        cv2.putText(strip, "knee angle (deg)", (strip_w // 2 - 45, strip_h - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.33, (130, 130, 130), 1)

        return strip

    # ── Mouse click ────────────────────────────────────────────────────────────

    def _on_click(self, x: int, y: int) -> None:
        if y >= self._video_h:
            return   # click landed in the angle strip, ignore

        xn = round(x / self._disp_w, 5)
        yn = round(y / self._video_h, 5)

        # Cycle back to hip after ANKLE
        if self._next_joint_idx >= len(_JOINT_ORDER):
            self._next_joint_idx = 0

        jname = _JOINT_ORDER[self._next_joint_idx]
        self.joints[jname] = (xn, yn)
        self._next_joint_idx += 1
        print(f"  [{jname}] norm=({xn:.3f}, {yn:.3f})")

    def _clear_joints(self) -> None:
        self.joints.clear()
        self._next_joint_idx = 0
        print("  [joints cleared]")

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self) -> dict:
        """
        Open the review window and return when the user makes a decision.

        Returns
        -------
        {"action": "accept", "sel": <dict>}
        {"action": "rerun",  "sel": <dict>}
        {"action": "skip"}
        """
        # Display geometry
        self._disp_w  = min(self.w, 1280)
        scale         = self._disp_w / self.w
        self._video_h = int(self.h * scale)
        strip_h       = max(110, int(self._video_h * 0.35))
        disp_h        = self._video_h + strip_h

        cv2.namedWindow(_WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(_WINDOW, self._disp_w, disp_h)

        fi_state = [max(0, int(self.start_frame or 0))]

        def _on_trackbar(val):
            fi_state[0] = val

        cv2.createTrackbar("Frame", _WINDOW, fi_state[0], self.total - 1, _on_trackbar)

        def _on_mouse(event, x, y, flags, _param):
            if event == cv2.EVENT_LBUTTONDOWN:
                self._on_click(x, y)

        cv2.setMouseCallback(_WINDOW, _on_mouse)
        action = "skip"

        while True:
            fi    = fi_state[0]
            frame = self._read_frame(fi)

            vid_panel = self._overlay_video(frame, fi)
            vid_panel = cv2.resize(vid_panel, (self._disp_w, self._video_h))
            strip     = self._render_strip(self._disp_w, strip_h, fi)
            composite = np.vstack([vid_panel, strip])
            cv2.imshow(_WINDOW, composite)

            key = cv2.waitKey(30) & 0xFF

            if key in (13, 10):                      # ENTER — accept
                action = "accept"; break
            elif key == ord('r'):                    # R — rerun
                action = "rerun"; break
            elif key in (ord('s'), ord('q'), 27):   # S / Q / ESC — skip
                action = "skip"; break
            elif key == 32:                          # SPACE — mark start
                self.start_frame = fi_state[0]
                print(f"  [start] frame {self.start_frame}  ({self.start_frame/self.fps:.2f}s)")
            elif key == 8:                           # BACKSPACE — clear joints
                self._clear_joints()
            elif key == ord('f') and self.start_frame is not None:
                fi_state[0] = self.start_frame
                cv2.setTrackbarPos("Frame", _WINDOW, fi_state[0])
            elif key in (81, 2, 0x25, ord('a')):    # Left / A
                fi_state[0] = max(0, fi_state[0] - 1)
                cv2.setTrackbarPos("Frame", _WINDOW, fi_state[0])
            elif key in (83, 3, 0x27, ord('d')):    # Right / D
                fi_state[0] = min(self.total - 1, fi_state[0] + 1)
                cv2.setTrackbarPos("Frame", _WINDOW, fi_state[0])
            elif key == ord(','):
                fi_state[0] = max(0, fi_state[0] - 10)
                cv2.setTrackbarPos("Frame", _WINDOW, fi_state[0])
            elif key == ord('.'):
                fi_state[0] = min(self.total - 1, fi_state[0] + 10)
                cv2.setTrackbarPos("Frame", _WINDOW, fi_state[0])

        cv2.destroyWindow(_WINDOW)

        if action == "skip":
            return {"action": "skip"}

        sel: dict = {}
        if self.start_frame is not None:
            sel["start_frame"] = self.start_frame
        for jname, (xn, yn) in self.joints.items():
            sel[f"{jname}_x_norm"] = xn
            sel[f"{jname}_y_norm"] = yn

        return {"action": action, "sel": sel}
