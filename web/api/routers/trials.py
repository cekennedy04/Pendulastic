"""
routers/trials.py
=================
Trial lifecycle endpoints.

Endpoints
---------
POST   /api/trials/record           — enqueue video → pipeline
POST   /api/trials/adjust-markers   — persist HIP→KNEE→ANKLE coords
POST   /api/trials/{id}/approve     — set approval gate
GET    /api/trials/{id}/status      — poll pipeline progress
GET    /api/trials/{id}/analysis    — return PT params (approved only)
GET    /api/trials                  — list all trials in session
"""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import os

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from web.api.models.trial import (
    AdjustMarkersRequest,
    AnalysisResponse,
    ApprovalRequest,
    RecordRequest,
    TrialResponse,
    TrialStatus,
)
from web.api.services.marker_store import MarkerStore
from web.api.services.pipeline_bridge import PipelineBridge
from web.api.services.pt_score_bridge import PTScoreBridge
from web.api.store import trial_db

router       = APIRouter()
pipeline     = PipelineBridge()
pt_bridge    = PTScoreBridge()
marker_store = MarkerStore()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_trial(trial_id: str) -> dict:
    trial = trial_db.get(trial_id)
    if trial is None:
        raise HTTPException(status_code=404, detail=f"Trial '{trial_id}' not found.")
    return trial


# ── Record ────────────────────────────────────────────────────────────────────

@router.post("/record", response_model=TrialResponse)
async def start_recording(req: RecordRequest, background_tasks: BackgroundTasks):
    """
    Initiate video acquisition and enqueue HPE pipeline processing.

    When video_path is provided the pipeline runs immediately in a background
    task.  When absent (live capture mode) a placeholder trial is created and
    the client must PATCH /api/trials/{id}/video once capture completes.
    """
    trial_id = str(uuid.uuid4())[:8].upper()
    trial: dict = {
        "id":             trial_id,
        "participant_id": req.participant_id,
        "status":         TrialStatus.RECORDING,
        "leg_side":       req.leg_side,
        "hpe_model":      req.hpe_model,
        "video_path":     req.video_path,
        "csv_path":       None,
        "start_frame":    0,
        "created_at":     _now(),
        "error":          None,
    }
    trial_db[trial_id] = trial

    if req.video_path:
        background_tasks.add_task(
            pipeline.run_async,
            trial_id=trial_id,
            video_path=req.video_path,
            leg_side=req.leg_side,
            hpe_model=req.hpe_model,
        )

    return TrialResponse(**trial)


# ── Upload video ──────────────────────────────────────────────────────────────

UPLOAD_DIR = Path("uploads")

@router.post("/upload", response_model=TrialResponse)
async def upload_video(
    background_tasks: BackgroundTasks,
    participant_id: str = Form(...),
    leg_side:       str = Form(...),
    hpe_model:      str = Form("mediapipe"),
    video: UploadFile = File(...),
):
    """
    Accept a video file upload from the mobile client, save it to disk, create
    a trial record, and enqueue the HPE pipeline as a background task.
    """
    trial_id = str(uuid.uuid4())[:8].upper()
    dest_dir = UPLOAD_DIR / trial_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    ext        = Path(video.filename or "video.mp4").suffix or ".mp4"
    video_path = str(dest_dir / f"video{ext}")

    with open(video_path, "wb") as fh:
        shutil.copyfileobj(video.file, fh)

    trial: dict = {
        "id":             trial_id,
        "participant_id": participant_id,
        "status":         TrialStatus.RECORDING,
        "leg_side":       leg_side,
        "hpe_model":      hpe_model,
        "video_path":     video_path,
        "csv_path":       None,
        "start_frame":    0,
        "created_at":     _now(),
        "error":          None,
    }
    trial_db[trial_id] = trial

    background_tasks.add_task(
        pipeline.run_async,
        trial_id=trial_id,
        video_path=video_path,
        leg_side=leg_side,
        hpe_model=hpe_model,
    )

    return TrialResponse(**trial)


# ── Adjust markers ─────────────────────────────────────────────────────────────

