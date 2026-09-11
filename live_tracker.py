"""
live_tracker.py
===============
Real-time knee angle calibration HUD for the Wartenberg Pendulum Test.

Uses MediaPipe PoseLandmarker (Tasks API, not the legacy mp.solutions).
Run from the project root:
    .venv/Scripts/python live_tracker.py

Controls:
    Q  — quit
    S  — save current frame to calibration_frame.jpg
"""
import math
import pathlib
import time

import cv2
import mediapipe as mp
import numpy as np

import capture_quality_guard as cqg

# ── Model ─────────────────────────────────────────────────────────────────────
_MODEL = pathlib.Path(__file__).parent / "models" / "mediapipe" / "pose_landmarker_full.task"

# ── BlazePose landmark indices (same for Tasks PoseLandmarker) ─────────────────
# RIGHT: hip=24  knee=26  ankle=28
# LEFT:  hip=23  knee=25  ankle=27
_SIDES = {
    "right": (24, 26, 28),
    "left":  (23, 25, 27),
}


def _knee_angle(hip: np.ndarray, knee: np.ndarray, ankle: np.ndarray) -> float:
    """Return the knee flexion angle in degrees (0 = full extension)."""
    u = hip   - knee   # thigh vector
    v = ankle - knee   # shank vector
    denom = float(np.linalg.norm(u) * np.linalg.norm(v))
    if denom < 1e-6:
        return float("nan")
    return math.degrees(math.acos(float(np.clip(np.dot(u, v) / denom, -1.0, 1.0))))


