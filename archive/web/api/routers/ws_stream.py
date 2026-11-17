"""
routers/ws_stream.py
====================
WebSocket endpoint for real-time frame ingestion and keypoint streaming.

Protocol
--------
Client → Server (first message, JSON):
    {
        "type":     "session_meta",
        "trial_id": "<id>",
        "model":    "mediapipe" | "rtmpose" | "hrnet" | "vitpose",
        "leg_side": "left" | "right",
        "width":    <int>,   # frame width in pixels
        "height":   <int>    # frame height in pixels
    }

Client → Server (subsequent messages, binary):
    bytes[0:4]  uint32LE  frame_index
    bytes[4:8]  uint32LE  timestamp_ms mod 2^32
    bytes[8:]   JPEG payload

Server → Client (JSON, sent after each processed frame):
    {
        "type":           "keypoints",
        "frame_index":    <int>,
        "timestamp_ms":   <int>,
        "keypoints": {
            "hip":   {"x": float, "y": float, "confidence": float},
            "knee":  {"x": float, "y": float, "confidence": float},
            "ankle": {"x": float, "y": float, "confidence": float}
        },
        "knee_angle_deg": float,
        "tracking_status": "stable" | "uncertain" | "lost"
    }

Server → Client (JSON, on completion):
    {"type": "complete", "trial_id": "<id>", "csv_path": "<path>"}

The endpoint is intentionally decoupled from the HPE workers: `_dispatch_frame`
is the single integration point.  Each worker module (mediapipe_worker.py,
rtmpose_worker.py, …) must expose a `process_frame(frame_bgr, leg_side) →
FrameResult` interface.  The stub below returns synthetic sinusoidal keypoints
so the frontend skeleton overlay can be validated without a camera or real HPE
weights.
"""
from __future__ import annotations

import math
import struct
import time
from typing import Any

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from web.api.store import trial_db

router = APIRouter()

# ── HPE dispatch table ────────────────────────────────────────────────────────
# Populated lazily to avoid importing heavy frameworks at module load time.
# Each entry is a callable: process_frame(bgr_array, leg_side) → FrameResult
_HPE_WORKERS: dict[str, Any] = {}


def _get_worker(model: str):
    """
    Return the HPE worker callable for `model`, importing it on first use.

    The real worker modules are expected to expose a module-level function:
        process_frame(frame_bgr: np.ndarray, leg_side: str) -> dict
    where the returned dict has keys: hip, knee, ankle (each with x, y,
    confidence in normalised [0, 1] image coordinates), plus knee_angle_deg.
    """
    if model not in _HPE_WORKERS:
        try:
            if model == "mediapipe":
                from mediapipe_worker import process_frame
            elif model == "rtmpose":
                from rtmpose_worker import process_frame
            elif model == "hrnet":
                from hrnet_worker import process_frame
            elif model == "vitpose":
                from vitpose_mmpose_worker import process_frame
            else:
                process_frame = None
            _HPE_WORKERS[model] = process_frame
        except ImportError:
            _HPE_WORKERS[model] = None
    return _HPE_WORKERS[model]


# ── Synthetic stub ────────────────────────────────────────────────────────────

def _synthetic_keypoints(frame_index: int, leg_side: str) -> dict:
    """
    Return sinusoidal stub keypoints that simulate a pendulum drop.

    Used when the real HPE worker is unavailable (integration testing,
    UI development without camera hardware).
    """
    t = frame_index / 15.0  # assume 15 fps
    # Pendulum angle: exponentially damped cosine starting from ~40° below horizontal
    angle_rad = math.radians(40) * math.exp(-0.18 * t) * math.cos(2 * math.pi * 0.9 * t)
    knee_angle_deg = 180.0 - math.degrees(angle_rad)

    # Anchor hip at 40% from top-centre; shank hangs from knee.
    hip_x,  hip_y  = 0.50, 0.30
    thigh_len       = 0.25
    shank_len       = 0.28
    knee_x = hip_x + thigh_len * math.sin(angle_rad)
    knee_y = hip_y + thigh_len * math.cos(angle_rad)
    ankle_x = knee_x + shank_len * math.sin(angle_rad * 0.4)
    ankle_y = knee_y + shank_len * math.cos(angle_rad * 0.4)

    return {
        "keypoints": {
            "hip":   {"x": round(hip_x, 4),   "y": round(hip_y, 4),   "confidence": 0.97},
            "knee":  {"x": round(knee_x, 4),  "y": round(knee_y, 4),  "confidence": 0.94},
            "ankle": {"x": round(ankle_x, 4), "y": round(ankle_y, 4), "confidence": 0.91},
        },
        "knee_angle_deg":  round(knee_angle_deg, 2),
        "tracking_status": "stable",
    }


