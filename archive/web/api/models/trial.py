"""
models/trial.py
===============
Pydantic request/response models for the trial lifecycle.

Trial status machine:
    idle → recording → pending_review → approved → analyzed
                                      ↘ rejected
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TrialStatus(str, Enum):
    IDLE           = "idle"
    RECORDING      = "recording"
    PENDING_REVIEW = "pending_review"
    APPROVED       = "approved"
    REJECTED       = "rejected"
    ANALYZED       = "analyzed"
    ERROR          = "error"


# ── Requests ──────────────────────────────────────────────────────────────────

class RecordRequest(BaseModel):
    participant_id: str
    leg_side:       str = Field(..., pattern="^(left|right)$")
    hpe_model:      str = "mediapipe"
    video_path:     Optional[str] = None   # None = live capture


class MarkerCoords(BaseModel):
    hip_x_norm:   float = Field(..., ge=0.0, le=1.0)
    hip_y_norm:   float = Field(..., ge=0.0, le=1.0)
    knee_x_norm:  float = Field(..., ge=0.0, le=1.0)
    knee_y_norm:  float = Field(..., ge=0.0, le=1.0)
    ankle_x_norm: float = Field(..., ge=0.0, le=1.0)
    ankle_y_norm: float = Field(..., ge=0.0, le=1.0)


class AdjustMarkersRequest(BaseModel):
    trial_id:    str
    markers:     MarkerCoords
    start_frame: int = Field(default=0, ge=0)


class ApprovalRequest(BaseModel):
    trial_id: Optional[str] = None   # convenience; authoritative value comes from URL path
    approved: bool


# ── Responses ─────────────────────────────────────────────────────────────────

class TrialResponse(BaseModel):
    id:             str
    participant_id: str
    status:         TrialStatus
    leg_side:       str
    hpe_model:      str
    video_path:     Optional[str]
    csv_path:       Optional[str]
    start_frame:    int
    created_at:     str
    error:          Optional[str] = None


class PTParameters(BaseModel):
    R2n:               float
    N:                 float
    phi_max_ratio:     float
    omega_max_n:       float
    omega_peak_deg_s:  float
    f:                 float
    area_ratio:        float


class FidelityReport(BaseModel):
    passed:            bool
    area_ratio_warn:   bool
    spasticity_type:   str
    A0_deg:            float
    neutral_deg:       float


class AnalysisResponse(BaseModel):
    trial_id:     str
    # str, not float, and Optional. The MAS scale is not numeric -- "1+" sits
    # between 1 and 2 and cannot be a float at all, so any trial grading 1+ used
    # to fail response validation here. None means the excursion gate refused a
    # grade; `mas_refusal_reason` then says why.
    mas_estimate: Optional[str] = None
    mas_refusal_reason: str = ""
    #: Why this grade must not be read as a clinical finding. Non-empty
    #: whenever mas_estimate is set; see pendulastic_pt_score's constant.
    mas_caveat: str = ""
    pt_score:     float
    parameters:   PTParameters
    fidelity:     FidelityReport
    time_series:  dict   # {t, angle, t_r, phi} as float lists
