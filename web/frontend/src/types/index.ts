// ── Domain types ── mirrors the Pydantic models in web/api/models/ ──────────

export type TrialStatus =
  | "idle"
  | "recording"
  | "pending_review"
  | "approved"
  | "rejected"
  | "analyzed"
  | "error";

export type LegSide = "left" | "right";
export type HpeModel = "mediapipe" | "rtmpose" | "hrnet" | "vitpose" | "all";
export type Sex = "M" | "F" | "other" | "unknown";

export interface MarkerCoords {
  hip_x_norm:   number;
  hip_y_norm:   number;
  knee_x_norm:  number;
  knee_y_norm:  number;
  ankle_x_norm: number;
  ankle_y_norm: number;
}

export interface Trial {
  id:             string;
  participant_id: string;
  status:         TrialStatus;
  leg_side:       LegSide;
  hpe_model:      HpeModel;
  video_path?:    string;
  csv_path?:      string;
  error?:         string;
  created_at:     string;
}

export interface Participant {
  id:                string;
  first_name:        string;
  last_name:         string;
  date_of_birth?:    string;
  sex?:              Sex;
  diagnosis?:        string;
  icd10?:            string;
  clinician?:        string;
  thigh_length_cm?:  number;
  shank_length_cm?:  number;
  mass_kg?:          number;
  height_cm?:        number;
  affected_side?:    LegSide;
  notes?:            string;
}

// ── PT score / analysis ──────────────────────────────────────────────────────

export interface PtParameters {
  R2n:              number;
  N:                number;
  phi_max_ratio:    number;
  omega_max_n:      number;
  omega_peak_deg_s: number;
  f:                number;
  area_ratio:       number;
}

export interface FidelityInfo {
  passed:           boolean;
  area_ratio_warn:  boolean;
  spasticity_type:  string;
  A0_deg:           number;
  neutral_deg:      number;
}

export interface TimeSeries {
  t:     number[];
  angle: number[];
  t_r:   number[];
  phi:   number[];
}

export interface AnalysisResult {
  trial_id:     string;
  mas_estimate: number;
  pt_score:     number;
  parameters:   PtParameters;
  fidelity:     FidelityInfo;
  time_series:  TimeSeries;
}

// ── API request/response shapes ──────────────────────────────────────────────

export interface RecordRequest {
  participant_id: string;
  leg_side:       LegSide;
  hpe_model?:     HpeModel;
  video_path?:    string;
}

export interface AdjustMarkersRequest {
  trial_id:    string;
  markers:     MarkerCoords;
  start_frame: number;
}

export interface ApprovalRequest {
  trial_id: string;
  approved: boolean;
}

// ── UI navigation ────────────────────────────────────────────────────────────

export type Screen =
  | "dashboard"
  | "participant"
  | "record"
  | "review"
  | "analysis";
