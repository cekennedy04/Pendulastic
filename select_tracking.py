"""
select_tracking.py
==================
Interactive tool to configure per-video tracking preferences.

For each video the user sets:
  1. Start frame  — press SPACE at the desired frame
  2. Hip joint    — click on the patient's hip (marks person + leg side)
  3. Knee joint   — click on the patient's knee
  4. Ankle joint  — click on the patient's ankle

Controls
--------
  SPACE           : mark current frame as analysis start
  Left click      : place the next joint (HIP → KNEE → ANKLE in sequence)
  BACKSPACE       : clear all joint marks and restart the sequence
  F               : jump to start_frame (if set)
  LEFT / RIGHT    : step ±1 frame
  , / .           : step ±10 frames
  ENTER           : save selection and advance to next video
  S               : skip this video (keep existing/default)
  Q / ESC         : quit

Saves: tracking_selections.json (same folder as this script)

Usage:
    .venv\\Scripts\\python.exe select_tracking.py [--duo-only] [--all] [--pid SUBSTR]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re

import cv2
import numpy as np

# ---------------------------------------------------------------------------
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
VIDEO_ROOT = os.path.join(BASE_DIR, "Recordings")
SEL_JSON   = os.path.join(BASE_DIR, "tracking_selections.json")
WINDOW     = "Tracking Selection"

# Joint sequence colours: HIP=blue, KNEE=green, ANKLE=red
_JOINT_ORDER  = ["hip", "knee", "ankle"]
_JOINT_COLOUR = {"hip": (255, 80, 30), "knee": (50, 220, 50), "ankle": (30, 80, 255)}
_JOINT_LABEL  = {"hip": "HIP (1/3)", "knee": "KNEE (2/3)", "ankle": "ANKLE (3/3)"}
# ---------------------------------------------------------------------------


def _rel_key(path: str) -> str:
    return os.path.relpath(os.path.abspath(path), BASE_DIR).replace("\\", "/")


def _load_all() -> dict:
    if os.path.exists(SEL_JSON):
        with open(SEL_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_all(data: dict) -> None:
    with open(SEL_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"[saved] {SEL_JSON}")


def discover_videos(duo_only: bool = False, pid_filter: str = "") -> list:
    videos = []
    for ext in ("*.avi", "*.mp4"):
        for p in glob.glob(os.path.join(VIDEO_ROOT, "**", ext), recursive=True):
            if duo_only and "duo" not in p.lower():
                continue
            if pid_filter and pid_filter.lower() not in p.lower():
                continue
            if not re.search(r"trial[_\s]*\d+", os.path.basename(p), re.I):
                continue
            videos.append(p)
    videos.sort()
    return videos


# ---------------------------------------------------------------------------
# Interactive selector
# ---------------------------------------------------------------------------

class VideoSelector:
    def __init__(self, video_path: str, existing: dict):
        self.path = video_path
        self.key  = _rel_key(video_path)

        # Existing or default state — support both old click_x/y and new hip/knee/ankle
        def _ex(key_new, key_old=None):
            v = existing.get(key_new)
            if v is None and key_old:
                v = existing.get(key_old)
            return v

        self.start_frame: int | None = existing.get("start_frame")
        self.joints: dict = {}
        for j in _JOINT_ORDER:
            xn = _ex(f"{j}_x_norm")
            yn = _ex(f"{j}_y_norm")
            if j == "hip":
                xn = xn if xn is not None else _ex("hip_x_norm", "click_x_norm")
                yn = yn if yn is not None else _ex("hip_y_norm", "click_y_norm")
            if xn is not None and yn is not None:
                self.joints[j] = (xn, yn)

        # Sequence state: which joint to place next
        self._next_joint_idx = len([j for j in _JOINT_ORDER if j in self.joints])
        if self._next_joint_idx >= len(_JOINT_ORDER):
            self._next_joint_idx = len(_JOINT_ORDER)

        # Video
        self.cap   = cv2.VideoCapture(video_path)
        self.fps   = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.total = max(1, int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        self.w     = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.h     = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._frame_cache: dict = {}

    def release(self):
        self.cap.release()

    def _read_frame(self, fi: int) -> np.ndarray | None:
        if fi in self._frame_cache:
            return self._frame_cache[fi]
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, frame = self.cap.read()
        if not ok:
            return None
        self._frame_cache[fi] = frame.copy()
        if len(self._frame_cache) > 20:
            del self._frame_cache[min(self._frame_cache)]
        return frame

    def _overlay(self, frame: np.ndarray, fi: int) -> np.ndarray:
        out = frame.copy()
        h, w = out.shape[:2]

        # ── Start-frame marker ────────────────────────────────────────────
        if self.start_frame is not None:
            sf_x = int(self.start_frame / max(self.total - 1, 1) * w)
            cv2.line(out, (sf_x, 0), (sf_x, h), (0, 220, 0), 2)
            cv2.putText(out, f"START f={self.start_frame} ({self.start_frame/self.fps:.1f}s)",
                        (sf_x + 5, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 220, 0), 2, cv2.LINE_AA)

        # ── Joint markers ─────────────────────────────────────────────────
        for jname, (xn, yn) in self.joints.items():
            px, py = int(xn * w), int(yn * h)
            col = _JOINT_COLOUR[jname]
            cv2.circle(out, (px, py), 12, col, 2)
            cv2.drawMarker(out, (px, py), col, cv2.MARKER_CROSS, 22, 2)
            cv2.putText(out, jname.upper(), (px + 14, py + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, 2, cv2.LINE_AA)

        # ── Next-joint prompt ─────────────────────────────────────────────
        if self._next_joint_idx < len(_JOINT_ORDER):
            nj = _JOINT_ORDER[self._next_joint_idx]
            col = _JOINT_COLOUR[nj]
            label = f"Click to place: {_JOINT_LABEL[nj]}"
            cv2.putText(out, label, (10, h - 80),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, col, 2, cv2.LINE_AA)
        else:
            cv2.putText(out, "All joints set — press ENTER to save",
                        (10, h - 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (100, 255, 100), 2, cv2.LINE_AA)

        # ── HUD ───────────────────────────────────────────────────────────
        lines = [
            f"Frame {fi}/{self.total-1}  ({fi/self.fps:.2f}s)  |  "
            f"Start: {'f='+str(self.start_frame) if self.start_frame is not None else 'not set'}",
            "SPACE=start frame  BACKSPACE=reset joints  LEFT/RIGHT=step  ,/.=step10",
            "ENTER=save  S=skip  Q=quit",
        ]
        y0 = h - 56
        for line in lines:
            cv2.putText(out, line, (10, y0), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (200, 200, 60), 1, cv2.LINE_AA)
            y0 += 16

        # Video name
        cv2.putText(out, os.path.basename(self.path),
                    (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.58,
                    (180, 220, 255), 2, cv2.LINE_AA)
        return out

    def _on_click(self, x: int, y: int):
        if self._next_joint_idx >= len(_JOINT_ORDER):
            return
        jname = _JOINT_ORDER[self._next_joint_idx]
        xn = round(x / self.w, 5)
        yn = round(y / self.h, 5)
        self.joints[jname] = (xn, yn)
        self._next_joint_idx += 1
        print(f"  [{jname}] ({x}, {y})  norm=({xn:.3f}, {yn:.3f})")

    def _clear_joints(self):
        self.joints.clear()
        self._next_joint_idx = 0
        print("  [joints cleared]")

    def run(self) -> str:
        """Returns 'save', 'skip', or 'quit'."""
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW, min(self.w, 1280), min(self.h, 720))

        fi_state = [0]

        def on_trackbar(val):
            fi_state[0] = val

        cv2.createTrackbar("Frame", WINDOW, 0, self.total - 1, on_trackbar)

        def on_mouse(event, x, y, flags, param):
            if event == cv2.EVENT_LBUTTONDOWN:
                self._on_click(x, y)

        cv2.setMouseCallback(WINDOW, on_mouse)

        result = "skip"

        while True:
            fi    = fi_state[0]
            frame = self._read_frame(fi)
            if frame is None:
                frame = np.zeros((self.h, self.w, 3), dtype=np.uint8)
            disp = self._overlay(frame, fi)
            cv2.imshow(WINDOW, disp)

            key = cv2.waitKey(30) & 0xFF

            if key in (13, 10):           # ENTER
                result = "save"; break
            elif key == ord('s'):         # Skip
                result = "skip"; break
            elif key in (ord('q'), 27):   # Q / ESC
                result = "quit"; break
            elif key == 32:               # SPACE — mark start frame
                self.start_frame = fi_state[0]
                print(f"  [start] frame {self.start_frame} ({self.start_frame/self.fps:.2f}s)")
            elif key == 8:                # BACKSPACE — clear joints
                self._clear_joints()
            elif key == ord('f') and self.start_frame is not None:
                fi_state[0] = self.start_frame
                cv2.setTrackbarPos("Frame", WINDOW, fi_state[0])
            elif key in (81, 2, 0x25):    # Left arrow
                fi_state[0] = max(0, fi_state[0] - 1)
                cv2.setTrackbarPos("Frame", WINDOW, fi_state[0])
            elif key in (83, 3, 0x27):    # Right arrow
                fi_state[0] = min(self.total - 1, fi_state[0] + 1)
                cv2.setTrackbarPos("Frame", WINDOW, fi_state[0])
            elif key == ord(','):
                fi_state[0] = max(0, fi_state[0] - 10)
                cv2.setTrackbarPos("Frame", WINDOW, fi_state[0])
            elif key == ord('.'):
                fi_state[0] = min(self.total - 1, fi_state[0] + 10)
                cv2.setTrackbarPos("Frame", WINDOW, fi_state[0])

        cv2.destroyWindow(WINDOW)
        return result

    def to_dict(self) -> dict:
        d: dict = {}
        if self.start_frame is not None:
            d["start_frame"] = self.start_frame
        for jname, (xn, yn) in self.joints.items():
            d[f"{jname}_x_norm"] = xn
            d[f"{jname}_y_norm"] = yn
        return d


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duo-only", action="store_true",
                    help="Only show duo videos")
    ap.add_argument("--all",      action="store_true",
                    help="Show videos even if they already have selections")
    ap.add_argument("--pid",      default="",
                    help="Filter by participant ID substring")
    args = ap.parse_args()

    videos = discover_videos(duo_only=args.duo_only, pid_filter=args.pid)
    if not videos:
        print("No videos found under", VIDEO_ROOT); return

    data = _load_all()

    def _has_sel(key):
        e = data.get(key, {})
        return bool(e.get("start_frame") is not None
                    or e.get("hip_x_norm") or e.get("click_x_norm"))

    to_process = [v for v in videos
                  if args.all or not _has_sel(_rel_key(v))]

    skipped = len(videos) - len(to_process)
    print(f"\n{len(to_process)} videos to configure  ({skipped} already have selections)\n")
    print("For each video: SPACE=start frame | click HIP then KNEE then ANKLE | ENTER=save\n")

    for idx, vpath in enumerate(to_process):
        key = _rel_key(vpath)
        print(f"[{idx+1}/{len(to_process)}] {os.path.basename(vpath)}")
        print(f"  {vpath}")
        existing = data.get(key, {})

        sel    = VideoSelector(vpath, existing)
        action = sel.run()
        sel.release()

        if action == "save":
            d = sel.to_dict()
            data[key] = d
            _save_all(data)
            print(f"  saved: {d}")
        elif action == "quit":
            print("Quitting."); break
        else:
            print("  skipped")

    print("\nDone.")


if __name__ == "__main__":
    main()