@router.post("/adjust-markers")
async def adjust_markers(req: AdjustMarkersRequest) -> dict:
    """
    Persist HIP → KNEE → ANKLE normalised pixel coordinates from the review
    screen to tracking_selections.json and update the trial's start frame.

    The next pipeline run for this video path will pick up the new coordinates
    via the standard _load_sel() mechanism shared across all HPE workers.
    """
    trial = _get_trial(req.trial_id)

    if not trial.get("video_path"):
        raise HTTPException(status_code=400, detail="Trial has no video path.")

    marker_store.save(
        video_path=trial["video_path"],
        markers=req.markers,
        start_frame=req.start_frame,
    )

    trial["start_frame"] = req.start_frame
    # Keep status at pending_review — client must re-run pipeline then re-approve.
    trial_db[req.trial_id] = trial

    return {"status": "ok", "start_frame": req.start_frame}


# ── Approval gate ─────────────────────────────────────────────────────────────

@router.post("/{trial_id}/approve")
async def approve_trial(trial_id: str, req: ApprovalRequest) -> dict:
    """
    Set trial approval status.  Analysis is gated behind TrialStatus.APPROVED;
    rejected trials must be re-recorded or re-adjusted before approval.
    """
    trial = _get_trial(trial_id)
    trial["status"] = TrialStatus.APPROVED if req.approved else TrialStatus.REJECTED
    trial_db[trial_id] = trial
    return {"status": "ok", "trial_status": trial["status"]}


# ── Status poll ───────────────────────────────────────────────────────────────

@router.get("/{trial_id}/status")
async def get_status(trial_id: str) -> dict:
    """Pipeline progress poll — safe to call every 2 s from the frontend."""
    trial = _get_trial(trial_id)
    return {
        "trial_id": trial_id,
        "status":   trial["status"],
        "csv_path": trial.get("csv_path"),
        "error":    trial.get("error"),
    }


# ── Analysis (approval-gated) ─────────────────────────────────────────────────

@router.get("/{trial_id}/analysis", response_model=AnalysisResponse)
async def get_analysis(trial_id: str):
    """
    Compute and return Popovic 7-parameters + data fidelity report.

    Raises 403 if the trial has not been approved; raises 409 if the pipeline
    has not yet produced an output CSV.
    """
    trial = _get_trial(trial_id)

    if trial["status"] not in (TrialStatus.APPROVED, TrialStatus.ANALYZED):
        raise HTTPException(
            status_code=403,
            detail="Trial must carry 'approved' status before analysis is accessible.",
        )

    csv_path = trial.get("csv_path")
    if not csv_path:
        raise HTTPException(
            status_code=409,
            detail="Pipeline has not produced an output CSV for this trial.",
        )

    result = pt_bridge.compute(csv_path=csv_path, trial_meta=trial)

    trial["status"] = TrialStatus.ANALYZED
    trial_db[trial_id] = trial

    return AnalysisResponse(**result)


# ── Get single trial ──────────────────────────────────────────────────────────

@router.get("/{trial_id}", response_model=TrialResponse)
async def get_trial(trial_id: str):
    """Return a single trial record."""
    return TrialResponse(**_get_trial(trial_id))


# ── Export CSV ────────────────────────────────────────────────────────────────

@router.get("/{trial_id}/export-csv")
async def export_csv(trial_id: str):
    """
    Stream the HPE output CSV for the trial as an attachment download.
    Raises 404 if the pipeline has not completed yet.
    """
    trial = _get_trial(trial_id)
    csv_path = trial.get("csv_path")
    if not csv_path or not os.path.isfile(csv_path):
        raise HTTPException(status_code=404, detail="No CSV output available for this trial.")

    with open(csv_path, encoding="utf-8") as fh:
        content = fh.read()

    filename = f"pendulastic_{trial_id}_{trial['hpe_model']}.csv"
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── List all trials ───────────────────────────────────────────────────────────

@router.get("/", response_model=list[TrialResponse])
async def list_trials(participant_id: str | None = None):
    """Return all trials, optionally filtered by participant."""
    rows = list(trial_db.values())
    if participant_id:
        rows = [r for r in rows if r["participant_id"] == participant_id]
    return [TrialResponse(**r) for r in rows]
