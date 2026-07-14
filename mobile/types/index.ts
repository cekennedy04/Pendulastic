/**
 * types/index.ts
 * ==============
 * Shared TypeScript interfaces for the Pendulastic mobile client.
 *
 * All domain types mirror the Pydantic models in web/api/models/ and the
 * WebSocket message protocol defined in web/api/routers/ws_stream.py.
 */

// ── WebSocket message protocol ────────────────────────────────────────────────

/** Sent once as the first message after the WebSocket connection opens. */
export interface WsSessionMeta {
  type:       "session_meta";
  trial_id:   string;
  model:      HpeModel;
  leg_side:   LegSide;
  /** Frame dimensions the client will send (pixels). */
  width:      number;
  height:     number;
}

/**
 * Binary frame messages are sent as raw ArrayBuffer (JPEG bytes).
 * The server correlates each buffer to the active session via the connection.
 * A lightweight 8-byte little-endian header prefixes each buffer:
 *   bytes 0-3: uint32 frame_index
 *   bytes 4-7: uint32 timestamp_ms (mod 2^32)
 * The remaining bytes are the JPEG payload.
 */
export type WsFrameMessage = ArrayBuffer;

/** Received from the server after each processed frame. */
export interface WsKeypointPayload {
  type:            "keypoints";
  frame_index:     number;
  timestamp_ms:    number;
  keypoints:       PoseKeypoints;
  knee_angle_deg:  number;
  tracking_status: TrackingStatus;
}

/** Received when the server encounters a non-fatal processing issue. */
export interface WsWarningPayload {
  type:    "warning";
  message: string;
}

/** Received when the trial pipeline signals completion. */
export interface WsCompletePayload {
  type:     "complete";
  trial_id: string;
  csv_path: string;
}

export type WsServerMessage =
  | WsKeypointPayload
  | WsWarningPayload
  | WsCompletePayload;

// ── Pose types ────────────────────────────────────────────────────────────────

export interface Keypoint {
  /** Normalised [0, 1] horizontal position within the camera frame. */
  x:          number;
  /** Normalised [0, 1] vertical position within the camera frame. */
  y:          number;
  confidence: number;
}

export interface PoseKeypoints {
  hip:   Keypoint;
  knee:  Keypoint;
  ankle: Keypoint;
}

export type TrackingStatus = "stable" | "uncertain" | "lost";

// ── Domain types (mirrors web/frontend/src/types/index.ts) ───────────────────

export type LegSide  = "left" | "right";
export type HpeModel = "mediapipe" | "rtmpose" | "hrnet" | "vitpose";

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

export type TrialStatus =
  | "idle" | "recording" | "pending_review"
  | "approved" | "rejected" | "analyzed" | "error";

export interface Participant {
  id:               string;
  first_name:       string;
  last_name:        string;
  date_of_birth?:   string;
  sex?:             "M" | "F" | "other" | "unknown";
  diagnosis?:       string;
  icd10?:           string;
  clinician?:       string;
  thigh_length_cm?: number;
  shank_length_cm?: number;
  mass_kg?:         number;
  height_cm?:       number;
  affected_side?:   LegSide;
  notes?:           string;
}
