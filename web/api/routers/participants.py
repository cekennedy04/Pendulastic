"""routers/participants.py — CRUD for participant clinical records."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from web.api.models.participant import ParticipantCreate, ParticipantResponse
from web.api.store import participant_db

router = APIRouter()


@router.post("/", response_model=ParticipantResponse, status_code=201)
async def create_participant(req: ParticipantCreate):
    pid = req.id or f"P{str(uuid.uuid4())[:6].upper()}"
    if pid in participant_db:
        raise HTTPException(status_code=409, detail=f"Participant '{pid}' already exists.")
    record = req.model_dump()
    record["id"]         = pid
    record["created_at"] = datetime.now(timezone.utc).isoformat()
    participant_db[pid]  = record
    return ParticipantResponse(**record)


@router.get("/{participant_id}", response_model=ParticipantResponse)
async def get_participant(participant_id: str):
    rec = participant_db.get(participant_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Participant '{participant_id}' not found.")
    return ParticipantResponse(**rec)


@router.get("/", response_model=list[ParticipantResponse])
async def list_participants():
    return [ParticipantResponse(**r) for r in participant_db.values()]


@router.patch("/{participant_id}", response_model=ParticipantResponse)
async def update_participant(participant_id: str, req: ParticipantCreate):
    rec = participant_db.get(participant_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Participant '{participant_id}' not found.")
    rec.update({k: v for k, v in req.model_dump().items() if v is not None})
    participant_db[participant_id] = rec
    return ParticipantResponse(**rec)