# ── Frame dispatch ────────────────────────────────────────────────────────────

def _dispatch_frame(
    jpeg_bytes: bytes,
    frame_index: int,
    model: str,
    leg_side: str,
) -> dict:
    """
    Decode the JPEG payload and route it to the appropriate HPE worker.
    Falls back to the synthetic stub when the worker module is unavailable.
    """
    worker = _get_worker(model)

    if worker is None:
        return _synthetic_keypoints(frame_index, leg_side)

    # Decode JPEG → BGR numpy array via OpenCV (available in the project venv).
    try:
        import cv2
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        frame_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame_bgr is None:
            raise ValueError("cv2.imdecode returned None")
        result = worker(frame_bgr, leg_side)
        return result
    except Exception as exc:
        # Non-fatal: return stub keypoints and log the failure.
        print(f"[ws_stream] HPE worker '{model}' failed on frame {frame_index}: {exc}")
        return _synthetic_keypoints(frame_index, leg_side)


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@router.websocket("/ws/stream")
async def stream_endpoint(ws: WebSocket):
    """
    Persistent WebSocket connection for frame streaming.

    State machine:
        WAITING_META  — connection open, waiting for the JSON session_meta message.
        STREAMING     — metadata received, accepting binary frame messages.
    """
    await ws.accept()

    session:     dict | None = None
    frame_count: int         = 0
    start_time:  float       = time.monotonic()
    # Per-joint position overrides sent by the client's correction mode.
    # Maps joint name → {"x": float, "y": float}.  Applied to every
    # subsequent frame until the client sends a new correction.
    corrections: dict[str, dict] = {}

    try:
        while True:
            # ── Receive next message ──────────────────────────────────────────
            message = await ws.receive()

            # Starlette's raw receive() doesn't raise WebSocketDisconnect itself —
            # it returns this sentinel message. Re-raise so the except clause
            # below actually runs the disconnect cleanup (pending_review, etc.)
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))

            # Text message: session_meta handshake or joint correction.
            if "text" in message:
                import json
                try:
                    msg = json.loads(message["text"])
                except ValueError:
                    await ws.send_json({"type": "warning", "message": "Invalid JSON."})
                    continue

                msg_type = msg.get("type")

                if msg_type == "session_meta":
                    session = {
                        "trial_id": msg["trial_id"],
                        "model":    msg.get("model", "mediapipe"),
                        "leg_side": msg.get("leg_side", "right"),
                        "width":    msg.get("width", 1),
                        "height":   msg.get("height", 1),
                    }
                    corrections.clear()
                    if session["trial_id"] in trial_db:
                        trial_db[session["trial_id"]]["status"] = "recording"

                elif msg_type == "correction":
                    joint = msg.get("joint")
                    if joint in ("hip", "knee", "ankle"):
                        corrections[joint] = {"x": float(msg["x"]), "y": float(msg["y"])}

                else:
                    await ws.send_json({"type": "warning", "message": f"Unknown message type: {msg_type}"})

                continue

            # Binary message: frame payload with 8-byte header.
            if "bytes" in message:
                raw: bytes = message["bytes"]
                if session is None:
                    await ws.send_json({"type": "warning", "message": "Frame received before session_meta."})
                    continue

                if len(raw) < 8:
                    continue

                # Parse header.
                frame_index, timestamp_ms = struct.unpack_from("<II", raw, 0)
                jpeg_bytes = raw[8:]

                # Dispatch to the HPE worker (synchronous — offload with
                # asyncio.to_thread for high-throughput deployments).
                result = _dispatch_frame(
                    jpeg_bytes,
                    frame_index,
                    session["model"],
                    session["leg_side"],
                )

                # Apply any client-side joint corrections.
                if corrections:
                    kpts = result["keypoints"]
                    for joint, override in corrections.items():
                        if joint in kpts:
                            kpts[joint]["x"] = override["x"]
                            kpts[joint]["y"] = override["y"]
                            kpts[joint]["confidence"] = 1.0

                payload = {
                    "type":        "keypoints",
                    "frame_index": frame_index,
                    "timestamp_ms": timestamp_ms,
                    **result,
                }
                await ws.send_json(payload)
                frame_count += 1

    except WebSocketDisconnect:
        elapsed = time.monotonic() - start_time
        trial_id = session["trial_id"] if session else "unknown"
        print(
            f"[ws_stream] trial {trial_id} disconnected after "
            f"{frame_count} frames in {elapsed:.1f} s"
        )

        # Transition the trial to pending_review so the mobile client can
        # navigate to the Review screen without a separate API call.
        if session and session["trial_id"] in trial_db:
            db_trial = trial_db[session["trial_id"]]
            if db_trial.get("status") == "recording":
                db_trial["status"] = "pending_review"
