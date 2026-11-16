"""
services/marker_store.py
========================
Thread-safe read/write wrapper around tracking_selections.json.

The on-disk format is identical to the one consumed by all HPE workers via
their _load_sel() functions, ensuring full backward compatibility.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

ROOT     = Path(__file__).resolve().parents[3]
SEL_FILE = ROOT / "tracking_selections.json"

_lock = threading.Lock()


class MarkerStore:
    def load(self) -> dict:
        with _lock:
            if not SEL_FILE.exists():
                return {}
            with open(SEL_FILE, encoding="utf-8") as fh:
                return json.load(fh)

    def save(self, video_path: str, markers, start_frame: int) -> None:
        """
        Write a marker entry for video_path.  The relative key uses forward
        slashes to match the convention used by _load_sel() in the workers.
        """
        rel = os.path.relpath(
            os.path.abspath(video_path), ROOT
        ).replace("\\", "/")

        with _lock:
            data = self.load()
            data[rel] = {
                "start_frame":  start_frame,
                "hip_x_norm":   markers.hip_x_norm,
                "hip_y_norm":   markers.hip_y_norm,
                "knee_x_norm":  markers.knee_x_norm,
                "knee_y_norm":  markers.knee_y_norm,
                "ankle_x_norm": markers.ankle_x_norm,
                "ankle_y_norm": markers.ankle_y_norm,
            }
            with open(SEL_FILE, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)

    def get(self, video_path: str) -> dict:
        rel = os.path.relpath(
            os.path.abspath(video_path), ROOT
        ).replace("\\", "/")
        return self.load().get(rel, {})
