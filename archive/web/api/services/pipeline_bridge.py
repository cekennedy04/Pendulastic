"""
services/pipeline_bridge.py
============================
Async bridge between the FastAPI layer and the individual HPE worker scripts.
Spawns the appropriate worker subprocess directly (mediapipe_worker.py,
rtmpose_worker.py, hrnet_worker.py, vitpose_mmpose_worker.py) rather than
calling the batch pendulastic_pipeline.py, which only accepts --compare.

Python interpreter routing:
  mediapipe / rtmpose  → .venv/Scripts/python.exe
  hrnet / vitpose      → miniconda3/envs/openmmlab/python.exe

Uses run_in_executor + subprocess.run (blocking) instead of
asyncio.create_subprocess_exec to avoid NotImplementedError on Windows
SelectorEventLoop.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from web.api.models.trial import TrialStatus
from web.api.store import trial_db

# Project root (three levels above this file: web/api/services/ → root)
ROOT = Path(__file__).resolve().parents[3]

VENV_PY      = ROOT / ".venv" / "Scripts" / "python.exe"
OPENMMLAB_PY = Path(r"C:\Users\cladi\miniconda3\envs\openmmlab\python.exe")

MODELS_DIR   = ROOT / "models"

# Worker scripts
MP_WRK         = ROOT / "mediapipe_worker.py"
RTMP_WRK       = ROOT / "rtmpose_worker.py"
HRNET_WRK      = ROOT / "hrnet_worker.py"
VITPOSE_WRK    = ROOT / "vitpose_mmpose_worker.py"

# MediaPipe task file — use the "full" variant
MP_TASK        = MODELS_DIR / "mediapipe" / "pose_landmarker_full.task"

# RTMPose ONNX — default to the medium variant
RTMP_ONNX      = (MODELS_DIR / "rtmpose" / "20230831" / "rtmpose_onnx"
                  / "rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504"
                  / "end2end.onnx")

# HRNet
HRNET_CFG      = Path(r"C:\Users\cladi\mmpose\configs\body_2d_keypoint\topdown_heatmap"
                      r"\coco\td-hm_hrnet-w48_8xb32-210e_coco-256x192.py")
HRNET_CKPT     = (MODELS_DIR / "PosePipeline" / "checkpoints"
                  / "td-hm_hrnet-w48_8xb32-210e_coco-256x192-0e67c616_20220913.pth")

# ViTPose (mmpose)
VITPOSE_CFG    = Path(r"C:\Users\cladi\mmpose\configs\body_2d_keypoint\topdown_heatmap"
                      r"\coco\td-hm_ViTPose-base_8xb64-210e_coco-256x192.py")
VITPOSE_CKPT   = (MODELS_DIR / "vitpose"
                  / "td-hm_ViTPose-base_8xb64-210e_coco-256x192-216eae50_20230314.pth")


def _venv() -> str:
    return str(VENV_PY) if VENV_PY.exists() else sys.executable


def _openmmlab() -> str:
    return str(OPENMMLAB_PY) if OPENMMLAB_PY.exists() else sys.executable


def _csv_path(video_path: str, hpe_model: str) -> str:
    """Derive output CSV path: <video_dir>/<stem>_<model>.csv"""
    p = Path(video_path)
    return str(p.parent / f"{p.stem}_{hpe_model}.csv")


def _build_cmd(video_path: str, csv_path: str, leg_side: str, hpe_model: str) -> list[str]:
    """Return the full subprocess command list for the given model."""
    leg_flags = ["--leg-side", leg_side] if leg_side else []

    if hpe_model == "mediapipe":
        return [
            _venv(), "-u", str(MP_WRK),
            "--video",        video_path,
            "--csv",          csv_path,
            "--task",         str(MP_TASK),
            "--score-thresh", "0.5",
            *leg_flags,
        ]

    if hpe_model == "rtmpose":
        return [
            _venv(), "-u", str(RTMP_WRK),
            "--video",        video_path,
            "--csv",          csv_path,
            "--pose-onnx",    str(RTMP_ONNX),
            "--score-thresh", "0.3",
            *leg_flags,
        ]

    if hpe_model == "hrnet":
        return [
            _openmmlab(), "-u", str(HRNET_WRK),
            "--video",        video_path,
            "--csv",          csv_path,
            "--config",       str(HRNET_CFG),
            "--ckpt",         str(HRNET_CKPT),
            "--score-thresh", "0.3",
            *leg_flags,
        ]

    if hpe_model == "vitpose":
        return [
            _openmmlab(), "-u", str(VITPOSE_WRK),
            "--video",        video_path,
            "--csv",          csv_path,
            "--config",       str(VITPOSE_CFG),
            "--ckpt",         str(VITPOSE_CKPT),
            "--score-thresh", "0.3",
            *leg_flags,
        ]

    raise ValueError(f"Unknown hpe_model: {hpe_model!r}")


class PipelineBridge:
    """Thin async wrapper that calls the appropriate HPE worker subprocess."""

    async def run_async(
        self,
        trial_id: str,
        video_path: str,
        leg_side: str,
        hpe_model: str = "mediapipe",
    ) -> None:
        """
        Execute the HPE pipeline for one video and update trial_db on completion.
        Runs in a FastAPI BackgroundTask — never awaited by the request handler.
        """
        csv_out = _csv_path(video_path, hpe_model)

        try:
            cmd = _build_cmd(video_path, csv_out, leg_side, hpe_model)
        except ValueError as exc:
            trial_db[trial_id]["status"] = TrialStatus.ERROR
            trial_db[trial_id]["error"]  = str(exc)
            return

        trial_db[trial_id]["status"] = TrialStatus.RECORDING

        loop = asyncio.get_event_loop()

        def _run_blocking() -> subprocess.CompletedProcess:
            return subprocess.run(
                cmd,
                capture_output=True,
                cwd=str(ROOT),
            )

        try:
            result: subprocess.CompletedProcess = await loop.run_in_executor(
                None, _run_blocking
            )

            if result.returncode != 0:
                stderr_text = result.stderr.decode(errors="replace")[-2000:]
                stdout_text = result.stdout.decode(errors="replace")[-500:]
                trial_db[trial_id]["status"] = TrialStatus.ERROR
                trial_db[trial_id]["error"]  = stderr_text or stdout_text or f"exit code {result.returncode}"
                return

            trial_db[trial_id]["csv_path"] = csv_out
            trial_db[trial_id]["status"]   = TrialStatus.PENDING_REVIEW

        except Exception as exc:
            trial_db[trial_id]["status"] = TrialStatus.ERROR
            trial_db[trial_id]["error"]  = repr(exc) or type(exc).__name__
