"""models/participant.py — Pydantic schema for participant clinical records."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class ParticipantCreate(BaseModel):
    id:              Optional[str] = None   # auto-generated if absent
    first_name:      str
    last_name:       str
    date_of_birth:   Optional[str] = None
    sex:             Optional[str] = None
    diagnosis:       Optional[str] = None
    icd10:           Optional[str] = None
    clinician:       Optional[str] = None
    thigh_length_cm: Optional[float] = None
    shank_length_cm: Optional[float] = None
    mass_kg:         Optional[float] = None
    height_cm:       Optional[float] = None
    affected_side:   Literal["left", "right", "bilateral"] = "right"
    notes:           Optional[str] = None


class ParticipantResponse(ParticipantCreate):
    id: str
    created_at: str