def _draw_hud(frame: np.ndarray, status: str, color, extra: str = "") -> None:
    h, w = frame.shape[:2]
    bar = frame.copy()
    cv2.rectangle(bar, (0, 0), (w, 60), (18, 18, 20), -1)
    cv2.addWeighted(bar, 0.50, frame, 0.50, 0, frame)
    cv2.putText(frame, status, (16, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.68, color, 2, cv2.LINE_AA)
    if extra:
        tw, _ = cv2.getTextSize(extra, cv2.FONT_HERSHEY_SIMPLEX, 0.68, 2)[:2]
        cv2.putText(frame, extra, (w - tw[0] - 16, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.68, (230, 230, 230), 2, cv2.LINE_AA)


def _draw_guard_panel(frame: np.ndarray, res, verdict: str) -> None:
    """Bottom panel: the two numbers that decide whether this camera position
    can produce a usable knee angle, plus what to do about it.

    Landmark visibility alone is NOT enough to clear a setup -- P17's right
    leg tracked landmarks happily while the camera looked along the thigh,
    and every recorded angle came out ~53 deg wrong. These are the numbers
    that would have caught it before recording.
    """
    h, w = frame.shape[:2]
    n_lines = 1 + len(res.reasons) if res is not None else 1
    panel_h = 34 + 26 * n_lines
    bar = frame.copy()
    cv2.rectangle(bar, (0, h - panel_h), (w, h), (18, 18, 20), -1)
    cv2.addWeighted(bar, 0.55, frame, 0.45, 0, frame)

    colors = {"GOOD": (30, 200, 70), "BAD": (0, 60, 240), "UNKNOWN": (150, 150, 150)}
    col = colors.get(verdict, colors["UNKNOWN"])
    cv2.putText(frame, f"PLACEMENT: {verdict}", (16, h - panel_h + 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.72, col, 2, cv2.LINE_AA)

    if res is None:
        return

    metrics = (f"thigh {res.thigh_px:.0f}px (want >={cqg.MIN_THIGH_PX:.0f})   "
               f"shank/thigh {res.ratio:.2f} (want "
               f"{cqg.RATIO_LO:.2f}-{cqg.RATIO_HI:.2f})")
    tw = cv2.getTextSize(metrics, cv2.FONT_HERSHEY_SIMPLEX, 0.58, 2)[0][0]
    cv2.putText(frame, metrics, (max(16, w - tw - 16), h - panel_h + 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.58, (225, 225, 225), 2, cv2.LINE_AA)

    for i, reason in enumerate(res.reasons):
        cv2.putText(frame, reason, (16, h - panel_h + 52 + 26 * i),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.58, (60, 190, 255), 2, cv2.LINE_AA)


def run() -> None:
    if not _MODEL.exists():
        print(f"Model not found: {_MODEL}")
        print("Expected path: models/mediapipe/pose_landmarker_full.task")
        return

    opts = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(_MODEL)),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.60,
        min_pose_presence_confidence=0.60,
        min_tracking_confidence=0.60,
    )

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("No camera found (index 0). Try cv2.VideoCapture(1) for an external camera.")
        return

    VIS_THRESH = 0.60
    t0 = time.monotonic()
    smoother = cqg.GuardSmoother()

    with mp.tasks.vision.PoseLandmarker.create_from_options(opts) as detector:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            ts_ms = int((time.monotonic() - t0) * 1000)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = detector.detect_for_video(mp_img, ts_ms)

            h, w = frame.shape[:2]
            status = "ADJUST CAMERA — hip, knee, and ankle must be visible"
            s_color = (0, 60, 240)
            extra = ""
            guard_res = None
            verdict = smoother.verdict()

            if result.pose_landmarks:
                lm = result.pose_landmarks[0]

                # Auto-select the side with the more visible knee
                r_vis = lm[26].visibility
                l_vis = lm[25].visibility
                side = "right" if r_vis >= l_vis else "left"
                hi, ki, ai = _SIDES[side]
                h_lm, k_lm, a_lm = lm[hi], lm[ki], lm[ai]

                if all(x.visibility > VIS_THRESH for x in (h_lm, k_lm, a_lm)):
                    def _px(lmk):
                        return np.array([lmk.x * w, lmk.y * h], dtype=float)

                    hip  = _px(h_lm)
                    knee = _px(k_lm)
                    ank  = _px(a_lm)
                    ang  = _knee_angle(hip, knee, ank)

                    guard_res = cqg.evaluate_leg_geometry(hip, knee, ank)
                    smoother.push(guard_res)
                    verdict = smoother.verdict()

                    # Limb turns red the moment the placement is unusable, so
                    # the operator sees it on the subject, not just in a panel.
                    LIMB = (50, 220, 100) if verdict == "GOOD" else (0, 60, 240)
                    for a, b in ((hip, knee), (knee, ank)):
                        cv2.line(frame,
                                 tuple(a.astype(int)), tuple(b.astype(int)),
                                 LIMB, 3, cv2.LINE_AA)
                    # Draw joint circles
                    for pt in (hip, knee, ank):
                        cv2.circle(frame, tuple(pt.astype(int)),
                                   10, (255, 255, 255), -1, cv2.LINE_AA)
                        cv2.circle(frame, tuple(pt.astype(int)),
                                   10, LIMB, 2, cv2.LINE_AA)

                    # Angle arc label beside knee
                    if math.isfinite(ang):
                        lx = int(knee[0]) + 18
                        ly = int(knee[1]) + 8
                        cv2.putText(frame, f"{ang:.0f}°",
                                    (lx, ly),
                                    cv2.FONT_HERSHEY_SIMPLEX, 1.1,
                                    (255, 255, 80), 2, cv2.LINE_AA)

                    if verdict == "GOOD":
                        status  = f"READY TO RECORD ({side} leg)"
                        s_color = (30, 200, 70)
                    else:
                        # Landmarks are visible and the old HUD would have said
                        # "ready" here. P17's right leg passed exactly this
                        # check and every angle it recorded was ~53 deg wrong.
                        status  = f"DO NOT RECORD ({side} leg) — fix camera placement"
                        s_color = (0, 60, 240)
                    extra   = f"Knee: {ang:.0f}°" if math.isfinite(ang) else ""

                else:
                    status  = "PARTIAL VIEW — step back or rotate camera"
                    s_color = (0, 140, 255)

            _draw_hud(frame, status, s_color, extra)
            _draw_guard_panel(frame, guard_res, verdict)

            cv2.imshow("Pendulastic — Live Calibration", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            elif key == ord("s"):
                out = "calibration_frame.jpg"
                cv2.imwrite(out, frame)
                print(f"Saved {out}")

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    run()
